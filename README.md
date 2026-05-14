# 🐾 Meow Gallery

A scrapbook-style photo & video album for the cats in your life — pure-Python HTTP server, vanilla-JS frontend, automatic iPhone format conversion built in.

> Personal / home-scale tool. **No authentication** — designed to live on `localhost` or a private LAN (Tailscale / VPN). If you expose it publicly, put it behind a reverse proxy with auth.

---

## Features

- 🖼️ **Scrapbook layout** — polaroid cards, washi-tape decorations, gentle tilts, grouped by month
- 📱 **iPhone-friendly ingest** — HEIC → JPEG, HEVC `.mov` → H.264 MP4 (video transcode runs in a background worker, upload returns instantly)
- 📐 **Full responsive design** — phone / iPad / landscape, touch gestures, installable as a PWA on iOS & Android
- ⏩ **Video seek works** — HTTP `Range` support
- 🔁 **Content-hash dedup** — SHA-1 of upload bytes; re-uploading the same file collapses to the existing record
- ✏️ **Editable metadata** — caption, tags, capture time, favorite — all from inside the lightbox
- ☑️ **Batch ops** — multi-select to delete / add tag / toggle favorite
- 🔗 **URL state** — filter / search / open photo are reflected in the hash, so refresh + back + share-link all work

---

## Quick start (Docker, recommended)

```bash
git clone https://github.com/Benedict-CS/meow.git
cd meow
docker compose up -d --build
# → http://localhost:8000
```

Data is persisted to `./data/` on the host (`uploads/`, `thumbs/`, `meta.json`).

The container runs as UID `1000:1000` so files on the host stay owned by you. If your UID isn't 1000:

```bash
CAT_UID=$(id -u) CAT_GID=$(id -g) docker compose up -d --build
```

---

## Bare-Python install (no Docker)

Requires Python 3.10+, `ffmpeg`, `Pillow`, `pillow-heif`.

```bash
sudo apt install -y ffmpeg python3-venv          # Debian / Ubuntu
python3 -m venv .venv
.venv/bin/pip install Pillow pillow-heif
.venv/bin/python server.py
# → http://127.0.0.1:8000
```

`server.py` auto-detects `./.venv/lib/.../site-packages` at startup, so `python3 server.py` works too once the venv is built.

### Environment variables

| Variable   | Default                                | Notes                                  |
| ---------- | -------------------------------------- | -------------------------------------- |
| `HOST`     | `127.0.0.1`                            | Use `0.0.0.0` to bind all interfaces   |
| `PORT`     | `8000`                                 |                                        |
| `DATA_DIR` | project root (Docker: `/data`)         | Where `uploads/ thumbs/ meta.json` go  |

---

## Keyboard & touch (Lightbox)

| Input            | Action                                |
| ---------------- | ------------------------------------- |
| `←` / `→`        | Previous / next photo                 |
| `Space`          | Toggle slideshow                      |
| `F`              | Toggle favorite                       |
| `Esc`            | Close                                 |
| Swipe left/right | Previous / next                       |
| Swipe down       | Close                                 |
| Tap date         | Edit capture time                     |
| Tap caption      | Edit caption (with explicit ✓ Save)   |

---

## Project layout

```
meow/
├── server.py              # pure-stdlib HTTP server (~900 lines)
├── static/
│   ├── index.html
│   ├── style.css
│   ├── app.js
│   ├── sw.js              # PWA service worker
│   ├── manifest.json      # PWA manifest
│   └── icon-*.png / .svg
├── Dockerfile             # python:3.12-slim + ffmpeg
├── docker-compose.yml
├── .dockerignore
├── .gitignore             # excludes data/ uploads/ thumbs/ meta.json .venv/
└── data/                  # ← gitignored; the only thing you need to back up
    ├── uploads/           # originals (already in browser-friendly formats)
    ├── thumbs/            # 600px JPEG thumbnails
    └── meta.json          # all metadata; plain JSON, human-readable
```

---

## API

| Method   | Path                  | Body / Query                                                        |
| -------- | --------------------- | ------------------------------------------------------------------- |
| `GET`    | `/api/list`           | —                                                                   |
| `POST`   | `/api/upload`         | `multipart/form-data`                                               |
| `PUT`    | `/api/meta?file=`     | JSON patch: `{caption?, tags?, favorite?, captured_at?}`            |
| `DELETE` | `/api/delete?file=`   | —                                                                   |
| `POST`   | `/api/batch-delete`   | `{ "files": [...] }`                                                |
| `POST`   | `/api/batch-meta`     | `{ "files": [...], "patch": { favorite?, caption?, tags_add?, tags_remove? } }` |
| `GET`    | `/uploads/*`          | Originals (supports `Range`)                                        |
| `GET`    | `/thumbs/*`           | Thumbnails                                                          |
| `GET`    | `/sw.js`, `/manifest.json` | PWA assets                                                     |

### Supported formats

| Kind  | Extensions                                    | After upload                |
| ----- | --------------------------------------------- | --------------------------- |
| Image | `.jpg .jpeg .png .gif .webp .bmp`             | kept as-is                  |
| Image | `.heic .heif` (iPhone)                        | converted to JPEG           |
| Video | `.mp4` (H.264)                                | kept as-is                  |
| Video | `.mp4` (HEVC), `.mov`, `.m4v`, `.webm`, `.ogv` | transcoded to H.264 MP4    |

Per-upload size limit: 500 MB.

---

## Backup & migration

`./data/` is the only irreplaceable thing in the project — **back it up and you're covered**.

```bash
# Snapshot
tar czf catdata-$(date +%F).tgz -C /path/to/meow data/

# Restore on a new host
git clone https://github.com/Benedict-CS/meow.git
cd meow
tar xzf /path/to/catdata-XXXX-XX-XX.tgz
docker compose up -d --build
```

Daily cron example:

```cron
0 3 * * * tar czf /backup/cat-$(date +\%F).tgz -C /home/ben/cat-gallery data/
```

`meta.json` is plain JSON, so you can hand-edit when needed (restore from backup if you break it).

---

## Install as a PWA

**iPhone (Safari)** — open `http://<host>:8000` → Share → Add to Home Screen. Launching from the home-screen icon opens it as a standalone, full-screen app with no URL bar.

**Android (Chrome)** — will prompt with an "Install app" banner automatically.

---

## Design notes

- **No database.** All metadata lives in one JSON file. Simple, backup-friendly, hand-editable. Comfortable up to ~10 000 photos.
- **No auth.** The app assumes a trusted network. Pair with nginx + basic-auth, oauth2-proxy, or Tailscale if you need access control.
- **No frontend framework.** Vanilla JS, no build step, no npm.
- **Background transcode via a thread + queue.** On startup any item still marked `processing: true` is re-queued, so a mid-transcode restart recovers cleanly.
- **Video transcode uses `libx264 -preset veryfast -crf 23`** — balanced for speed and quality. Bump CRF or change preset to trade size vs. CPU.

---

## License

Personal project; no license declared. Forking for personal use is fine; if you want to distribute, please get in touch first.
