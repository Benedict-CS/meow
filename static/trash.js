// ===== /trash — soft-deleted items with restore / permanent delete =====
// Polished UX: multi-select, batch restore + batch delete (client-side loop
// over the existing per-item endpoints — server contract unchanged),
// custom confirm dialog (friendlier and accessible than window.confirm),
// TTL progress bar with color tier, keyboard support, focus management.

const $  = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

let toastTimer;
function toast(msg, kind = '') {
  const el = $('#toast');
  el.textContent = msg;
  el.className = 'toast ' + kind;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 2400);
}

function fmtDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return '';
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}/${p(d.getMonth()+1)}/${p(d.getDate())}`;
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
}

// Tier the days_left into one of four buckets that drive both the
// progress-bar color and the countdown text.
//   safe     — > 50% TTL remaining
//   warn     — 25–50% remaining
//   danger   — 1–25% remaining
//   critical — final 24h (days_left === 0)
function tier(daysLeft, ttl) {
  if (daysLeft <= 0) return 'critical';
  const pct = daysLeft / ttl;
  if (pct > 0.5)  return 'safe';
  if (pct > 0.25) return 'warn';
  return 'danger';
}

// ===== Custom confirm dialog =====
let confirmResolver = null;
let lastFocusedBeforeConfirm = null;

function openConfirm({ title, body, okText = '確定', cancelText = '再想想', okClass = '' }) {
  return new Promise((resolve) => {
    confirmResolver = resolve;
    const backdrop = $('#confirm-backdrop');
    const card = backdrop.querySelector('.confirm-card');
    $('#confirm-title').textContent = title;
    $('#confirm-body').textContent  = body;
    const okBtn = $('#confirm-ok');
    const cancelBtn = $('#confirm-cancel');
    okBtn.textContent = okText;
    cancelBtn.textContent = cancelText;
    okBtn.className = 'confirm-ok' + (okClass ? ' ' + okClass : '');
    lastFocusedBeforeConfirm = document.activeElement;
    backdrop.hidden = false;
    // Focus the cancel button by default — safer for destructive actions.
    requestAnimationFrame(() => cancelBtn.focus());
  });
}

function closeConfirm(result) {
  const backdrop = $('#confirm-backdrop');
  backdrop.hidden = true;
  if (lastFocusedBeforeConfirm && document.contains(lastFocusedBeforeConfirm)) {
    try { lastFocusedBeforeConfirm.focus(); } catch (_) {}
  }
  lastFocusedBeforeConfirm = null;
  if (confirmResolver) {
    const r = confirmResolver;
    confirmResolver = null;
    r(result);
  }
}

$('#confirm-ok').addEventListener('click', () => closeConfirm(true));
$('#confirm-cancel').addEventListener('click', () => closeConfirm(false));
$('#confirm-backdrop').addEventListener('click', (e) => {
  if (e.target.id === 'confirm-backdrop') closeConfirm(false);
});
document.addEventListener('keydown', (e) => {
  if ($('#confirm-backdrop').hidden) return;
  if (e.key === 'Escape') { e.preventDefault(); closeConfirm(false); }
  // Trap focus inside the dialog
  if (e.key === 'Tab') {
    const focusable = $('#confirm-backdrop').querySelectorAll('button');
    if (!focusable.length) return;
    const first = focusable[0];
    const last  = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
});

// ===== Selection state =====
const selected = new Set();
let currentItems = [];

function updateSelectionUI() {
  const total = currentItems.length;
  const selCount = selected.size;
  const selectAll = $('#select-all');
  if (selCount === 0) {
    selectAll.checked = false;
    selectAll.indeterminate = false;
  } else if (selCount === total) {
    selectAll.checked = true;
    selectAll.indeterminate = false;
  } else {
    selectAll.checked = false;
    selectAll.indeterminate = true;
  }

  // Re-tag visible cards.
  $$('.trash-item').forEach(el => {
    const name = el.dataset.name;
    el.classList.toggle('selected', selected.has(name));
    const cb = el.querySelector('.trash-check');
    if (cb) cb.setAttribute('aria-checked', selected.has(name) ? 'true' : 'false');
  });

  // Show batch buttons only when at least one item is selected.
  const restoreBtn = $('#restore-selected');
  const deleteBtn  = $('#delete-selected');
  restoreBtn.hidden = selCount === 0;
  deleteBtn.hidden  = selCount === 0;
  if (selCount > 0) {
    restoreBtn.textContent = `↻ 還原所選 (${selCount})`;
    deleteBtn.textContent  = `🗑 刪除所選 (${selCount})`;
  }

  // Update count line: prefer showing selection over total when active.
  const countEl = $('#trash-count');
  if (selCount > 0) {
    countEl.textContent = `已選 ${selCount} / ${total}`;
  } else {
    countEl.textContent = `${total} 個項目`;
  }
}

function toggleSelect(name) {
  if (selected.has(name)) selected.delete(name);
  else selected.add(name);
  updateSelectionUI();
}

// ===== Rendering =====
function renderList(items, ttl) {
  const list = $('#trash-list');
  const tools = $('#trash-toolbar');
  currentItems = items;
  // Prune selection of anything that's no longer present.
  const names = new Set(items.map(i => i.name));
  [...selected].forEach(n => { if (!names.has(n)) selected.delete(n); });

  if (!items.length) {
    list.setAttribute('aria-busy', 'false');
    list.innerHTML = `
      <div class="empty">
        <div class="empty-emoji" aria-hidden="true">🐱</div>
        <p>垃圾桶是空的，沒有要救回的東西</p>
        <p class="empty-sub">貓貓睡得好沉 💤</p>
        <p class="empty-tip">小提示：刪除的相片會在這裡保留 ${ttl} 天，期限內都能救回。<br><a href="/">回相簿首頁</a></p>
      </div>`;
    tools.hidden = true;
    return;
  }

  tools.hidden = false;

  list.innerHTML = items.map(it => {
    const t = tier(it.days_left, ttl);
    const pct = Math.max(0, Math.min(100, (it.days_left / ttl) * 100));
    const left = it.days_left === 0
      ? '今天消失'
      : it.days_left === 1
        ? '明天消失'
        : `${it.days_left} 天後消失`;

    const visual = it.kind === 'note'
      ? `<div class="trash-note-text">${escapeHtml(it.text || '（空筆記）')}</div>`
      : `<div class="trash-thumb">
           <img src="${escapeHtml(it.thumb_url)}" loading="lazy" alt="">
           ${it.kind_inner === 'video' ? '<div class="video-badge" aria-label="影片">▶</div>' : ''}
         </div>`;

    const label = it.kind === 'note'
      ? `<span class="caption">📝 筆記</span>`
      : `<span class="caption">${escapeHtml(it.caption || it.name.split('-').slice(3).join('-') || '無說明')}</span>`;

    const ttlTitle = `剩餘 ${it.days_left} 天 / 共 ${ttl} 天`;

    return `
      <article class="trash-item ${selected.has(it.name) ? 'selected' : ''}" data-name="${escapeHtml(it.name)}" role="listitem">
        <button class="trash-check" type="button" role="checkbox"
                aria-checked="${selected.has(it.name) ? 'true' : 'false'}"
                aria-label="選取此項目" data-name="${escapeHtml(it.name)}"></button>
        ${visual}
        <div class="trash-meta">
          ${label}
          <div class="timing">
            <span class="deleted-at">${fmtDate(it.deleted_at)} 刪除</span>
            <span class="countdown" data-tier="${t}">${left}</span>
          </div>
        </div>
        <div class="ttl-bar" data-tier="${t}" style="--pct: ${pct}%" role="progressbar"
             aria-valuemin="0" aria-valuemax="${ttl}" aria-valuenow="${it.days_left}"
             aria-label="${ttlTitle}" title="${ttlTitle}"></div>
        <div class="trash-actions">
          <button class="restore" type="button" data-name="${escapeHtml(it.name)}" aria-label="還原 ${escapeHtml(it.caption || '此項目')}">↻ 還原</button>
          <button class="perma"   type="button" data-name="${escapeHtml(it.name)}" aria-label="永久刪除 ${escapeHtml(it.caption || '此項目')}">🗑 永久刪除</button>
        </div>
      </article>`;
  }).join('');

  list.setAttribute('aria-busy', 'false');

  // Wire up per-item handlers.
  list.querySelectorAll('.restore').forEach(btn => {
    btn.addEventListener('click', (e) => { e.stopPropagation(); restoreOne(btn.dataset.name); });
  });
  list.querySelectorAll('.perma').forEach(btn => {
    btn.addEventListener('click', (e) => { e.stopPropagation(); permaDeleteOne(btn.dataset.name); });
  });
  list.querySelectorAll('.trash-check').forEach(cb => {
    cb.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleSelect(cb.dataset.name);
    });
    cb.addEventListener('keydown', (e) => {
      if (e.key === ' ' || e.key === 'Enter') {
        e.preventDefault();
        toggleSelect(cb.dataset.name);
      }
    });
  });

  updateSelectionUI();
}

// ===== Load =====
async function load() {
  const list = $('#trash-list');
  list.setAttribute('aria-busy', 'true');
  let data;
  try {
    const r = await fetch('/api/trash', { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    data = await r.json();
  } catch (err) {
    list.setAttribute('aria-busy', 'false');
    list.innerHTML = `
      <div class="error-state" role="alert">
        <div style="font-size:2rem">😿</div>
        <p>無法載入垃圾桶資料</p>
        <button class="retry" type="button" id="retry-load">重試</button>
      </div>`;
    const retry = $('#retry-load');
    if (retry) retry.addEventListener('click', load);
    $('#trash-toolbar').hidden = true;
    return;
  }
  const items = data.items || [];
  const ttl = data.ttl_days || 30;
  $('#ttl-line').textContent = `${ttl} 天後自動永久消失`;
  renderList(items, ttl);
}

// ===== Animated removal helper =====
function animateRemove(name, mode /* 'restore' | 'perma' */) {
  return new Promise((resolve) => {
    const el = document.querySelector(`.trash-item[data-name="${CSS.escape(name)}"]`);
    if (!el) return resolve();
    const cls = mode === 'restore' ? 'is-restoring' : 'is-leaving';
    // Respect reduced motion: just hide.
    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduce) { el.remove(); return resolve(); }
    el.classList.add(cls);
    el.addEventListener('animationend', () => { el.remove(); resolve(); }, { once: true });
    // Safety timeout in case animationend doesn't fire.
    setTimeout(() => { try { el.remove(); } catch (_) {} resolve(); }, 600);
  });
}

// ===== Per-item ops =====
async function restoreOne(name) {
  const r = await fetch('/api/restore?file=' + encodeURIComponent(name), { method: 'POST' });
  if (r.ok) {
    selected.delete(name);
    await animateRemove(name, 'restore');
    toast('已還原 ↻', 'ok');
    load();
  } else {
    toast('還原失敗', 'error');
  }
}

async function permaDeleteOne(name) {
  const ok = await openConfirm({
    title: '永久刪除？',
    body: '確定要永久刪除這個項目嗎？這不能還原。',
    okText: '永久刪除',
    cancelText: '再想想',
  });
  if (!ok) return;
  const r = await fetch('/api/trash?file=' + encodeURIComponent(name), { method: 'DELETE' });
  if (r.ok) {
    selected.delete(name);
    await animateRemove(name, 'perma');
    toast('已永久刪除', 'ok');
    load();
  } else {
    toast('刪除失敗', 'error');
  }
}

// ===== Batch ops =====
async function restoreSelected() {
  const names = [...selected];
  if (!names.length) return;
  const ok = await openConfirm({
    title: `還原 ${names.length} 個？`,
    body: '這些項目會回到相簿。',
    okText: '還原',
    cancelText: '取消',
    okClass: 'is-friendly',
  });
  if (!ok) return;
  let success = 0, fail = 0;
  for (const name of names) {
    try {
      const r = await fetch('/api/restore?file=' + encodeURIComponent(name), { method: 'POST' });
      if (r.ok) {
        success++;
        selected.delete(name);
        animateRemove(name, 'restore');
      } else fail++;
    } catch { fail++; }
  }
  if (fail === 0) toast(`已還原 ${success} 個 ↻`, 'ok');
  else            toast(`還原 ${success} 個，${fail} 個失敗`, fail ? 'error' : 'ok');
  load();
}

async function deleteSelected() {
  const names = [...selected];
  if (!names.length) return;
  const ok = await openConfirm({
    title: `永久刪除 ${names.length} 個？`,
    body: '這些項目會永久消失，無法還原。',
    okText: '永久刪除',
    cancelText: '取消',
  });
  if (!ok) return;
  let success = 0, fail = 0;
  for (const name of names) {
    try {
      const r = await fetch('/api/trash?file=' + encodeURIComponent(name), { method: 'DELETE' });
      if (r.ok) {
        success++;
        selected.delete(name);
        animateRemove(name, 'perma');
      } else fail++;
    } catch { fail++; }
  }
  if (fail === 0) toast(`已永久刪除 ${success} 個`, 'ok');
  else            toast(`刪除 ${success} 個，${fail} 個失敗`, fail ? 'error' : 'ok');
  load();
}

// ===== Wire toolbar =====
$('#empty-all').addEventListener('click', async () => {
  const ok = await openConfirm({
    title: '清空垃圾桶？',
    body: '所有項目會永久消失，無法還原。',
    okText: '清空',
    cancelText: '取消',
  });
  if (!ok) return;
  const r = await fetch('/api/trash', { method: 'DELETE' });
  if (r.ok) {
    const data = await r.json();
    toast(`已清空（${data.deleted_count} 個）`, 'ok');
    selected.clear();
    load();
  } else {
    toast('清空失敗', 'error');
  }
});

$('#select-all').addEventListener('change', (e) => {
  if (e.target.checked) {
    currentItems.forEach(it => selected.add(it.name));
  } else {
    selected.clear();
  }
  updateSelectionUI();
});

$('#restore-selected').addEventListener('click', restoreSelected);
$('#delete-selected').addEventListener('click', deleteSelected);

// Keyboard shortcut: Esc clears selection when not in a dialog.
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && $('#confirm-backdrop').hidden && selected.size > 0) {
    selected.clear();
    updateSelectionUI();
  }
});

load();
