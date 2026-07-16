#!/usr/bin/env python3
"""Cat gallery server.

Endpoints:
  GET  /                       -> index.html
  GET  /static/*               -> static assets
  GET  /uploads/*              -> uploaded originals
  GET  /thumbs/*               -> generated thumbnails
  GET  /api/list               -> JSON of all items + metadata
  POST /api/upload             -> multipart upload
  PUT  /api/meta?file=NAME     -> JSON body to patch metadata
  DELETE /api/delete?file=NAME -> remove a single file
  POST /api/batch-delete       -> JSON body {"files":[...]}
"""
import glob
import hashlib
import json
import mimetypes
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs

# Use bundled .venv for pillow-heif (iPhone HEIC support) if present.
_venv_site = next(iter(glob.glob(
    str(Path(__file__).parent / ".venv" / "lib" / "python*" / "site-packages"))), None)
if _venv_site and _venv_site not in sys.path:
    sys.path.insert(0, _venv_site)

from PIL import Image, ExifTags, ImageOps
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIF_OK = True
except ImportError:
    HEIF_OK = False
    sys.stderr.write("[heif] pillow-heif not installed — HEIC uploads will fail\n")

ROOT = Path(__file__).parent.resolve()
STATIC_DIR = ROOT / "static"
# DATA_DIR lets Docker/systemd point persistent storage at a volume.
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT))).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
THUMB_DIR = DATA_DIR / "thumbs"
TRASH_UPLOAD_DIR = DATA_DIR / "trash" / "uploads"
TRASH_THUMB_DIR = DATA_DIR / "trash" / "thumbs"
META_PATH = DATA_DIR / "meta.json"
for d in (UPLOAD_DIR, THUMB_DIR, TRASH_UPLOAD_DIR, TRASH_THUMB_DIR):
    d.mkdir(parents=True, exist_ok=True)

TRASH_TTL_DAYS = 30

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".heif"}
VIDEO_EXT = {".mp4", ".webm", ".mov", ".m4v", ".ogg", ".ogv"}
ALLOWED_EXT = IMAGE_EXT | VIDEO_EXT
MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB per request
THUMB_MAX = 600

_meta_lock = threading.Lock()

# Hash → filename index so we can detect re-uploads of the same bytes.
# Built once from meta.json at startup, updated on upload/delete.
_hash_index: dict = {}
_hash_lock = threading.Lock()


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:16]


# Background work queue — video transcode + thumb run here so the upload
# response returns quickly even for large iPhone HEVC clips.
_jobs: "queue.Queue[str]" = queue.Queue()


# ---------- Metadata store ----------
def _empty_meta():
    return {"files": {}, "notes": {}, "trash": {}}


def load_meta() -> dict:
    if not META_PATH.is_file():
        return _empty_meta()
    try:
        with META_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if "files" not in data:
            data["files"] = {}
        if "notes" not in data:
            data["notes"] = {}
        if "trash" not in data:
            data["trash"] = {}
        return data
    except Exception:
        return _empty_meta()


def save_meta(data: dict):
    tmp = META_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(META_PATH)


def update_meta(filename: str, patch: dict):
    with _meta_lock:
        data = load_meta()
        cur = data["files"].get(filename, {})
        cur.update(patch)
        data["files"][filename] = cur
        save_meta(data)
        return cur


def batch_update_meta(updates: dict):
    """Apply many {filename: patch} pairs in a single meta.json rewrite."""
    if not updates:
        return
    with _meta_lock:
        data = load_meta()
        for name, patch in updates.items():
            cur = data["files"].get(name, {})
            cur.update(patch)
            data["files"][name] = cur
        save_meta(data)


def remove_meta(filename: str):
    with _meta_lock:
        data = load_meta()
        if filename in data["files"]:
            del data["files"][filename]
            save_meta(data)


# ---------- Notes (text-only timeline entries, no file on disk) ----------
def add_note(text: str, captured_at: str = "", tags=None, favorite: bool = False):
    nid = f"n-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    rec = {
        "text": str(text)[:2000],
        "captured_at": captured_at or datetime.now().isoformat(timespec="seconds"),
        "tags": [str(t)[:30] for t in (tags or [])][:20],
        "favorite": bool(favorite),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    with _meta_lock:
        data = load_meta()
        data["notes"][nid] = rec
        save_meta(data)
    return nid, rec


def update_note(nid: str, patch: dict):
    with _meta_lock:
        data = load_meta()
        rec = data["notes"].get(nid)
        if rec is None:
            return None
        if "text" in patch:
            rec["text"] = str(patch["text"])[:2000]
        if "captured_at" in patch:
            try:
                datetime.fromisoformat(str(patch["captured_at"]))
                rec["captured_at"] = str(patch["captured_at"])
            except ValueError:
                pass
        if "tags" in patch and isinstance(patch["tags"], list):
            rec["tags"] = [str(t)[:30] for t in patch["tags"]][:20]
        if "favorite" in patch:
            rec["favorite"] = bool(patch["favorite"])
        data["notes"][nid] = rec
        save_meta(data)
        return rec


def delete_note(nid: str) -> bool:
    """Hard-delete a note (legacy / internal). For user-facing delete, use trash_note."""
    with _meta_lock:
        data = load_meta()
        if nid in data["notes"]:
            del data["notes"][nid]
            save_meta(data)
            return True
        return False


