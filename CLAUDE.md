# CLAUDE.md

Guidance for working in this repository. Keep it accurate — update it when the
architecture changes.

## What this is

Meow Gallery: a scrapbook-style photo/video album for a household's cats.

- **Backend:** a single pure-stdlib Python HTTP server (`server.py`). No web
  framework, no database. Metadata lives in one JSON file (`meta.json`).
- **Frontend:** vanilla JavaScript + plain CSS in `static/`. No build step, no
  npm, no bundler. Files are served as-is.
- **Media tooling:** Pillow + pillow-heif (image decode/encode, EXIF, thumbs)
  and `ffmpeg`/`ffprobe` (video transcode, capture-time, video thumbs).
- **Deploy:** Docker (`python:3.12-slim` + ffmpeg), single image, `./data`
  bind-mounted for persistence.

There is **no authentication** — it is designed for localhost / a private LAN.
Do not add features that assume the network is hostile without also adding auth;
that is an explicit non-goal (see README "Design notes").

Language note: the product UI is Traditional Chinese by design. User-facing
strings (toasts, labels, HTML copy) are Chinese on purpose. Code comments,
docstrings, README, and this file are English.

## Layout

```
server.py            # the entire backend (~1400 lines, stdlib only)
scripts/notes.py     # CLI to export/import the notes timeline across hosts
static/
  index.html         # main timeline SPA
  app.js             # ~1800 lines: gallery, lightbox, upload, notes, URL state
  style.css
  disk.html/.css/.js # /disk storage dashboard  (reads /api/stats)
  trash.html/.css/.js# /trash bin              (reads /api/trash)
  sw.js              # PWA service worker
  manifest.json, icon-*  # PWA assets
Dockerfile, docker-compose.yml, .dockerignore
data/                # gitignored runtime data (uploads, thumbs, trash, meta.json)
notes-seed.json      # committed; shareable export of text notes only
```

## Backend architecture (`server.py`)

- **HTTP layer:** `Handler(BaseHTTPRequestHandler)` on a `ThreadingHTTPServer`.
  Routing is manual in `do_GET/do_POST/do_PUT/do_DELETE`. Responses go through
  `_send_json` / `_send_file`. `_send_file` implements HTTP `Range` so `<video>`
  can seek.
- **Storage model:** `meta.json` is `{"files": {name: rec}, "notes": {id: rec},
  "trash": {name/id: entry}}`. `uploads/` holds originals, `thumbs/` holds 600px
  JPEG thumbnails, `trash/uploads` + `trash/thumbs` hold soft-deleted media.
- **Concurrency & locks:**
  - `_meta_lock` guards all read-modify-write cycles on `meta.json`
    (`update_meta`, `batch_update_meta`, trash/note ops, etc.). `load_meta` /
    `save_meta` themselves are lock-free; callers must hold `_meta_lock` around
    a load→mutate→save sequence.
  - `_hash_lock` guards `_hash_index` (SHA-1 prefix → filename, for upload
    dedup). **Lock order is always meta → hash**; never acquire `_meta_lock`
    while holding `_hash_lock`.
  - `save_meta` writes to a `.tmp` file then `os.replace` — atomic swap.
- **Background worker:** one daemon thread (`_worker_loop`) drains `_jobs`
  (a `queue.Queue`). Video uploads return immediately with `processing: true`;
  the worker transcodes (`normalize_video`) + generates the thumb, then clears
  the flag. On startup `backfill_existing` re-queues anything still marked
  `processing` so a mid-transcode restart recovers.
