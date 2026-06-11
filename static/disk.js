// ===== /disk — capacity dashboard =====
// Vanilla JS. No imports. Preserves the /api/stats contract exactly.

// ---------- formatters ----------
const fmt = (b) => {
  if (b == null || !isFinite(b)) return '—';
  if (b < 1024) return b + ' B';
  if (b < 1024 ** 2) return (b / 1024).toFixed(1) + ' KB';
  if (b < 1024 ** 3) return (b / 1024 ** 2).toFixed(1) + ' MB';
  if (b < 1024 ** 4) return (b / 1024 ** 3).toFixed(2) + ' GB';
  return (b / 1024 ** 4).toFixed(2) + ' TB';
};
const pct = (n) => (n * 100).toFixed(1) + '%';

const prefersReducedMotion = () =>
  window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// ---------- DOM refs ----------
const $stats = document.querySelector('#stats');
const $updated = document.querySelector('#updated');
const $refreshBtn = document.querySelector('#refresh-btn');

// ---------- state ----------
let pollTimer = null;
let isLoading = false;
let firstLoadDone = false;
let lastFetchAt = null;

// ---------- animation helpers ----------
/**
 * Tween numeric content of an element from its current value to target.
 * Uses requestAnimationFrame; respects prefers-reduced-motion.
 */
function tweenNumber(el, target, { duration = 700, format = (v) => v.toLocaleString() } = {}) {
  if (!el) return;
  const start = Number(el.dataset.value || 0);
  if (prefersReducedMotion() || duration <= 0) {
    el.textContent = format(target);
    el.dataset.value = String(target);
    return;
  }
  const t0 = performance.now();
  const easeOut = (t) => 1 - Math.pow(1 - t, 3);
  function step(now) {
    const t = Math.min(1, (now - t0) / duration);
    const v = start + (target - start) * easeOut(t);
    el.textContent = format(t === 1 ? target : Math.round(v));
    if (t < 1) requestAnimationFrame(step);
    else el.dataset.value = String(target);
  }
  requestAnimationFrame(step);
}

/** Tween a byte-formatted element from current to target. */
function tweenBytes(el, target, duration = 800) {
  if (!el) return;
  const start = Number(el.dataset.value || 0);
  if (prefersReducedMotion() || duration <= 0) {
    el.textContent = fmt(target);
    el.dataset.value = String(target);
    return;
  }
  const t0 = performance.now();
  const easeOut = (t) => 1 - Math.pow(1 - t, 3);
  function step(now) {
    const t = Math.min(1, (now - t0) / duration);
    const v = start + (target - start) * easeOut(t);
    el.textContent = fmt(t === 1 ? target : v);
    if (t < 1) requestAnimationFrame(step);
    else el.dataset.value = String(target);
  }
  requestAnimationFrame(step);
}