# ---------- Trash (soft delete with 30-day retention) ----------
def trash_file(name: str) -> bool:
    """Move a photo/video from uploads to trash. Stash its meta + move thumb."""
    src = UPLOAD_DIR / name
    if not src.is_file():
        return False
    dst = TRASH_UPLOAD_DIR / name
    if dst.exists():
        dst.unlink()
    src.rename(dst)
    thumb_src = thumb_path(name)
    thumb_dst = TRASH_THUMB_DIR / (Path(name).stem + ".jpg")
    if thumb_src.is_file():
        if thumb_dst.exists():
            thumb_dst.unlink()
        thumb_src.rename(thumb_dst)
    with _meta_lock:
        data = load_meta()
        rec = data["files"].pop(name, {})
        data.setdefault("trash", {})[name] = {
            "kind": "file",
            "deleted_at": datetime.now().isoformat(timespec="seconds"),
            "original": rec,
        }
        save_meta(data)
    # Free the hash so re-uploading the same content doesn't get bounced as duplicate
    _hash_unregister(rec.get("hash", ""))
    return True


def trash_note(nid: str) -> bool:
    with _meta_lock:
        data = load_meta()
        if nid not in data["notes"]:
            return False
        rec = data["notes"].pop(nid)
        data.setdefault("trash", {})[nid] = {
            "kind": "note",
            "deleted_at": datetime.now().isoformat(timespec="seconds"),
            "original": rec,
        }
        save_meta(data)
    return True


def restore_from_trash(name: str) -> bool:
    with _meta_lock:
        data = load_meta()
        trash = data.get("trash", {})
        entry = trash.pop(name, None)
        if entry is None:
            return False
        if entry["kind"] == "file":
            src = TRASH_UPLOAD_DIR / name
            if not src.is_file():
                return False
            dst = UPLOAD_DIR / name
            src.rename(dst)
            thumb_src = TRASH_THUMB_DIR / (Path(name).stem + ".jpg")
            thumb_dst = thumb_path(name)
            if thumb_src.is_file():
                thumb_src.rename(thumb_dst)
            data["files"][name] = entry["original"]
            h = entry["original"].get("hash", "")
            if h:
                # Re-register under _hash_lock (lock order is always meta -> hash,
                # so calling this while holding _meta_lock is deadlock-free).
                _hash_register(h, name)
        else:
            data["notes"][name] = entry["original"]
        save_meta(data)
    return True


def permanently_delete(name: str) -> bool:
    """Remove from trash forever — unlinks file + thumb, drops trash entry."""
    with _meta_lock:
        data = load_meta()
        entry = data.get("trash", {}).pop(name, None)
        save_meta(data)
    if entry is None:
        return False
    if entry["kind"] == "file":
        f = TRASH_UPLOAD_DIR / name
        if f.is_file():
            try: f.unlink()
            except Exception: pass
        t = TRASH_THUMB_DIR / (Path(name).stem + ".jpg")
        if t.is_file():
            try: t.unlink()
            except Exception: pass
    return True


def purge_old_trash():
    """Run at startup: hard-delete trash entries older than TRASH_TTL_DAYS."""
    cutoff = datetime.now() - timedelta(days=TRASH_TTL_DAYS)
    data = load_meta()
    trash = data.get("trash", {})
    to_purge = []
    for name, entry in trash.items():
        try:
            deleted_at = datetime.fromisoformat(entry.get("deleted_at", ""))
            if deleted_at < cutoff:
                to_purge.append(name)
        except ValueError:
            continue
    for name in to_purge:
        permanently_delete(name)
    if to_purge:
        sys.stderr.write(f"[trash] purged {len(to_purge)} item(s) older than {TRASH_TTL_DAYS} days\n")


_NOTE_ID_RE = re.compile(r"^n-\d{8}-\d{6}-[0-9a-f]{6}$")


def _is_note_id(s: str) -> bool:
    return bool(_NOTE_ID_RE.match(s or ""))


# ---------- Helpers ----------
def safe_name(name: str) -> str:
    name = os.path.basename(name)
    name = re.sub(r"[^\w.\-]", "_", name, flags=re.UNICODE)
    return name[:80] or "file"