- **Startup sequence (`main`):** reconfigure stdout/stderr to a lenient error
  handler (so emoji banners don't crash non-UTF-8 consoles) → `backfill_existing`
  (convert HEIC/HEVC, generate missing thumbs/meta) → `purge_old_trash`
  (drop trash older than `TRASH_TTL_DAYS = 30`) → `_rebuild_hash_index` →
  spawn hash-backfill + worker daemon threads → `serve_forever`.
- **Ingest pipeline (`_handle_upload`):** parse multipart (custom
  `parse_multipart`, whole body buffered in memory, capped at
  `MAX_UPLOAD_BYTES = 500 MB`) → dedup by SHA-1 prefix → save with a unique
  `YYYYMMDD-HHMMSS-<rand>-<safe-stem><ext>` name → read capture time
  (EXIF for images, `ffprobe` for video) → images: synchronous HEIC→JPEG +
  thumb; videos: enqueue background job.
- **Security-relevant helpers:** `safe_name` sanitizes upload filenames;
  `_validate_filename` rejects names with slashes / leading-dot / NUL and
  confirms the resolved path stays under `UPLOAD_DIR`; the static/uploads/thumbs
  GET routes re-check that the resolved target is inside the served base
  (path-traversal guard). Preserve these checks when touching routing.

## Frontend architecture (`static/app.js`)

- Single `state` object; `render()` rebuilds the gallery from `state.items`
  (fetched from `/api/list`), grouped by month.
- Lightbox, upload modal, note editor, and a generic "ask" modal share a modal
  stack + focus-trap system (`registerModal`, `trapFocus`, body-scroll lock
  counter). Escape closes the topmost modal.
- URL hash mirrors filter/tag/search/open-photo (`syncStateToUrl` /
  `applyUrlState`) so refresh, back, and share-links work.
- While any item is `processing`, `ensurePolling` polls `/api/list` every 3s.
- Service worker (`sw.js`): app-shell precache, network-first for navigations
  and `/api/*`, stale-while-revalidate for `/static/*`, cache-first (capped) for
  `/thumbs/*`, passthrough for `/uploads/*`. Bump `CACHE_VERSION` in `sw.js` to
  force clients to drop old caches. (Note: the server's `_send_sw_js` string
  substitution is currently a no-op — `sw.js` self-manages its version constant.)

## HTTP API (summary)

`GET /api/list`, `GET /api/stats`, `GET /api/trash`;
`POST /api/upload` (multipart), `/api/batch-delete`, `/api/batch-meta`,
`/api/note`, `/api/restore`;
`PUT /api/meta?file=`, `/api/note?id=`;
`DELETE /api/delete?file=`, `/api/note?id=`, `/api/trash[?file=]`.
Static/media prefixes: `/static/`, `/uploads/`, `/thumbs/`, `/trash-files/`,
`/trash-thumbs/`, plus `/sw.js`, `/manifest.json`. See README for the full table.

## Running & verifying

```bash
# Docker (primary)
docker compose up -d --build           # -> http://localhost:8000

# Bare Python (Pillow + pillow-heif + ffmpeg on PATH)
python server.py                       # -> http://127.0.0.1:8000
```

Env vars: `HOST` (default `127.0.0.1`), `PORT` (`8000`), `DATA_DIR`
(default: repo root; Docker: `/data`).

There is no test suite. To smoke-test after a backend change, run the server
against a throwaway `DATA_DIR` and exercise the endpoints (upload a generated
JPEG via multipart, list, patch meta, delete→trash→restore, hit `/api/stats`
and `/sw.js`). Confirm the path-traversal guards still return 403/400 for
`../` filenames. `ffmpeg`/`pillow-heif` are optional for image-only testing;
the server logs a warning and keeps running without them.

## Conventions & gotchas

- Keep `server.py` dependency-light: stdlib + Pillow/pillow-heif only. `ffmpeg`
  is invoked as a subprocess, never imported.
- Any code path that both mutates `meta.json` and touches `_hash_index` must
  respect the meta → hash lock order.
- `_send_file` swallows `BrokenPipeError`/`ConnectionResetError` (clients
  disconnecting mid-stream is normal) — keep that.
- Frontend has no bundler: edit `static/*.js` directly. When shipping frontend
  changes to installed PWAs, bump `CACHE_VERSION` in `sw.js`.
- Never commit `data/`, `meta.json`, uploads/thumbs, or secrets (already
  gitignored). `notes-seed.json` is the one committed data artifact (text notes
  only, via `scripts/notes.py export`).
