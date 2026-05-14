# 🐾 貓貓相簿 · Meow Gallery

收集每一個喵喵瞬間 — 拼貼風格的相片 / 影片相簿，純 Python + 原生 HTTP，自帶 iPhone 格式自動轉檔。

> 個人 / 家用級工具，**沒有登入機制**，預設只在本機或內網跑。若要對外請套 reverse proxy + auth。

---

## 特色

- 🖼️ **拼貼 (scrapbook) 排版** — Polaroid 卡片、紙膠帶、傾斜變化，按月份分區
- 📱 **iPhone 友善** — HEIC → JPEG、HEVC `.mov` → H.264 MP4 自動轉檔（影片轉碼在背景做，上傳秒回）
- 📐 **完整 RWD** — 手機 / iPad / 橫向、觸控手勢、PWA 可加到主畫面變獨立 app
- ⏩ **影片可 seek** — HTTP `Range` 支援
- 🔁 **內容去重** — SHA-1 hash，重複上傳合併
- ✏️ **可編輯 metadata** — 標題、標籤、拍攝時間、最愛，全部 lightbox 內改
- ☑️ **批次操作** — 多選後一鍵刪除 / 加標籤 / 設最愛
- 🔗 **URL 狀態同步** — filter / 搜尋 / 開啟的照片都在 hash，可分享連結、上一頁回得到

---

## 快速開始（Docker，推薦）

```bash
git clone https://github.com/Benedict-CS/meow.git
cd meow
docker compose up -d --build
# → http://localhost:8000
```

資料會自動存在 `./data/`（uploads / thumbs / meta.json）。

容器內預設用 `1000:1000` 跑，跟 host 上的 `ben` 對得起來。如果你 UID 不是 1000：

```bash
CAT_UID=$(id -u) CAT_GID=$(id -g) docker compose up -d --build
```

---

## 不用 Docker（直接跑 Python）

需要 Python 3.10+、ffmpeg、Pillow、pillow-heif。

```bash
sudo apt install -y ffmpeg python3-venv      # Debian/Ubuntu
python3 -m venv .venv
.venv/bin/pip install Pillow pillow-heif
.venv/bin/python server.py
# → http://127.0.0.1:8000
```

`server.py` 啟動時會自動把 `./venv/lib/.../site-packages` 加入 `sys.path`，所以也可以直接 `python3 server.py`。

### 環境變數

| 變數 | 預設 | 說明 |
|---|---|---|
| `HOST` | `127.0.0.1` | 監聽 IP，要外網可用 `0.0.0.0` |
| `PORT` | `8000` | 監聽 port |
| `DATA_DIR` | 專案目錄（Docker 是 `/data`） | uploads / thumbs / meta.json 的家 |

---

## 操作快捷鍵（Lightbox）

| 鍵 | 動作 |
|---|---|
| `←` / `→` | 上一張 / 下一張 |
| `Space` | 投影片播放 / 暫停 |
| `F` | 切換最愛 |
| `Esc` | 關閉 |
| 觸控滑動 | 左右切換、下滑關閉 |
| 點日期 | 編輯拍攝時間 |
| 點描述 | 編輯標題（有「✓ 儲存」按鈕） |

---

## 檔案結構

```
meow/
├── server.py              純 stdlib HTTP server (~900 行)
├── static/
│   ├── index.html
│   ├── style.css
│   ├── app.js
│   ├── sw.js              PWA service worker
│   ├── manifest.json      PWA manifest
│   └── icon-*.png / .svg
├── Dockerfile             python:3.12-slim + ffmpeg
├── docker-compose.yml
├── .dockerignore
├── .gitignore             擋掉 data/ uploads/ thumbs/ meta.json .venv/
└── data/                  ← 不在 git，唯一需要備份的東西
    ├── uploads/           原始檔
    ├── thumbs/            縮圖（max 600px JPEG）
    └── meta.json          全部 metadata（人類可讀 JSON）
```

---

## API

| Method | Path | 說明 |
|---|---|---|
| `GET`    | `/api/list`         | 所有檔案 + metadata |
| `POST`   | `/api/upload`       | 多檔上傳 (multipart) |
| `PUT`    | `/api/meta?file=`   | 改單一檔（body: JSON patch） |
| `DELETE` | `/api/delete?file=` | 刪單一檔 |
| `POST`   | `/api/batch-delete` | `{files:[...]}` |
| `POST`   | `/api/batch-meta`   | `{files:[...], patch:{favorite, caption, tags_add, tags_remove}}` |
| `GET`    | `/uploads/*`        | 原始檔（支援 `Range`） |
| `GET`    | `/thumbs/*`         | 縮圖 |
| `GET`    | `/sw.js`, `/manifest.json` | PWA |

支援的格式：

| 類型 | 副檔名 | 上傳後 |
|---|---|---|
| 圖片 | `.jpg .jpeg .png .gif .webp .bmp` | 原樣保留 |
| 圖片 | `.heic .heif` (iPhone) | 轉成 JPEG |
| 影片 | `.mp4` (H.264) | 原樣保留 |
| 影片 | `.mp4` (HEVC) / `.mov` / `.m4v` / `.webm` / `.ogv` | 轉成 H.264 MP4 |

單檔上限 500 MB。

---

## 備份 / Migration

整個系統只有 `./data/` 是不可重生的，**備份它一份就足夠**。

```bash
# 備份（含時間戳）
tar czf catdata-$(date +%F).tgz -C /path/to/meow data/

# 還原到新機器
git clone https://github.com/Benedict-CS/meow.git
cd meow
tar xzf /path/to/catdata-XXXX-XX-XX.tgz
docker compose up -d --build
```

每天自動備份的 cron 範例：

```cron
0 3 * * * tar czf /backup/cat-$(date +\%F).tgz -C /home/ben/cat-gallery data/
```

`meta.json` 是純 JSON、人類可讀，必要時可手動編輯（修壞了從備份還原即可）。

---

## PWA 安裝

**iPhone (Safari)**：開 `http://<host>:8000` → 分享 → 加入主畫面 → 之後從主畫面開就是全螢幕 app（無網址列、無 tab）。

**Android (Chrome)**：會自動跳「安裝 app」提示，點下去就行。

---

## 設計取捨

- **沒有資料庫** — 所有 metadata 都在一個 JSON 檔。簡單、可備份、可手動編輯。適合 < 10000 張的個人相簿。
- **沒有認證** — 預期跑在 LAN / Tailscale 之類的私網內。對外請套 nginx + basic auth 或 oauth2-proxy。
- **沒有前端 framework** — vanilla JS，沒有 build step、沒有 npm。
- **背景轉檔用 thread queue** — 簡單，重啟時會把 `processing:true` 的檔重新排進佇列。
- **影片轉碼用 `libx264 -preset veryfast -crf 23`** — 平衡速度跟畫質。要更小檔可改 `crf 26` 或 `preset slow`。

---

## License

私人專案，目前未設 license。要 fork 自用沒問題，要散布請聯絡作者。