def kind_of(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in VIDEO_EXT:
        return "video"
    return "image"


def parse_multipart(body: bytes, boundary: bytes):
    """Returns list of (field_name, filename, content_type, data)."""
    sep = b"--" + boundary
    parts = body.split(sep)
    out = []
    for part in parts:
        if not part or part in (b"--\r\n", b"--", b"\r\n"):
            continue
        if part.startswith(b"\r\n"):
            part = part[2:]
        if part.endswith(b"\r\n"):
            part = part[:-2]
        if b"\r\n\r\n" not in part:
            continue
        head_raw, content = part.split(b"\r\n\r\n", 1)
        if content.endswith(b"\r\n"):
            content = content[:-2]
        headers = {}
        for line in head_raw.decode("utf-8", "replace").split("\r\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        cd = headers.get("content-disposition", "")
        field = None
        filename = None
        for tok in cd.split(";"):
            tok = tok.strip()
            m = re.match(r'name="([^"]*)"', tok)
            if m:
                field = m.group(1)
            m = re.match(r'filename="([^"]*)"', tok)
            if m:
                filename = m.group(1)
        out.append((field, filename, headers.get("content-type", "application/octet-stream"), content))
    return out


# ---------- EXIF / ffprobe ----------
_EXIF_TAGS = {v: k for k, v in ExifTags.TAGS.items()}


_EXIF_IFD_TAG = 0x8769  # Exif sub-IFD


def _parse_exif_datetime(raw):
    if not raw:
        return None
    s = str(raw).strip().rstrip("\x00").strip()
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y:%m:%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).isoformat()
        except ValueError:
            continue
    return None


def read_image_capture_time(path: Path):
    """DateTimeOriginal (36867) and DateTimeDigitized (36868) live in the Exif sub-IFD;
    DateTime (306) lives in the top-level IFD. Check sub-IFD first, then top."""
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            if not exif:
                return None
            try:
                sub = exif.get_ifd(_EXIF_IFD_TAG)
            except Exception:
                sub = {}
            for tag_id in (36867, 36868):  # DateTimeOriginal, DateTimeDigitized
                got = _parse_exif_datetime(sub.get(tag_id))
                if got:
                    return got
            got = _parse_exif_datetime(exif.get(306))  # DateTime
            if got:
                return got
    except Exception as e:
        sys.stderr.write(f"[exif] {path.name}: {e}\n")
    return None


def read_video_capture_time(path: Path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format_tags=creation_time", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        raw = out.stdout.strip()
        if raw:
            # iso-8601, e.g. "2026-03-12T14:23:01.000000Z"
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).isoformat()
            except ValueError:
                pass
    except Exception as e:
        sys.stderr.write(f"[ffprobe] {path.name}: {e}\n")
    return None


# ---------- iPhone format conversion ----------
HEIC_EXT = {".heic", ".heif"}


def heic_to_jpeg(src: Path) -> Path:
    """Convert HEIC/HEIF to JPEG in place. Returns the new .jpg path.
    Capture time is read separately into meta.json before this call, so we
    don't need to round-trip EXIF orientation back into the JPEG.
    """
    dst = src.with_suffix(".jpg")
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode != "RGB":
            im = im.convert("RGB")
        im.save(dst, "JPEG", quality=92, optimize=True)
    if dst != src and src.is_file():
        src.unlink()
    return dst


def video_codec(path: Path) -> str:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip().lower()
    except Exception as e:
        sys.stderr.write(f"[codec] {path.name}: {e}\n")
        return ""


def normalize_video(src: Path) -> Path:
    """Make sure the file is .mp4 with H.264 so every browser plays it.
    Returns the (possibly new) path. If transcoding fails the original is kept.
    """
    ext = src.suffix.lower()
    codec = video_codec(src)

    # already optimal
    if ext == ".mp4" and codec == "h264":
        return src

    tmp = src.with_name(src.stem + ".__convert.mp4")
    if codec == "h264":
        # just repack into .mp4 — fast, lossless
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
               "-c", "copy", "-movflags", "+faststart", str(tmp)]
    else:
        # HEVC / other → re-encode to H.264 + AAC
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
               "-c:a", "aac", "-b:a", "128k",
               "-pix_fmt", "yuv420p",
               "-movflags", "+faststart",
               str(tmp)]
    try:
        subprocess.run(cmd, check=True, timeout=900, capture_output=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        if tmp.is_file():
            tmp.unlink()
        msg = e.stderr.decode("utf-8", "replace")[:200] if getattr(e, "stderr", None) else str(e)
        sys.stderr.write(f"[transcode] {src.name}: {msg}\n")
        return src

    dst = src.with_suffix(".mp4")
    if dst.exists() and dst != src:
        dst.unlink()
    src.unlink()
    tmp.rename(dst)
    return dst


# ---------- Hash index + background worker ----------
def _hash_register(h: str, name: str):
    if not h:
        return
    with _hash_lock:
        _hash_index[h] = name


def _hash_unregister(h: str):
    if not h:
        return
    with _hash_lock:
        _hash_index.pop(h, None)


def _hash_rename(h: str, new_name: str):
    if not h:
        return
    with _hash_lock:
        _hash_index[h] = new_name


def _hash_lookup(h: str):
    with _hash_lock:
        return _hash_index.get(h)


def _rebuild_hash_index():
    data = load_meta()
    with _hash_lock:
        _hash_index.clear()
        for name, rec in data["files"].items():
            h = rec.get("hash")
            if h:
                _hash_index[h] = name


def _process_video_job(filename: str):
    """Run in worker thread: transcode if needed, generate thumb, clear `processing`."""
    src = UPLOAD_DIR / filename
    if not src.is_file():
        return
    try:
        new_path = normalize_video(src)
        new_name = new_path.name
        if new_name != filename:
            # carry metadata + hash index to new name
            with _meta_lock:
                data = load_meta()
                rec = data["files"].pop(filename, {})
                rec["processing"] = False
                data["files"][new_name] = rec
                save_meta(data)
            old_thumb = THUMB_DIR / (Path(filename).stem + ".jpg")
            new_thumb = THUMB_DIR / (new_path.stem + ".jpg")
            if old_thumb.is_file() and old_thumb != new_thumb:
                old_thumb.unlink()
            h = rec.get("hash")
            if h:
                _hash_rename(h, new_name)
        else:
            update_meta(new_name, {"processing": False})
        ensure_thumb(new_name)
    except Exception as e:
        sys.stderr.write(f"[worker] {filename}: {e}\n")
        update_meta(filename, {"processing": False, "error": str(e)[:200]})


def _worker_loop():
    while True:
        name = _jobs.get()
        try:
            _process_video_job(name)
        finally:
            _jobs.task_done()


# ---------- Thumbnails ----------
def make_image_thumb(src: Path, dst: Path) -> bool:
    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((THUMB_MAX, THUMB_MAX), Image.LANCZOS)
            if im.mode in ("RGBA", "P"):
                im = im.convert("RGB")
            im.save(dst, "JPEG", quality=82, optimize=True)
        return True
    except Exception as e:
        sys.stderr.write(f"[thumb-img] {src.name}: {e}\n")
        return False


def make_video_thumb(src: Path, dst: Path) -> bool:
    try:
        # grab a frame around t=1s
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", "1", "-i", str(src),
             "-vframes", "1", "-vf", f"scale='min({THUMB_MAX},iw)':-1",
             "-q:v", "4", str(dst)],
            check=True, timeout=30,
        )
        return dst.is_file()
    except Exception as e:
        sys.stderr.write(f"[thumb-vid] {src.name}: {e}\n")
        return False


