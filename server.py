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
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
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
META_PATH = DATA_DIR / "meta.json"
for d in (UPLOAD_DIR, THUMB_DIR):
    d.mkdir(parents=True, exist_ok=True)

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
def load_meta() -> dict:
    if not META_PATH.is_file():
        return {"files": {}}
    try:
        with META_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if "files" not in data:
            data["files"] = {}
        return data
    except Exception:
        return {"files": {}}


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


def remove_meta(filename: str):
    with _meta_lock:
        data = load_meta()
        if filename in data["files"]:
            del data["files"][filename]
            save_meta(data)


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


# ---------- Listing ----------
def list_uploads():
    meta = load_meta()["files"]
    items = []
    for p in UPLOAD_DIR.iterdir():
        if not p.is_file() or p.name.startswith("."):
            continue
        m = meta.get(p.name, {})
        captured_at = m.get("captured_at")
        if not captured_at:
            captured_at = datetime.fromtimestamp(p.stat().st_mtime).isoformat()
        ext = p.suffix.lower()
        items.append({
            "name": p.name,
            "url": f"/uploads/{p.name}",
            "thumb_url": f"/thumbs/{p.stem}.jpg" if thumb_path(p.name).is_file() else f"/uploads/{p.name}",
            "size": p.stat().st_size,
            "mtime": p.stat().st_mtime,
            "captured_at": captured_at,
            "kind": "video" if ext in VIDEO_EXT else "image",
            "favorite": bool(m.get("favorite", False)),
            "caption": m.get("caption", ""),
            "tags": m.get("tags", []),
            "processing": bool(m.get("processing", False)),
        })
    items.sort(key=lambda x: x["captured_at"], reverse=True)
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

        # PWA: service worker must be served from the site root so its scope
        # covers /api, /uploads etc. Manifest can live anywhere.
        if path == "/sw.js":
            return self._send_file(STATIC_DIR / "sw.js", cache=False)
        if path == "/manifest.json":
            return self._send_file(STATIC_DIR / "manifest.json", cache=False)

        if path == "/api/list":
            return self._send_json(200, {"items": list_uploads()})

        for prefix, base, cache in (
            ("/static/", STATIC_DIR, False),   # no cache so CSS/JS edits show up immediately
            ("/uploads/", UPLOAD_DIR, True),
            ("/thumbs/", THUMB_DIR, True),
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

        self.send_error(404, "Not Found")

    def do_PUT(self):
        parsed = urlparse(self.path)
        if unquote(parsed.path) != "/api/meta":
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
        if unquote(parsed.path) != "/api/delete":
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
        target = UPLOAD_DIR / name
        if not target.is_file():
            return False
        # drop hash from dedup index before removing meta
        rec = load_meta()["files"].get(name, {})
        _hash_unregister(rec.get("hash", ""))
        target.unlink()
        tp = thumb_path(name)
        if tp.is_file():
            tp.unlink()
        remove_meta(name)
        return True

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

        updated, missing = [], []
        for name in files:
            name = str(name)
            if not self._validate_filename(name):
                missing.append(name)
                continue
            with _meta_lock:
                data = load_meta()
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
                save_meta(data)
            updated.append(name)
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
            if not self._validate_filename(name):
                missing.append(name)
                continue
            if self._delete_one(name):
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

    # Pass 2 — thumbnails + meta defaults + hashes for everything currently present.
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
        if not rec.get("hash"):
            try:
                rec["hash"] = _hash_bytes(p.read_bytes())
                changed = True
            except Exception as e:
                sys.stderr.write(f"[backfill-hash] {p.name}: {e}\n")
        if rec.get("processing"):
            # Resume any half-finished video job from before a restart.
            _jobs.put(p.name)

    if changed:
        save_meta(data)


def main():
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    backfill_existing()
    _rebuild_hash_index()
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
