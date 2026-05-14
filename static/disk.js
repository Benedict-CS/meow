// ===== /disk — capacity dashboard =====
const fmt = (b) => {
  if (b < 1024) return b + ' B';
  if (b < 1024 ** 2) return (b / 1024).toFixed(1) + ' KB';
  if (b < 1024 ** 3) return (b / 1024 ** 2).toFixed(1) + ' MB';
  if (b < 1024 ** 4) return (b / 1024 ** 3).toFixed(2) + ' GB';
  return (b / 1024 ** 4).toFixed(2) + ' TB';
};
const pct = (n) => (n * 100).toFixed(1) + '%';

function bar(galleryFrac, otherFrac) {
  const otherEnd = otherFrac * 100;
  const galleryEnd = (otherFrac + galleryFrac) * 100;
  return `
    <div class="bar">
      <div class="bar-other" style="width: ${otherEnd}%;"></div>
      <div class="bar-mine"  style="left: ${otherEnd}%; width: ${galleryFrac * 100}%;"></div>
    </div>
  `;
}

async function load() {
  let s;
  try {
    const r = await fetch('/api/stats', { cache: 'no-store' });
    s = await r.json();
  } catch {
    document.querySelector('#stats').innerHTML = `<div class="loading error">無法取得容量資料 😿</div>`;
    return;
  }

  const galleryFrac = s.host_disk.total ? s.bytes.total / s.host_disk.total : 0;
  const otherUsed = Math.max(0, s.host_disk.used - s.bytes.total);
  const otherFrac = s.host_disk.total ? otherUsed / s.host_disk.total : 0;
  const freeFrac = 1 - galleryFrac - otherFrac;

  // rough "how many more photos can fit"
  const avg = s.avg_upload_bytes || 3 * 1024 * 1024;  // assume 3 MB if no data
  const moreFiles = Math.floor(s.host_disk.free / avg);

  document.querySelector('#stats').innerHTML = `
    <section class="card">
      <h2>📸 相簿內容</h2>
      <div class="row"><span>總檔數</span><b>${s.files.total}</b></div>
      <div class="row"><span>📷 照片</span><b>${s.files.image}</b></div>
      <div class="row"><span>🎬 影片</span><b>${s.files.video}</b></div>
      <div class="row"><span>平均一張 / 支</span><b>${s.avg_upload_bytes ? fmt(s.avg_upload_bytes) : '—'}</b></div>
    </section>

    <section class="card">
      <h2>💾 相簿吃了多少</h2>
      <div class="row"><span>原始檔</span><b>${fmt(s.bytes.uploads)}</b></div>
      <div class="row"><span>縮圖</span><b>${fmt(s.bytes.thumbs)}</b></div>
      <div class="row"><span>metadata</span><b>${fmt(s.bytes.meta)}</b></div>
      <div class="row total"><span>合計</span><b>${fmt(s.bytes.total)}</b></div>
    </section>

    <section class="card">
      <h2>🖥️ 主機磁碟</h2>
      ${bar(galleryFrac, otherFrac)}
      <div class="legend">
        <span><i class="dot other"></i>系統其他 ${pct(otherFrac)}</span>
        <span><i class="dot mine"></i>貓貓相簿 ${pct(galleryFrac)}</span>
        <span><i class="dot free"></i>剩餘 ${pct(freeFrac)}</span>
      </div>
      <div class="row"><span>總容量</span><b>${fmt(s.host_disk.total)}</b></div>
      <div class="row"><span>已使用</span><b>${fmt(s.host_disk.used)}</b></div>
      <div class="row"><span>還剩</span><b>${fmt(s.host_disk.free)}</b></div>
      <div class="row hint"><span>估計還可放</span><b>~ ${moreFiles.toLocaleString()} 張</b></div>
      <div class="row hint"><span>位置</span><b>${s.data_dir}</b></div>
    </section>
  `;

  document.querySelector('#updated').textContent =
    `更新時間：${new Date().toLocaleTimeString('zh-TW')} · 每 30 秒自動更新`;
}

load();
setInterval(load, 30_000);