def thumb_path(filename: str) -> Path:
    return THUMB_DIR / (Path(filename).stem + ".jpg")


def ensure_thumb(filename: str) -> bool:
    tp = thumb_path(filename)
    if tp.is_file():
        return True
    src = UPLOAD_DIR / filename
    if not src.is_file():
        return False
    if kind_of(filename) == "video":
        return make_video_thumb(src, tp)
    return make_image_thumb(src, tp)


# ---------- Stats (for /disk page) ----------
def gather_stats() -> dict:
    """Snapshot of file counts + disk usage. Cheap enough to run per-request."""
    images = videos = 0
    upload_bytes = 0
    for p in UPLOAD_DIR.iterdir():
        if not p.is_file() or p.name.startswith("."):
            continue
        sz = p.stat().st_size
        upload_bytes += sz
        if p.suffix.lower() in VIDEO_EXT:
            videos += 1
        else:
            images += 1
    thumb_bytes = sum(p.stat().st_size for p in THUMB_DIR.iterdir() if p.is_file())
    meta_bytes = META_PATH.stat().st_size if META_PATH.is_file() else 0
    total = images + videos
    usage = shutil.disk_usage(DATA_DIR)
    avg = upload_bytes // total if total else 0
    return {
        "files": {"total": total, "image": images, "video": videos},
        "bytes": {
            "uploads": upload_bytes,
            "thumbs": thumb_bytes,
            "meta": meta_bytes,
            "total": upload_bytes + thumb_bytes + meta_bytes,
        },
        "avg_upload_bytes": avg,
        "host_disk": {
            "total": usage.total,
            "used":  usage.used,
            "free":  usage.free,
        },
        "data_dir": str(DATA_DIR),
    }


# ---------- Listing ----------
def list_uploads():
    full_meta = load_meta()
    meta = full_meta["files"]
    items = []
    for p in UPLOAD_DIR.iterdir():
        if not p.is_file() or p.name.startswith("."):
            continue
        st = p.stat()  # single stat call reused below (this runs per /api/list poll)
        m = meta.get(p.name, {})
        captured_at = m.get("captured_at")
        if not captured_at:
            captured_at = datetime.fromtimestamp(st.st_mtime).isoformat()
        ext = p.suffix.lower()
        items.append({
            "name": p.name,
            "url": f"/uploads/{p.name}",
            "thumb_url": f"/thumbs/{p.stem}.jpg" if thumb_path(p.name).is_file() else f"/uploads/{p.name}",
            "size": st.st_size,
            "mtime": st.st_mtime,
            "captured_at": captured_at,
            "kind": "video" if ext in VIDEO_EXT else "image",
            "favorite": bool(m.get("favorite", False)),
            "caption": m.get("caption", ""),
            "tags": m.get("tags", []),
            "processing": bool(m.get("processing", False)),
        })

    # Notes — text-only entries with no file on disk, but they live on the
    # same timeline so the gallery groups them with photos by captured_at.
    for nid, rec in full_meta.get("notes", {}).items():
        items.append({
            "id": nid,
            "name": nid,                              # used as the unique key
            "kind": "note",
            "text": rec.get("text", ""),
            "captured_at": rec.get("captured_at"),
            "tags": rec.get("tags", []),
            "favorite": bool(rec.get("favorite", False)),
            "created_at": rec.get("created_at"),
        })

    items.sort(key=lambda x: x["captured_at"] or "", reverse=True)
    return items


