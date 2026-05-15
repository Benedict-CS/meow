// ===== /trash — soft-deleted items with restore / permanent delete =====
const $ = (s) => document.querySelector(s);

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

async function load() {
  let data;
  try {
    const r = await fetch('/api/trash', { cache: 'no-store' });
    data = await r.json();
  } catch {
    $('#trash-list').innerHTML = '<div class="loading">無法載入垃圾桶資料 😿</div>';
    return;
  }
  const items = data.items || [];
  const ttl = data.ttl_days || 30;
  $('#ttl-line').textContent = `${ttl} 天後自動永久消失`;

  const list = $('#trash-list');
  const tools = $('#trash-toolbar');

  if (!items.length) {
    // Reuse the sleeping-cat + Zzz animation from the main gallery's empty
    // state (class `empty-emoji` is defined in style.css).
    list.innerHTML = `
      <div class="empty">
        <div class="empty-emoji">🐱</div>
        <p>垃圾桶是空的，沒有要救回的東西</p>
        <p class="empty-sub">貓貓睡得好沉 💤</p>
      </div>`;
    tools.hidden = true;
    return;
  }

  tools.hidden = false;
  $('#trash-count').textContent = `${items.length} 個項目`;

  list.innerHTML = items.map(it => {
    const countdownClass = it.days_left <= 7 ? 'countdown' : 'countdown safe';
    const left = it.days_left === 0 ? '今天消失' : `${it.days_left} 天後消失`;

    const visual = it.kind === 'note'
      ? `<div class="trash-note-text">${escapeHtml(it.text || '（空筆記）')}</div>`
      : `<div class="trash-thumb">
           <img src="${escapeHtml(it.thumb_url)}" loading="lazy" alt="">
           ${it.kind_inner === 'video' ? '<div class="video-badge">▶</div>' : ''}
         </div>`;

    const label = it.kind === 'note'
      ? `<span class="caption">📝 筆記</span>`
      : `<span class="caption">${escapeHtml(it.caption || it.name.split('-').slice(3).join('-') || '無說明')}</span>`;

    return `
      <div class="trash-item" data-name="${escapeHtml(it.name)}">
        ${visual}
        <div class="trash-meta">
          ${label}
          <div class="timing">
            <span>${fmtDate(it.deleted_at)} 刪除</span>
            <span class="${countdownClass}">${left}</span>
          </div>
        </div>
        <div class="trash-actions">
          <button class="restore" data-name="${escapeHtml(it.name)}">↻ 還原</button>
          <button class="perma" data-name="${escapeHtml(it.name)}">🗑 永久刪除</button>
        </div>
      </div>`;
  }).join('');

  list.querySelectorAll('.restore').forEach(btn => {
    btn.addEventListener('click', () => restore(btn.dataset.name));
  });
  list.querySelectorAll('.perma').forEach(btn => {
    btn.addEventListener('click', () => permaDelete(btn.dataset.name));
  });
}

async function restore(name) {
  const r = await fetch('/api/restore?file=' + encodeURIComponent(name), { method: 'POST' });
  if (r.ok) {
    toast('已還原 ↻', 'ok');
    load();
  } else {
    toast('還原失敗', 'error');
  }
}

async function permaDelete(name) {
  if (!confirm('確定要永久刪除這個項目嗎？這不能還原。')) return;
  const r = await fetch('/api/trash?file=' + encodeURIComponent(name), { method: 'DELETE' });
  if (r.ok) {
    toast('已永久刪除', 'ok');
    load();
  } else {
    toast('刪除失敗', 'error');
  }
}

$('#empty-all').addEventListener('click', async () => {
  if (!confirm('要清空整個垃圾桶嗎？所有項目會永久消失，無法還原。')) return;
  const r = await fetch('/api/trash', { method: 'DELETE' });
  if (r.ok) {
    const data = await r.json();
    toast(`已清空（${data.deleted_count} 個）`, 'ok');
    load();
  } else {
    toast('清空失敗', 'error');
  }
});

load();