// ---------- markup builders ----------
function buildMarkup(s) {
  // Derived fractions
  const galleryFrac = s.host_disk.total ? s.bytes.total / s.host_disk.total : 0;
  const otherUsed = Math.max(0, s.host_disk.used - s.bytes.total);
  const otherFrac = s.host_disk.total ? otherUsed / s.host_disk.total : 0;
  const freeFrac = Math.max(0, 1 - galleryFrac - otherFrac);

  // Rough "how many more photos can fit"
  const avg = s.avg_upload_bytes || 3 * 1024 * 1024; // assume 3 MB if no data
  const moreFiles = Math.floor(s.host_disk.free / avg);

  return {
    galleryFrac, otherFrac, freeFrac, moreFiles,
    html: `
    <section class="card reveal" aria-labelledby="card-album-h">
      <h2 id="card-album-h"><span aria-hidden="true">📸</span> 相簿內容</h2>
      <div class="row">
        <span>總檔數</span>
        <b data-anim="num" data-target="${s.files.total}">0</b>
      </div>
      <div class="row">
        <span><span aria-hidden="true">📷</span> 照片</span>
        <b data-anim="num" data-target="${s.files.image}">0</b>
      </div>
      <div class="row">
        <span><span aria-hidden="true">🎬</span> 影片</span>
        <b data-anim="num" data-target="${s.files.video}">0</b>
      </div>
      <div class="row">
        <span>平均一張 / 支</span>
        <b>${s.avg_upload_bytes ? fmt(s.avg_upload_bytes) : '—'}</b>
      </div>
    </section>

    <section class="card reveal" aria-labelledby="card-size-h">
      <h2 id="card-size-h"><span aria-hidden="true">💾</span> 相簿吃了多少</h2>
      <div class="row">
        <span>原始檔</span>
        <b data-anim="bytes" data-target="${s.bytes.uploads}">0 B</b>
      </div>
      <div class="row">
        <span>縮圖</span>
        <b data-anim="bytes" data-target="${s.bytes.thumbs}">0 B</b>
      </div>
      <div class="row">
        <span>metadata</span>
        <b data-anim="bytes" data-target="${s.bytes.meta}">0 B</b>
      </div>
      <div class="row total">
        <span>合計</span>
        <b data-anim="bytes" data-target="${s.bytes.total}">0 B</b>
      </div>
    </section>

    <section class="card reveal" aria-labelledby="card-host-h">
      <h2 id="card-host-h"><span aria-hidden="true">🖥️</span> 主機磁碟</h2>

      <div
        class="bar"
        role="img"
        aria-label="主機磁碟使用：系統其他 ${pct(otherFrac)}、貓貓相簿 ${pct(galleryFrac)}、剩餘 ${pct(freeFrac)}"
      >
        <div class="bar-other" data-bar-other style="width: 0%;"></div>
        <div class="bar-mine"  data-bar-mine  style="left: 0%; width: 0%;"></div>
      </div>

      <div class="legend" aria-hidden="true">
        <span><i class="dot other"></i>系統其他<span class="pct">${pct(otherFrac)}</span></span>
        <span><i class="dot mine"></i>貓貓相簿<span class="pct">${pct(galleryFrac)}</span></span>
        <span><i class="dot free"></i>剩餘<span class="pct">${pct(freeFrac)}</span></span>
      </div>

      <div class="row">
        <span>總容量</span>
        <b data-anim="bytes" data-target="${s.host_disk.total}">0 B</b>
      </div>
      <div class="row">
        <span>已使用</span>
        <b data-anim="bytes" data-target="${s.host_disk.used}">0 B</b>
      </div>
      <div class="row">
        <span>還剩</span>
        <b data-anim="bytes" data-target="${s.host_disk.free}">0 B</b>
      </div>
      <div class="row hint">
        <span>估計還可放</span>
        <b>~ <span data-anim="num" data-target="${moreFiles}">0</span> 張</b>
      </div>
      <div class="row hint path">
        <span>位置</span>
        <b title="${escapeAttr(s.data_dir || '')}">${escapeHtml(s.data_dir || '')}</b>
      </div>
    </section>
    `
  };
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}
function escapeAttr(s) {
  return escapeHtml(s).replace(/"/g, '&quot;');
}

// ---------- animate inserted nodes ----------
function animateNewlyInserted(meta) {
  // Numbers
  $stats.querySelectorAll('[data-anim="num"]').forEach((el) => {
    const target = Number(el.dataset.target || 0);
    tweenNumber(el, target, { duration: 700 });
  });
  // Byte sizes
  $stats.querySelectorAll('[data-anim="bytes"]').forEach((el) => {
    const target = Number(el.dataset.target || 0);
    tweenBytes(el, target, 800);
  });
  // Bar segments — small delay so the entry animation lands first
  const otherEl = $stats.querySelector('[data-bar-other]');
  const mineEl  = $stats.querySelector('[data-bar-mine]');
  const otherEnd = meta.otherFrac * 100;
  const mineEnd = meta.galleryFrac * 100;
  const apply = () => {
    if (otherEl) otherEl.style.width = otherEnd + '%';
    if (mineEl) {
      mineEl.style.left  = otherEnd + '%';
      mineEl.style.width = mineEnd + '%';
    }
  };
  if (prefersReducedMotion()) {
    apply();
  } else {
    // Force a layout flush so the transition runs
    requestAnimationFrame(() => requestAnimationFrame(apply));
  }
}

// ---------- error rendering ----------
function renderError(message) {
  $stats.setAttribute('aria-busy', 'false');
  $stats.innerHTML = `
    <div class="error-state" role="alert">
      <span class="err-emoji" aria-hidden="true">😿</span>
      <p class="err-title">載入失敗</p>
      <p class="err-msg">${escapeHtml(message || '無法取得容量資料')}</p>
      <button type="button" class="retry-btn" id="retry-btn">
        <span aria-hidden="true">↻</span>
        <span>再試一次</span>
      </button>
    </div>
  `;
  const $retry = document.querySelector('#retry-btn');
  if ($retry) {
    $retry.addEventListener('click', () => load(true));
    // Move focus so screen readers + keyboard users land on the retry control
    $retry.focus({ preventScroll: true });
  }
}

// ---------- updated-at footnote ----------
function setUpdated() {
  lastFetchAt = new Date();
  const t = lastFetchAt.toLocaleTimeString('zh-TW', { hour12: false });
  $updated.innerHTML =
    `<span class="pulse-dot" aria-hidden="true"></span>更新於 ${t} · 每 30 秒自動更新`;
}

// ---------- main loader ----------
async function load(userInitiated = false) {
  if (isLoading) return;
  isLoading = true;

  if (userInitiated && $refreshBtn) {
    $refreshBtn.classList.add('spinning');
    $refreshBtn.setAttribute('aria-busy', 'true');
  }
  $stats.setAttribute('aria-busy', 'true');

  let s;
  try {
    const r = await fetch('/api/stats', { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    s = await r.json();
  } catch (err) {
    isLoading = false;
    if ($refreshBtn) {
      $refreshBtn.classList.remove('spinning');
      $refreshBtn.removeAttribute('aria-busy');
    }
    renderError('伺服器沒有回應，請稍後再試');
    return;
  }

  const { html, ...meta } = buildMarkup(s);
  $stats.innerHTML = html;
  $stats.setAttribute('aria-busy', 'false');

  animateNewlyInserted(meta);
  setUpdated();

  firstLoadDone = true;
  isLoading = false;

  if ($refreshBtn) {
    $refreshBtn.classList.remove('spinning');
    $refreshBtn.removeAttribute('aria-busy');
  }
}

// ---------- polling control (pause when tab hidden) ----------
function startPolling() {
  stopPolling();
  pollTimer = setInterval(() => {
    if (document.visibilityState === 'visible') load(false);
  }, 30_000);
}
function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') {
    // If it's been a while since last fetch, refresh immediately
    if (!lastFetchAt || (Date.now() - lastFetchAt.getTime()) > 30_000) {
      load(false);
    }
    startPolling();
  } else {
    stopPolling();
  }
});

// ---------- refresh button + keyboard shortcut ----------
if ($refreshBtn) {
  $refreshBtn.addEventListener('click', () => load(true));
}
document.addEventListener('keydown', (e) => {
  // 'R' refreshes — ignore when user is typing in a field or using modifier keys
  if (e.defaultPrevented) return;
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const tag = (e.target && e.target.tagName) || '';
  if (/^(INPUT|TEXTAREA|SELECT)$/.test(tag)) return;
  if (e.key === 'r' || e.key === 'R') {
    e.preventDefault();
    load(true);
  }
});

// ---------- kickoff ----------
load(false);
startPolling();