# ---------- HTTP ----------
class Handler(BaseHTTPRequestHandler):
    server_version = "CatGallery/2.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    # --- response helpers ---
    def _send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_sw_js(self):
        """Serve sw.js with VERSION substituted to the newest static file's mtime.
        Without this, the browser's service worker keeps serving stale app.js /
        style.css after a code update — users have to hard-refresh to see new
        features. Auto-bumping VERSION makes the SW reinstall on every code change."""
        sw_path = STATIC_DIR / "sw.js"
        if not sw_path.is_file():
            return self.send_error(404, "Not Found")
        try:
            latest = max(f.stat().st_mtime for f in STATIC_DIR.iterdir() if f.is_file())
            version = f"cat-gallery-{int(latest)}"
        except Exception:
            version = "cat-gallery-v1"
        body = sw_path.read_text(encoding="utf-8").replace(
            "'cat-gallery-v1'", f"'{version}'"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/javascript; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_file(self, path: Path, cache=True):
        if not path.is_file():
            return self.send_error(404, "Not Found")
        ctype, _ = mimetypes.guess_type(str(path))
        ctype = ctype or "application/octet-stream"
        size = path.stat().st_size
        cache_hdr = "public, max-age=3600" if cache else "no-store"

        # HTTP Range — required so <video> can seek
        rng_hdr = self.headers.get("Range", "")
        m = re.match(r"bytes=(\d*)-(\d*)\s*$", rng_hdr)
        if m:
            s_raw, e_raw = m.group(1), m.group(2)
            if s_raw == "" and e_raw == "":
                start, end = 0, size - 1
            elif s_raw == "":
                n = int(e_raw)
                start = max(0, size - n)
                end = size - 1
            elif e_raw == "":
                start = int(s_raw)
                end = size - 1
            else:
                start = int(s_raw)
                end = min(int(e_raw), size - 1)
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            length = end - start + 1
            self.send_response(206)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", cache_hdr)
            self.end_headers()
            try:
                with path.open("rb") as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(64 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", cache_hdr)
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        try:
            with path.open("rb") as f:
                while chunk := f.read(64 * 1024):
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return b""
        if length > MAX_UPLOAD_BYTES:
            return None
        body = bytearray()
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(64 * 1024, remaining))
            if not chunk:
                break
            body.extend(chunk)
            remaining -= len(chunk)
        return bytes(body)

    # --- routing ---
    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path in ("/", "/index.html"):
            return self._send_file(STATIC_DIR / "index.html", cache=False)

        if path == "/disk":
            return self._send_file(STATIC_DIR / "disk.html", cache=False)
        if path == "/trash":
            return self._send_file(STATIC_DIR / "trash.html", cache=False)

        # PWA: service worker must be served from the site root so its scope
        # covers /api, /uploads etc. Manifest can live anywhere.
        if path == "/sw.js":
            return self._send_sw_js()
        if path == "/manifest.json":
            return self._send_file(STATIC_DIR / "manifest.json", cache=False)

        if path == "/api/list":
            return self._send_json(200, {"items": list_uploads()})
        if path == "/api/stats":
            return self._send_json(200, gather_stats())
        if path == "/api/trash":
            return self._handle_get_trash()

        for prefix, base, cache in (
            ("/static/", STATIC_DIR, False),   # no cache so CSS/JS edits show up immediately
            ("/uploads/", UPLOAD_DIR, True),
            ("/thumbs/", THUMB_DIR, True),
            ("/trash-files/", TRASH_UPLOAD_DIR, False),
            ("/trash-thumbs/", TRASH_THUMB_DIR, False),
        ):
            if path.startswith(prefix):
                rel = path[len(prefix):]
                target = (base / rel).resolve()
                if base not in target.parents and target != base:
                    return self.send_error(403, "Forbidden")
                return self._send_file(target, cache=cache)

        self.send_error(404, "Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/api/upload":
            return self._handle_upload()
        if path == "/api/batch-delete":
            return self._handle_batch_delete()
        if path == "/api/batch-meta":
            return self._handle_batch_meta()
        if path == "/api/note":
            return self._handle_note_create()
        if path == "/api/restore":
            return self._handle_restore()

        self.send_error(404, "Not Found")

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/api/note":
            return self._handle_note_update()
        if path != "/api/meta":
            return self.send_error(404, "Not Found")
        params = parse_qs(parsed.query)
        name = (params.get("file") or [""])[0]
        if not self._validate_filename(name):
            return self._send_json(400, {"error": "invalid filename"})

        body = self._read_body()
        if body is None:
            return self._send_json(413, {"error": "body too large"})
        try:
            patch = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return self._send_json(400, {"error": "bad json"})

        allowed = {}
        if "favorite" in patch:
            allowed["favorite"] = bool(patch["favorite"])
        if "caption" in patch:
            allowed["caption"] = str(patch["caption"])[:500]
        if "tags" in patch:
            tags = patch["tags"]
            if not isinstance(tags, list):
                return self._send_json(400, {"error": "tags must be a list"})
            allowed["tags"] = [str(t)[:30] for t in tags][:20]
        if "captured_at" in patch:
            # let user override capture time
            try:
                datetime.fromisoformat(str(patch["captured_at"]))
                allowed["captured_at"] = str(patch["captured_at"])
            except ValueError:
                return self._send_json(400, {"error": "bad captured_at"})

        merged = update_meta(name, allowed)
        return self._send_json(200, {"name": name, "meta": merged})

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/api/note":
            return self._handle_note_delete()
        if path == "/api/trash":
            return self._handle_trash_delete()
        if path != "/api/delete":
            return self.send_error(404, "Not Found")
        params = parse_qs(parsed.query)
        name = (params.get("file") or [""])[0]
        if not self._validate_filename(name):
            return self._send_json(400, {"error": "invalid filename"})
        deleted = self._delete_one(name)
        if not deleted:
            return self._send_json(404, {"error": "not found"})
        return self._send_json(200, {"deleted": name})

    # --- handlers ---
    def _validate_filename(self, name: str) -> bool:
        if not name or "/" in name or "\\" in name or name.startswith(".") or "\x00" in name:
            return False
        target = (UPLOAD_DIR / name).resolve()
        return UPLOAD_DIR in target.parents

    def _delete_one(self, name: str) -> bool:
        """Soft-delete: move file + thumb to trash, stash meta. 30-day retention.
        Permanent deletion happens later via /api/trash (or the 30-day purge)."""
        return trash_file(name)

    def _handle_upload(self):
        ctype = self.headers.get("Content-Type", "")
        m = re.search(r"boundary=([^\s;]+)", ctype)
        if not m:
            return self._send_json(400, {"error": "missing multipart boundary"})
        boundary = m.group(1).strip('"').encode()
        body = self._read_body()
        if body is None:
            return self._send_json(413, {"error": "upload too large"})
        if not body:
            return self._send_json(400, {"error": "empty body"})

        saved, errors = [], []
        for _field, filename, _ct, data in parse_multipart(body, boundary):
            if not filename:
                continue
            ext = Path(filename).suffix.lower()
            if ext not in ALLOWED_EXT:
                errors.append(f"{filename}: 不支援的檔案類型 {ext}")
                continue

            # Dedup: hash raw upload bytes; if we've seen these bytes, return the existing file.
            h = _hash_bytes(data)
            existing_name = _hash_lookup(h)
            if existing_name and (UPLOAD_DIR / existing_name).is_file():
                saved.append({
                    "name": existing_name,
                    "url": f"/uploads/{existing_name}",
                    "thumb_url": f"/thumbs/{Path(existing_name).stem}.jpg",
                    "captured_at": load_meta()["files"].get(existing_name, {}).get("captured_at"),
                    "size": len(data),
                    "duplicate": True,
                })
                continue

            base = safe_name(Path(filename).stem)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            unique = uuid.uuid4().hex[:6]
            out_name = f"{stamp}-{unique}-{base}{ext}"
            out_path = UPLOAD_DIR / out_name
            with out_path.open("wb") as f:
                f.write(data)

            # Read capture time before any conversion (EXIF/metadata still intact).
            if ext in VIDEO_EXT:
                captured = read_video_capture_time(out_path)
            else:
                captured = read_image_capture_time(out_path)
            if not captured:
                captured = datetime.now().isoformat(timespec="seconds")

            is_video = ext in VIDEO_EXT
            processing = False

            if is_video:
                # Defer transcode + thumb to background worker so upload returns fast.
                update_meta(out_name, {
                    "captured_at": captured,
                    "favorite": False,
                    "caption": "",
                    "tags": [],
                    "uploaded_at": datetime.now().isoformat(timespec="seconds"),
                    "hash": h,
                    "processing": True,
                })
                _hash_register(h, out_name)
                _jobs.put(out_name)
                processing = True
            else:
                # Images: synchronous HEIC conversion + thumb (both fast).
                try:
                    if ext in HEIC_EXT and HEIF_OK:
                        out_path = heic_to_jpeg(out_path)
                except Exception as e:
                    sys.stderr.write(f"[convert] {out_name}: {e}\n")
                out_name = out_path.name
                update_meta(out_name, {
                    "captured_at": captured,
                    "favorite": False,
                    "caption": "",
                    "tags": [],
                    "uploaded_at": datetime.now().isoformat(timespec="seconds"),
                    "hash": h,
                })
                _hash_register(h, out_name)
                ensure_thumb(out_name)

            saved.append({
                "name": out_name,
                "url": f"/uploads/{out_name}",
                "thumb_url": f"/thumbs/{Path(out_name).stem}.jpg",
                "captured_at": captured,
                "size": out_path.stat().st_size,
                "processing": processing,
            })

        return self._send_json(200, {"saved": saved, "errors": errors})

    def _handle_get_trash(self):
        data = load_meta()
        trash = data.get("trash", {})
        items = []
        now = datetime.now()
        for name, entry in trash.items():
            try:
                deleted_at = datetime.fromisoformat(entry.get("deleted_at", ""))
                days_left = max(0, TRASH_TTL_DAYS - (now - deleted_at).days)
            except ValueError:
                days_left = TRASH_TTL_DAYS
            item = {
                "name": name,
                "kind": entry["kind"],
                "deleted_at": entry.get("deleted_at"),
                "days_left": days_left,
            }
            orig = entry.get("original", {})
            if entry["kind"] == "file":
                item["url"] = f"/trash-files/{name}"
                item["thumb_url"] = f"/trash-thumbs/{Path(name).stem}.jpg"
                item["caption"] = orig.get("caption", "")
                item["tags"] = orig.get("tags", [])
                item["captured_at"] = orig.get("captured_at")
                item["kind_inner"] = "video" if Path(name).suffix.lower() in VIDEO_EXT else "image"
            else:
                item["text"] = orig.get("text", "")
                item["tags"] = orig.get("tags", [])
                item["captured_at"] = orig.get("captured_at")
            items.append(item)
        items.sort(key=lambda x: x.get("deleted_at") or "", reverse=True)
        return self._send_json(200, {"items": items, "ttl_days": TRASH_TTL_DAYS})

    def _handle_restore(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        name = (params.get("file") or [""])[0]
        if not name:
            return self._send_json(400, {"error": "missing file param"})
        if not restore_from_trash(name):
            return self._send_json(404, {"error": "not found in trash"})
        return self._send_json(200, {"restored": name})

    def _handle_trash_delete(self):
        """DELETE /api/trash?file=<name>  → permanently delete one
           DELETE /api/trash             → empty entire trash"""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        name = (params.get("file") or [""])[0]
        if name:
            if permanently_delete(name):
                return self._send_json(200, {"deleted": name})
            return self._send_json(404, {"error": "not found"})
        # Empty all
        data = load_meta()
        names = list(data.get("trash", {}).keys())
        count = 0
        for n in names:
            if permanently_delete(n):
                count += 1
        return self._send_json(200, {"deleted_count": count})

    def _handle_note_create(self):
        body = self._read_body()
        if body is None:
            return self._send_json(413, {"error": "body too large"})
        try:
            payload = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return self._send_json(400, {"error": "bad json"})
        text = str(payload.get("text", "")).strip()
        if not text:
            return self._send_json(400, {"error": "empty text"})
        captured = str(payload.get("captured_at", ""))
        if captured:
            try:
                datetime.fromisoformat(captured)
            except ValueError:
                return self._send_json(400, {"error": "bad captured_at"})
        tags = payload.get("tags") or []
        if not isinstance(tags, list):
            return self._send_json(400, {"error": "tags must be a list"})
        favorite = bool(payload.get("favorite", False))
        nid, rec = add_note(text, captured, tags, favorite)
        return self._send_json(200, {"id": nid, "note": rec})

    def _handle_note_update(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        nid = (params.get("id") or [""])[0]
        if not _is_note_id(nid):
            return self._send_json(400, {"error": "invalid id"})
        body = self._read_body()
        if body is None:
            return self._send_json(413, {"error": "body too large"})
        try:
            patch = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return self._send_json(400, {"error": "bad json"})
        rec = update_note(nid, patch)
        if rec is None:
            return self._send_json(404, {"error": "not found"})
        return self._send_json(200, {"id": nid, "note": rec})

    def _handle_note_delete(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        nid = (params.get("id") or [""])[0]
        if not _is_note_id(nid):
            return self._send_json(400, {"error": "invalid id"})
        # Soft delete — goes to trash for 30 days
        if not trash_note(nid):
            return self._send_json(404, {"error": "not found"})
        return self._send_json(200, {"deleted": nid})

    def _handle_batch_meta(self):
        """Apply a metadata patch to many files. Body: {files:[...], patch:{...}}.
        For 'tags' we support {"tags_add": [...], "tags_remove": [...]} too, so callers
        don't need to fetch-modify-PUT each item individually."""
        body = self._read_body()
        if body is None:
            return self._send_json(413, {"error": "body too large"})
        try:
            payload = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return self._send_json(400, {"error": "bad json"})
        files = payload.get("files") or []
        patch = payload.get("patch") or {}
        if not isinstance(files, list) or not isinstance(patch, dict):
            return self._send_json(400, {"error": "bad shape"})

        # additive/subtractive tag ops
        tags_add = [str(t)[:30] for t in (patch.get("tags_add") or [])][:20]
        tags_remove = set(str(t) for t in (patch.get("tags_remove") or []))

        base_patch = {}
        if "favorite" in patch:
            base_patch["favorite"] = bool(patch["favorite"])
        if "caption" in patch:
            base_patch["caption"] = str(patch["caption"])[:500]
        if "captured_at" in patch:
            try:
                datetime.fromisoformat(str(patch["captured_at"]))
                base_patch["captured_at"] = str(patch["captured_at"])
            except ValueError:
                return self._send_json(400, {"error": "bad captured_at"})

        # Batch ops support both photo files AND note IDs. Caption only makes
        # sense for files (notes have a 'text' field instead).
        updated, missing = [], []
        with _meta_lock:
            data = load_meta()
            for name in files:
                name = str(name)
                if _is_note_id(name) and name in data["notes"]:
                    rec = data["notes"][name]
                    for k, v in base_patch.items():
                        if k == "caption":
                            continue  # notes don't have caption
                        rec[k] = v
                    if tags_add or tags_remove:
                        cur = list(rec.get("tags", []))
                        if tags_remove:
                            cur = [t for t in cur if t not in tags_remove]
                        if tags_add:
                            for t in tags_add:
                                if t and t not in cur:
                                    cur.append(t)
                        rec["tags"] = cur[:20]
                    data["notes"][name] = rec
                    updated.append(name)
                    continue
                if not self._validate_filename(name):
                    missing.append(name)
                    continue
                rec = data["files"].get(name)
                if rec is None:
                    missing.append(name)
                    continue
                rec.update(base_patch)
                if tags_add or tags_remove:
                    cur = list(rec.get("tags", []))
                    if tags_remove:
                        cur = [t for t in cur if t not in tags_remove]
                    if tags_add:
                        for t in tags_add:
                            if t and t not in cur:
                                cur.append(t)
                    rec["tags"] = cur[:20]
                data["files"][name] = rec
                updated.append(name)
            save_meta(data)
        return self._send_json(200, {"updated": updated, "missing": missing})

    def _handle_batch_delete(self):
        body = self._read_body()
        if body is None:
            return self._send_json(413, {"error": "body too large"})
        try:
            data = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return self._send_json(400, {"error": "bad json"})
        files = data.get("files") or []
        if not isinstance(files, list):
            return self._send_json(400, {"error": "files must be a list"})
        deleted, missing = [], []
        for name in files:
            name = str(name)
            # Note IDs go through trash_note; filenames through trash_file.
            if _is_note_id(name):
                if trash_note(name):
                    deleted.append(name)
                else:
                    missing.append(name)
                continue
            if not self._validate_filename(name):
                missing.append(name)
                continue
            if trash_file(name):
                deleted.append(name)
            else:
                missing.append(name)
        return self._send_json(200, {"deleted": deleted, "missing": missing})


# ---------- Backfill: handle pre-existing uploads ----------
def backfill_existing():
    """Generate thumbnails / metadata for existing uploads, and convert any
    iPhone-format files (HEIC, HEVC, .mov) so every browser can display them."""
    data = load_meta()
    files = data["files"]
    changed = False

    # Pass 1 — convert iPhone formats. Read capture time first so we don't
    # lose it when the source file is replaced.
    for p in list(UPLOAD_DIR.iterdir()):
        if not p.is_file() or p.name.startswith("."):
            continue
        ext = p.suffix.lower()
        old_name = p.name
        needs_convert = (
            (ext in HEIC_EXT and HEIF_OK) or
            (ext in VIDEO_EXT and not (ext == ".mp4" and video_codec(p) == "h264"))
        )
        if not needs_convert:
            continue

        # Make sure we have a capture time saved before clobbering the file.
        existing = files.get(old_name, {})
        captured = existing.get("captured_at")
        if not captured:
            captured = (read_video_capture_time(p) if ext in VIDEO_EXT
                        else read_image_capture_time(p))
        try:
            new_p = heic_to_jpeg(p) if ext in HEIC_EXT else normalize_video(p)
        except Exception as e:
            sys.stderr.write(f"[backfill-convert] {old_name}: {e}\n")
            continue
        if new_p.name == old_name:
            continue

        # Move meta entry (and old thumbnail) to the new filename.
        carry = existing or {
            "favorite": False, "caption": "", "tags": [],
            "uploaded_at": datetime.fromtimestamp(new_p.stat().st_mtime).isoformat(timespec="seconds"),
        }
        if captured:
            carry["captured_at"] = captured
        files[new_p.name] = carry
        files.pop(old_name, None)
        old_thumb = THUMB_DIR / (Path(old_name).stem + ".jpg")
        new_thumb = THUMB_DIR / (new_p.stem + ".jpg")
        if old_thumb.is_file() and old_thumb != new_thumb:
            old_thumb.unlink()
        changed = True
        sys.stderr.write(f"[backfill-convert] {old_name} -> {new_p.name}\n")

    # Pass 2 — thumbnails + meta defaults for everything currently present.
    # NOTE: hash computation moved to a background thread (see _backfill_hashes_async)
    # so server startup stays fast even with thousands of files.
    for p in UPLOAD_DIR.iterdir():
        if not p.is_file() or p.name.startswith("."):
            continue
        ensure_thumb(p.name)
        rec = files.get(p.name)
        if rec is None:
            captured = (read_video_capture_time(p) if kind_of(p.name) == "video"
                        else read_image_capture_time(p))
            if not captured:
                captured = datetime.fromtimestamp(p.stat().st_mtime).isoformat()
            rec = {
                "captured_at": captured,
                "favorite": False,
                "caption": "",
                "tags": [],
                "uploaded_at": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
            }
            files[p.name] = rec
            changed = True
        if rec.get("processing"):
            # Resume any half-finished video job from before a restart.
            _jobs.put(p.name)

    if changed:
        save_meta(data)


def _backfill_hashes_async():
    """Compute SHA-1 for files missing the hash field. Runs in a daemon thread
    so server startup isn't blocked by big libraries. Updates _hash_index as
    each hash becomes available so dedup gradually starts working."""
    try:
        data = load_meta()
        todo = [name for name, rec in data["files"].items() if not rec.get("hash")]
        if not todo:
            return
        sys.stderr.write(f"[hash-backfill] computing for {len(todo)} files\n")
        updates = {}
        for name in todo:
            p = UPLOAD_DIR / name
            if not p.is_file():
                continue
            try:
                h = _hash_bytes(p.read_bytes())
                updates[name] = {"hash": h}
                _hash_register(h, name)
            except Exception as e:
                sys.stderr.write(f"[hash-backfill] {name}: {e}\n")
        batch_update_meta(updates)
        sys.stderr.write(f"[hash-backfill] done ({len(updates)} hashed)\n")
    except Exception as e:
        sys.stderr.write(f"[hash-backfill] aborted: {e}\n")


def main():
    # Startup banners below contain emoji. On consoles whose encoding can't
    # represent them (e.g. Windows cp950/cp1252, where stdout uses a strict
    # error handler) a bare print() raises UnicodeEncodeError and the server
    # never starts. Fall back to a lenient error handler so those characters
    # degrade gracefully instead of crashing boot. No-op on UTF-8 (Docker).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except Exception:
            pass

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    backfill_existing()
    purge_old_trash()
    _rebuild_hash_index()
    # Hash backfill runs in background — first request can hit server within
    # ~50ms even if there are thousands of un-hashed files to chew through.
    threading.Thread(target=_backfill_hashes_async, daemon=True, name="hash-backfill").start()
    threading.Thread(target=_worker_loop, daemon=True, name="cat-worker").start()
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"🐾 Cat gallery running at http://{host}:{port}")
    print(f"   Uploads -> {UPLOAD_DIR}")
    print(f"   Thumbs  -> {THUMB_DIR}")
    print(f"   Meta    -> {META_PATH}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye~")
        srv.server_close()


if __name__ == "__main__":
    main()
