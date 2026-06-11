# 🐾 Meow Gallery

A scrapbook-style photo & video album for the cats in your life — pure-Python HTTP server, vanilla-JS frontend, automatic iPhone format conversion built in. No database, no build step, no npm.

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Pillow](https://img.shields.io/badge/Pillow-12-9B59B6)](https://python-pillow.org/)
[![pillow-heif](https://img.shields.io/badge/HEIC-pillow--heif-FF6B6B)](https://github.com/bigcat88/pillow_heif)
[![ffmpeg](https://img.shields.io/badge/ffmpeg-HEVC%20%E2%86%92%20H.264-007808?logo=ffmpeg&logoColor=white)](https://ffmpeg.org/)
[![Vanilla JS](https://img.shields.io/badge/Vanilla_JS-no_build_step-F7DF1E?logo=javascript&logoColor=black)](https://developer.mozilla.org/docs/Web/JavaScript)
[![CSS3](https://img.shields.io/badge/CSS3-1572B6?logo=css3&logoColor=white)](https://developer.mozilla.org/docs/Web/CSS)
[![PWA](https://img.shields.io/badge/PWA-installable-5A0FC8?logo=pwa&logoColor=white)](https://web.dev/progressive-web-apps/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![Status](https://img.shields.io/badge/Status-Production%20Ready-brightgreen)](#)
[![Self-hosted](https://img.shields.io/badge/Self--hosted-LAN-orange)](#)

> [!WARNING]
> **No authentication.** Meow Gallery is designed for `localhost` or a private LAN (Tailscale / VPN). If you expose it to the open internet, put it behind a reverse proxy with auth (nginx + basic-auth, oauth2-proxy, Cloudflare Access, etc.). There is no login screen — anyone who can reach the port can view, upload, edit, and delete.

> **Project status / scope.** Personal home-scale tool, built to track one or two cats. Comfortable up to ~10,000 items on a single JSON metadata file. Not aimed at multi-user, multi-tenant, or "photo cloud" use cases.

---

## Features

- 🖼️ **Scrapbook layout** — polaroid cards, washi-tape decorations, gentle tilts, grouped by month
- 📱 **iPhone-friendly ingest** — HEIC → JPEG, HEVC `.mov` → H.264 MP4 (video transcode runs in a background worker, upload returns instantly)
- 📐 **Full responsive design** — phone / iPad / landscape, touch gestures, installable as a PWA on iOS & Android
- ⏩ **Video seek works** — HTTP `Range` support
- 🔁 **Content-hash dedup** — SHA-1 of upload bytes; re-uploading the same file collapses to the existing record
- ✏️ **Editable metadata** — caption, tags, capture time, favorite — all from inside the lightbox
- ☑️ **Batch ops** — multi-select to delete / add tag / change date / toggle favorite
- 📝 **Text notes** — record events that have no photo (naming day, vet visit, etc.) as sticky-note cards on the same timeline
- 🗑️ **30-day trash** — deletions are soft; restore from `/trash` until they age out
- 📊 **Disk dashboard** — `/disk` shows item counts, storage used, and host disk free
- 🔗 **URL state** — filter / search / open photo are reflected in the hash, so refresh + back + share-link all work

---

## Screenshots

> _(coming soon — drop in once a fresh capture set is ready)_

- **Timeline view** — the scrapbook grid grouped by month
- **Lightbox** — fullscreen photo with caption, tags, and capture-time editing
- **Notes** — sticky-card timeline entries sharing the same wall as photos

---

## Quick start (Docker, recommended)

```bash
git clone https://github.com/Benedict-CS/meow.git
cd meow
docker compose up -d --build
# → http://localhost:8000
```

Data is persisted to `./data/` on the host (`uploads/`, `thumbs/`, `trash/`, `meta.json`).

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

| Variable   | Default                          | Notes                                  |
| ---------- | -------------------------------- | -------------------------------------- |
| `HOST`     | `127.0.0.1`                      | Use `0.0.0.0` to bind all interfaces   |
| `PORT`     | `8000`                           |                                        |
| `DATA_DIR` | project root (Docker: `/data`)   | Where `uploads/ thumbs/ trash/ meta.json` go |

---

## Keyboard & touch (Lightbox)

| Input              | Action                                          |
| ------------------ | ----------------------------------------------- |
| `←` / `→`          | Previous / next photo                           |
| `Space`            | Toggle slideshow                                |
| `F`                | Toggle favorite                                 |
| `D`                | Download original file (Save to Photos on iOS)  |
| `S`                | Share deep link to this photo                   |
| `Esc`              | Close                                           |
| `Ctrl/⌘ + Enter`   | Save caption (while editing)                    |
| Swipe left / right | Previous / next                                 |
| Swipe down         | Close                                           |
| Tap date           | Edit capture time                               |
| Tap caption        | Edit caption (with explicit ✓ Save)             |

---

## Project layout

```text
meow/
├── server.py              # pure-stdlib HTTP server (~1400 lines)
├── scripts/
│   └── notes.py           # export/import the notes timeline across hosts
├── static/
│   ├── index.html
│   ├── disk.html          # /disk dashboard
│   ├── trash.html         # /trash bin (30-day retention)
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
    ├── trash/             # soft-deleted items, auto-purged after 30 days
    └── meta.json          # all metadata; plain JSON, human-readable
```

---

## API

### Pages

| Method | Path        | Notes                                |
| ------ | ----------- | ------------------------------------ |
| `GET`  | `/`         | Main timeline (index.html)           |
| `GET`  | `/disk`     | Storage / file-count dashboard       |
| `GET`  | `/trash`    | Trash bin (restore or purge)         |

### Files & metadata

| Method   | Path                      | Body / Query                                                        |
| -------- | ------------------------- | ------------------------------------------------------------------- |
| `GET`    | `/api/list`               | —                                                                   |
| `GET`    | `/api/stats`              | Counts + disk usage (powers `/disk`)                                |
| `POST`   | `/api/upload`             | `multipart/form-data`                                               |
| `PUT`    | `/api/meta?file=`         | JSON patch: `{caption?, tags?, favorite?, captured_at?}`            |
| `DELETE` | `/api/delete?file=`       | Soft delete → trash (30-day retention)                              |
| `POST`   | `/api/batch-delete`       | `{ "files": [...] }` (files or note IDs)                            |
| `POST`   | `/api/batch-meta`         | `{ "files": [...], "patch": { favorite?, caption?, captured_at?, tags_add?, tags_remove? } }` |

### Notes (text-only timeline entries)

| Method   | Path                  | Body / Query                                              |
| -------- | --------------------- | -------------------------------------------------------- |
| `POST`   | `/api/note`           | `{ text, captured_at?, tags?, favorite? }`               |
| `PUT`    | `/api/note?id=`       | JSON patch: `{text?, captured_at?, tags?, favorite?}`    |
| `DELETE` | `/api/note?id=`       | Soft delete → trash                                      |

### Trash

| Method   | Path                       | Notes                                          |
| -------- | -------------------------- | ---------------------------------------------- |
| `GET`    | `/api/trash`               | List items in trash, with `days_left`          |
| `POST`   | `/api/restore?file=`       | Restore one item back to the gallery           |
| `DELETE` | `/api/trash?file=`         | Permanently delete one item                    |
| `DELETE` | `/api/trash`               | Empty the entire trash                         |

### Static assets

| Method | Path                          | Notes                            |
| ------ | ----------------------------- | -------------------------------- |
| `GET`  | `/uploads/*`                  | Originals (supports `Range`)     |
| `GET`  | `/thumbs/*`                   | Thumbnails                       |
| `GET`  | `/trash-files/*`              | Originals currently in trash     |
| `GET`  | `/trash-thumbs/*`             | Thumbnails for trashed items     |
| `GET`  | `/sw.js`, `/manifest.json`    | PWA assets                       |

### Supported formats

| Kind  | Extensions                                          | After upload              |
| ----- | --------------------------------------------------- | ------------------------- |
| Image | `.jpg .jpeg .png .gif .webp .bmp`                   | kept as-is                |
| Image | `.heic .heif` (iPhone)                              | converted to JPEG         |
| Video | `.mp4` (H.264)                                      | kept as-is                |
| Video | `.mp4` (HEVC), `.mov`, `.m4v`, `.webm`, `.ogv`, `.ogg` | transcoded to H.264 MP4 |

Per-upload size limit: **500 MB**.

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

## Sharing the notes timeline across machines

Photos and content hashes stay in `data/meta.json` (gitignored — each
instance has its own photo library). But the text-only **timeline notes**
(`📝` sticky cards) often want to ride along with `git pull` so a freshly
cloned host gets the same memory wall.

There's a tiny helper for that — `scripts/notes.py`:

```bash
# On the host where you write notes:
python3 scripts/notes.py export        # dumps notes → notes-seed.json (committed to git)
git add notes-seed.json && git commit -m "Update notes timeline" && git push

# On any other host after git pull:
python3 scripts/notes.py import        # merges notes-seed.json into local data/meta.json
                                       # additive only — won't overwrite local notes
```

`scripts/notes.py status` shows where everything is. The import step is
**idempotent**: re-running just skips notes already present locally.

If you'd rather not keep the seed file around long-term, `git rm
notes-seed.json` after every host has imported is fine — the script can
re-create it any time via `export`.

---

## Limitations over plain HTTP on a LAN

Some browser APIs only work in a "secure context" (HTTPS or `localhost`).
If you serve the gallery as plain HTTP to other devices on the LAN, expect:

- **Web Share with a file (⤓ Download/Share button on iOS Safari)** — silently
  unavailable; the lightbox falls back to a plain `<a download>` link, which
  still works on desktop but is less smooth on phones. Long-press on the
  photo still gives "Save to Photos" though.
- **Service Worker / offline cache / install banner** — won't register over
  HTTP-LAN. You can still "Add to Home Screen" from iOS Safari (manifest
  works), but installed PWA features (offline, push) are dormant.

To get the full experience, terminate TLS in front of the gallery — easiest
options: Tailscale Funnel, Cloudflare Tunnel, or a local reverse proxy with
a self-signed cert.

---

## Install as a PWA

**iPhone (Safari)** — open `http://<host>:8000` → Share → Add to Home Screen. Launching from the home-screen icon opens it as a standalone, full-screen app with no URL bar.

**Android (Chrome)** — will prompt with an "Install app" banner automatically (HTTPS or `localhost` only — see the Limitations section above).

---

## Troubleshooting

**HEIC photo uploads but won't display.**
The `pillow-heif` package isn't installed. In Docker this is baked in; for a bare-Python install make sure `.venv/bin/pip install pillow-heif` succeeded. On startup `server.py` prints `[heif] pillow-heif not installed — HEIC uploads will fail` if it's missing.

**Video upload finishes but shows "processing…" forever.**
Background transcode either crashed or `ffmpeg` isn't on `PATH`. Check the server log for `[transcode]` or `[worker]` lines. On a restart any item still marked `processing: true` is re-queued, so simply restarting often clears it. If `ffmpeg` is missing, install it (`apt install ffmpeg`) and restart.

**PWA won't install / no install banner.**
The browser requires a secure context. Plain HTTP over a LAN won't register the service worker — see the "Limitations over plain HTTP on a LAN" section. Use Tailscale, Cloudflare Tunnel, or a reverse proxy with TLS to get the full PWA experience.

**`Address already in use` / port 8000 taken.**
Another process is bound to 8000. Either stop it, or change the port:
- Docker: edit `docker-compose.yml` → `ports: ["8001:8000"]`
- Bare Python: `PORT=8001 python3 server.py`

---

## Tech stack

**Backend**

- **Python 3.10+**, standard library only — `http.server`, `threading`, `queue`, no framework
- **[Pillow](https://python-pillow.org/) 12** — image decode/encode, EXIF parsing, thumbnail generation
- **[pillow-heif](https://github.com/bigcat88/pillow_heif)** — HEIC/HEIF decode (ships its own libheif in the manylinux wheel, no apt install needed)
- **[ffmpeg](https://ffmpeg.org/)** — HEVC `.mov` / `.mp4` → H.264 MP4, video keyframe extraction for thumbs

**Frontend**

- **Vanilla JavaScript (ES2020+)** — no framework, no build step, no `npm`
- **Plain CSS** with custom properties, mobile-first responsive
- **Service Worker + Web App Manifest** for PWA install on iOS/Android
- **`<datalist>`** for tag autocomplete, **HTTP Range** for video seek, **`hashchange`** for state sync

**Infrastructure**

- **Docker** (`python:3.12-slim` + ffmpeg) — single-image deploy
- **docker compose** orchestration with bind-mounted `./data/` volume
- **Plain JSON** metadata store — no database, hand-editable, version-control friendly
- **SHA-1 content hash** for upload de-dup

---

## Design notes

- **No database.** All metadata lives in one JSON file. Simple, backup-friendly, hand-editable. Comfortable up to ~10,000 photos.
- **No auth.** The app assumes a trusted network. Pair with nginx + basic-auth, oauth2-proxy, or Tailscale if you need access control.
- **No frontend framework.** Vanilla JS, no build step, no npm.
- **Soft delete with 30-day TTL.** Deletes go to `data/trash/`; the trash page lets you restore or empty early. Expired entries are purged at server start.
- **Background transcode via a thread + queue.** On startup any item still marked `processing: true` is re-queued, so a mid-transcode restart recovers cleanly.
- **Video transcode uses `libx264 -preset veryfast -crf 23`** — balanced for speed and quality. Bump CRF or change preset to trade size vs. CPU.

---

## Contributing

This is a personal project shaped around one household's cats — feature scope is intentionally narrow. Bug reports and small focused PRs are welcome, but please open an issue first for anything bigger than a fix so we can sanity-check fit before you sink time into it. Forks for your own household are very much encouraged.

---

## License

Personal project; no license declared. Forking for personal use is fine; if you want to distribute, please get in touch first.
