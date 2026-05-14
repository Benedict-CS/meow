// ===== Cat Gallery — frontend =====
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

const state = {
  items: [],            // all items (raw, sorted desc by captured_at)
  filter: 'all',        // all | image | video | favorite
  activeTag: null,      // tag filter
  search: '',           // search query
  selectMode: false,
  selected: new Set(),
  lbIndex: -1,          // index within currently visible items
  slideshowTimer: null,
  pollTimer: null,      // background poll while any item is processing
  applyingUrl: false,   // suppress URL writes while restoring from URL
};

const MONTH_NAMES_ZH = [
  '1 月', '2 月', '3 月', '4 月', '5 月', '6 月',
  '7 月', '8 月', '9 月', '10 月', '11 月', '12 月',
];

// ---------- Toast ----------
let toastTimer;
function toast(msg, kind = '') {
  const el = $('#toast');
  el.textContent = msg;
  el.className = 'toast ' + kind;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 2400);
}

// Cute toast — pick a random phrase from a category.
const PHRASES = {
  saved:    ['儲存好了 🐾', '記下了 ✨', '貓貓滿意 😺', '咕嚕咕嚕～', '收下了 💝', '寫進相簿了 📒'],
  fav_on:   ['加入最愛 💖', '貓貓也愛這張 ❤️', '永久珍藏 ✨', '收進喵心 💝', '今天的小確幸 🌸'],
  fav_off:  ['取消最愛了', '下次再說 〜', '從最愛拿出來了'],
  uploaded: ['上傳完成 🐱', '又多幾張喵喵 🎉', '可愛收齊 ✨', '相簿更胖了 🐾', '貓口普查 +N 🐈'],
  deleted:  ['再見 👋', '已刪除 🗑', '送走了 〜'],
  error:    ['出了點事 😿', '失敗了，再試試？', '貓貓困惑了 ❓'],
};
function cuteToast(key, fallback, kind = '') {
  const arr = PHRASES[key];
  const msg = (arr && arr.length) ? arr[Math.floor(Math.random() * arr.length)] : (fallback || '');
  toast(msg, kind);
}

// ---------- Heart burst (clicking ❤️) ----------
function spawnHearts(originEl) {
  const r = originEl.getBoundingClientRect();
  const cx = r.left + r.width / 2;
  const cy = r.top  + r.height / 2;
  const n = 4 + Math.floor(Math.random() * 3); // 4..6
  const colors = ['#ec5e6b', '#ff8a95', '#ffb1b8', '#d9886b', '#f06292'];
  const glyphs = ['♥', '♡', '❤', '💕'];
  for (let i = 0; i < n; i++) {
    const h = document.createElement('div');
    h.className = 'heart-burst';
    h.textContent = glyphs[Math.floor(Math.random() * glyphs.length)];
    h.style.left = cx + 'px';
    h.style.top  = cy + 'px';
    h.style.color = colors[i % colors.length];
    h.style.setProperty('--dx',  ((Math.random() * 110) - 55) + 'px');
    h.style.setProperty('--dy',  -(50 + Math.random() * 80) + 'px');
    h.style.setProperty('--rot', ((Math.random() * 70) - 35) + 'deg');
    h.style.animationDelay = (i * 0.04) + 's';
    document.body.appendChild(h);
    setTimeout(() => h.remove(), 1300);
  }
}

// ---------- Lightbox enter transitions (random per slide) ----------
const ENTER_CLASSES = ['enter-fade', 'enter-right', 'enter-left', 'enter-zoom', 'enter-up'];
function pickEnter() {
  return ENTER_CLASSES[Math.floor(Math.random() * ENTER_CLASSES.length)];
}
const SLIDESHOW_MS = 2000;

// ---------- Helpers ----------
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
}
function parseDate(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  return isNaN(d) ? null : d;
}
function fmtDate(iso) {
  const d = parseDate(iso);
  if (!d) return '';
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}/${pad(d.getMonth()+1)}/${pad(d.getDate())}`;
}
function fmtDateTime(iso) {
  const d = parseDate(iso);
  if (!d) return '';
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}/${pad(d.getMonth()+1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function monthKey(iso) {
  const d = parseDate(iso);
  if (!d) return '0000-00';
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;
}
function monthLabel(key) {
  const [y, m] = key.split('-');
  return `${y} 年 ${MONTH_NAMES_ZH[parseInt(m,10)-1] || m}`;
}

// ---------- API ----------
async function apiList() {
  const r = await fetch('/api/list', { cache: 'no-store' });
  const data = await r.json();
  state.items = data.items || [];
}
async function apiPatchMeta(name, patch) {
  const r = await fetch('/api/meta?file=' + encodeURIComponent(name), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  if (!r.ok) throw new Error('patch failed');
  return r.json();
}
async function apiDelete(name) {
  const r = await fetch('/api/delete?file=' + encodeURIComponent(name), { method: 'DELETE' });
  return r.ok;
}
async function apiBatchDelete(names) {
  const r = await fetch('/api/batch-delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ files: names }),
  });
  return r.ok ? r.json() : null;
}
async function apiBatchMeta(names, patch) {
  const r = await fetch('/api/batch-meta', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ files: names, patch }),
  });
  return r.ok ? r.json() : null;
}

// ---------- Filtering ----------
function visibleItems() {
  const q = state.search.trim().toLowerCase();
  return state.items.filter(it => {
    if (state.filter === 'image' && it.kind !== 'image') return false;
    if (state.filter === 'video' && it.kind !== 'video') return false;
    if (state.filter === 'favorite' && !it.favorite) return false;
    if (state.activeTag && !(it.tags || []).includes(state.activeTag)) return false;
    if (q) {
      const hay = (it.name + ' ' + (it.caption||'') + ' ' + (it.tags||[]).join(' ')).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function allTags() {
  const counts = new Map();
  for (const it of state.items) {
    for (const t of it.tags || []) counts.set(t, (counts.get(t) || 0) + 1);
  }
  return [...counts.entries()].sort((a,b) => b[1] - a[1]);
}

// ---------- Render ----------
function render() {
  const gallery = $('#gallery');
  const items = visibleItems();
  $('#count').textContent = items.length;
  $('#total-count').textContent = state.items.length;

  // tag bar
  const tags = allTags();
  const tagBar = $('#tag-bar');
  tagBar.innerHTML = '';
  if (tags.length) {
    for (const [tag, count] of tags) {
      const chip = document.createElement('button');
      chip.className = 'tag-chip' + (state.activeTag === tag ? ' active' : '');
      chip.textContent = `# ${tag} · ${count}`;
      chip.addEventListener('click', () => {
        state.activeTag = (state.activeTag === tag) ? null : tag;
        render();
      });
      tagBar.appendChild(chip);
    }
    tagBar.hidden = false;
  } else {
    tagBar.hidden = true;
  }

  // wipe gallery contents (keep empty-state node)
  [...gallery.querySelectorAll('.item, .month-header')].forEach(n => n.remove());
  $('#empty-state').hidden = items.length > 0;

  // group by month
  const groups = new Map();
  for (const it of items) {
    const k = monthKey(it.captured_at);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(it);
  }
  const monthKeys = [...groups.keys()].sort().reverse();

  let visibleIdx = 0;
  for (const mk of monthKeys) {
    const hdr = document.createElement('div');
    hdr.className = 'month-header';
    hdr.innerHTML = `<h2>${escapeHtml(monthLabel(mk))} <span class="count">${groups.get(mk).length} 張</span></h2>`;
    gallery.appendChild(hdr);

    for (const it of groups.get(mk)) {
      gallery.appendChild(renderItem(it, visibleIdx));
      visibleIdx++;
    }
  }

  // Refresh tag autocomplete options
  const dl = $('#all-tags');
  if (dl) {
    dl.innerHTML = tags.map(([t]) => `<option value="${escapeHtml(t)}"></option>`).join('');
  }

  syncStateToUrl();
  ensurePolling();
}

// stable pseudo-random tilt/tape variant from filename
function hashStr(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) { h = (h * 31 + s.charCodeAt(i)) | 0; }
  return Math.abs(h);
}
function fmtShortDate(iso) {
  const d = parseDate(iso); if (!d) return '';
  return `${d.getMonth()+1}月${d.getDate()}日`;
}

function renderItem(it, idx) {
  const node = document.createElement('div');
  node.className = 'item polaroid' + (it.favorite ? ' is-favorite' : '')
    + (it.processing ? ' is-processing' : '');
  if (state.selected.has(it.name)) node.classList.add('selected');
  node.dataset.idx = idx;
  node.dataset.name = it.name;

  // deterministic look per item: tilt, tape, and size for scrapbook variety
  const h = hashStr(it.name);
  const tilt = ((h % 60) - 30) / 10;   // -3.0 .. +2.9 degrees
  const tapeVariant = h % 5;           // 0..4
  const sizeKey = (h >> 3) % 7;
  const sizeClass = sizeKey <= 3 ? '' : (sizeKey <= 5 ? 'size-l' : 'size-s');
  if (sizeClass) node.classList.add(sizeClass);
  node.style.setProperty('--tilt', `${tilt}deg`);
  node.dataset.tape = String(tapeVariant);

  // washi tape decoration
  const tape = document.createElement('div');
  tape.className = 'item-tape';
  node.appendChild(tape);

  // photo box
  const photo = document.createElement('div');
  photo.className = 'photo-wrap';

  if (it.kind === 'image') {
    const img = document.createElement('img');
    img.loading = 'lazy';
    img.decoding = 'async';
    img.src = it.thumb_url || it.url;
    img.alt = it.caption || it.name;
    photo.appendChild(img);
  } else {
    const img = document.createElement('img');
    img.loading = 'lazy';
    img.decoding = 'async';
    img.src = it.thumb_url || it.url;
    img.alt = it.caption || it.name;
    photo.appendChild(img);
    const badge = document.createElement('div');
    badge.className = 'video-badge';
    badge.innerHTML = '▶';
    photo.appendChild(badge);
  }
  node.appendChild(photo);

  // bottom strip (date + caption — always visible, handwriting style)
  const strip = document.createElement('div');
  strip.className = 'strip';
  strip.innerHTML = `
    <div class="strip-date">${escapeHtml(fmtShortDate(it.captured_at))}</div>
    <div class="strip-caption">${it.caption ? escapeHtml(it.caption) : '<span class="strip-placeholder">點開寫一句…</span>'}</div>
  `;
  node.appendChild(strip);

  // tag pills (always visible, sit on top of photo)
  if ((it.tags||[]).length) {
    const tagWrap = document.createElement('div');
    tagWrap.className = 'item-tags';
    for (const t of it.tags) {
      const span = document.createElement('span');
      span.className = 'tag';
      span.textContent = '# ' + t;
      tagWrap.appendChild(span);
    }
    node.appendChild(tagWrap);
  }

  // favorite button
  const fav = document.createElement('button');
  fav.className = 'fav-btn';
  fav.title = it.favorite ? '取消收藏' : '收藏';
  fav.textContent = it.favorite ? '❤️' : '🤍';
  fav.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (state.selectMode) return;
    const newFav = !it.favorite;
    if (newFav) spawnHearts(fav);
    try {
      await apiPatchMeta(it.name, { favorite: newFav });
      it.favorite = newFav;
      render();
    } catch { cuteToast('error', '更新失敗', 'error'); }
  });
  node.appendChild(fav);

  // delete button
  const del = document.createElement('button');
  del.className = 'delete-btn';
  del.title = '刪除';
  del.textContent = '🗑';
  del.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (state.selectMode) return;
    if (!confirm(`真的要刪除「${it.name}」嗎？`)) return;
    if (await apiDelete(it.name)) {
      cuteToast('deleted', '已刪除', 'ok');
      await apiList(); render();
    } else cuteToast('error', '刪除失敗', 'error');
  });
  node.appendChild(del);

  // processing overlay (video transcoding in worker)
  if (it.processing) {
    const overlay = document.createElement('div');
    overlay.className = 'processing-overlay';
    overlay.innerHTML = `<div class="processing-spin"><span>🐾</span><span>🐾</span><span>🐾</span></div><div class="processing-label">處理中…</div>`;
    node.appendChild(overlay);
  }

  node.addEventListener('click', () => {
    if (it.processing) {
      toast('影片還在處理中，請稍候');
      return;
    }
    if (state.selectMode) {
      toggleSelect(it.name, node);
    } else {
      openLightbox(idx);
    }
  });

  return node;
}

function toggleSelect(name, node) {
  if (state.selected.has(name)) {
    state.selected.delete(name);
    node.classList.remove('selected');
  } else {
    state.selected.add(name);
    node.classList.add('selected');
  }
  $('#select-count').textContent = `已選 ${state.selected.size} 個`;
}

// ---------- Upload ----------
const dropZone = $('#drop-zone');
const fileInput = $('#file-input');
const uploadList = $('#upload-list');

const uploadModal = $('#upload-modal');

function openUploadModal() {
  uploadModal.hidden = false;
  document.body.style.overflow = 'hidden';
}
function closeUploadModal() {
  uploadModal.hidden = true;
  document.body.style.overflow = '';
  uploadList.hidden = true;
  uploadList.innerHTML = '';
}

$('#browse-btn').addEventListener('click', (e) => { e.stopPropagation(); fileInput.click(); });
dropZone.addEventListener('click', () => fileInput.click());
$('#fab-upload').addEventListener('click', openUploadModal);
$('#upload-modal-close').addEventListener('click', closeUploadModal);
uploadModal.addEventListener('click', (e) => {
  if (e.target === uploadModal) closeUploadModal();
});
document.addEventListener('keydown', (e) => {
  if (!uploadModal.hidden && e.key === 'Escape') closeUploadModal();
});
fileInput.addEventListener('change', () => { handleFiles(fileInput.files); fileInput.value = ''; });

['dragenter', 'dragover'].forEach(ev =>
  dropZone.addEventListener(ev, (e) => { e.preventDefault(); dropZone.classList.add('dragover'); }));
['dragleave', 'drop'].forEach(ev =>
  dropZone.addEventListener(ev, (e) => { e.preventDefault(); dropZone.classList.remove('dragover'); }));
dropZone.addEventListener('drop', (e) => handleFiles(e.dataTransfer.files));

function handleFiles(fileList) {
  const files = [...fileList];
  if (!files.length) return;
  uploadList.hidden = false;
  let done = 0;
  let dupCount = 0;
  files.forEach((f) => {
    const row = document.createElement('div');
    row.className = 'upload-item';
    row.innerHTML = `
      <div class="name">${escapeHtml(f.name)}</div>
      <div class="bar"><span></span></div>
      <div class="status">0%</div>
    `;
    uploadList.appendChild(row);
    uploadOne(f, row).then((result) => {
      done++;
      if (result?.duplicate) {
        dupCount++;
        row.classList.add('done');
        row.querySelector('.status').textContent = '已存在';
      }
      if (done === files.length) {
        setTimeout(async () => {
          await apiList(); render();
          closeUploadModal();
          if (dupCount === files.length) {
            toast(`${dupCount} 個是重複的，已合併`, '');
          } else {
            const phrase = PHRASES.uploaded[Math.floor(Math.random() * PHRASES.uploaded.length)]
              .replace('+N', `+${files.length - dupCount}`);
            toast(dupCount ? `${phrase}（${dupCount} 個重複）` : phrase, 'ok');
          }
        }, 700);
      }
    });
  });
}

function uploadOne(file, row) {
  return new Promise((resolve) => {
    const fd = new FormData();
    fd.append('file', file, file.name);
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/upload');
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const pct = Math.round(e.loaded / e.total * 100);
      row.querySelector('.bar > span').style.width = pct + '%';
      row.querySelector('.status').textContent = pct + '%';
    };
    xhr.onload = () => {
      if (xhr.status === 200) {
        const data = JSON.parse(xhr.responseText || '{}');
        const first = data.saved?.[0];
        if (data.errors?.length) {
          row.classList.add('fail');
          row.querySelector('.status').textContent = '失敗';
          toast(data.errors[0], 'error');
          resolve({});
          return;
        }
        row.classList.add('done');
        row.querySelector('.status').textContent = first?.duplicate ? '已存在'
          : (first?.processing ? '排隊中' : '完成');
        resolve(first || {});
        return;
      }
      row.classList.add('fail');
      row.querySelector('.status').textContent = '失敗';
      toast('上傳失敗 (' + xhr.status + ')', 'error');
      resolve({});
    };
    xhr.onerror = () => {
      row.classList.add('fail');
      row.querySelector('.status').textContent = '錯誤';
      toast('網路錯誤', 'error');
      resolve({});
    };
    xhr.send(fd);
  });
}

// ---------- Filter chips ----------
$$('.chip').forEach(btn => {
  btn.addEventListener('click', () => {
    $$('.chip').forEach(c => c.classList.remove('active'));
    btn.classList.add('active');
    state.filter = btn.dataset.filter;
    render();
  });
});

// ---------- Search ----------
let searchTimer;
$('#search').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.search = e.target.value;
    render();
  }, 120);
});

// ---------- Multi-select ----------
$('#select-mode-btn').addEventListener('click', () => enterSelectMode());
$('#select-cancel-btn').addEventListener('click', () => exitSelectMode());
$('#select-all-btn').addEventListener('click', () => {
  const items = visibleItems();
  const allSelected = items.every(it => state.selected.has(it.name));
  if (allSelected) state.selected.clear();
  else for (const it of items) state.selected.add(it.name);
  render();
  $('#select-count').textContent = `已選 ${state.selected.size} 個`;
});
$('#batch-delete-btn').addEventListener('click', async () => {
  if (!state.selected.size) return;
  if (!confirm(`真的要刪除 ${state.selected.size} 個檔案嗎？`)) return;
  const names = [...state.selected];
  const result = await apiBatchDelete(names);
  if (result) {
    toast(`已刪除 ${result.deleted.length} 個`, 'ok');
    exitSelectMode();
    await apiList(); render();
  } else {
    toast('批次刪除失敗', 'error');
  }
});

function enterSelectMode() {
  state.selectMode = true;
  state.selected.clear();
  document.body.classList.add('select-mode');
  $('#select-bar').hidden = false;
  $('#select-count').textContent = `已選 0 個`;
}
function exitSelectMode() {
  state.selectMode = false;
  state.selected.clear();
  document.body.classList.remove('select-mode');
  $('#select-bar').hidden = true;
  render();
}

// ---------- Lightbox ----------
const lb = $('#lightbox');
const lbContent = $('#lb-content');
const lbDate = $('#lb-date');
const lbCaptionDisplay = $('#lb-caption-display');
const lbCaptionEdit = $('#lb-caption-edit');
const lbFav = $('#lb-fav');
const lbSlideshow = $('#lb-slideshow');
const lbTags = $('#lb-tags');

function openLightbox(idx) {
  state.lbIndex = idx;
  lb.hidden = false;
  document.body.style.overflow = 'hidden';
  showLb();
  syncStateToUrl();
}
function closeLightbox() {
  stopSlideshow();
  lb.hidden = true;
  lbContent.innerHTML = '';
  state.lbIndex = -1;
  document.body.style.overflow = '';
  syncStateToUrl();
}
function currentLbItem() {
  const items = visibleItems();
  if (state.lbIndex < 0 || state.lbIndex >= items.length) return null;
  return items[state.lbIndex];
}
function showLb() {
  const it = currentLbItem();
  if (!it) return closeLightbox();
  lbContent.innerHTML = '';
  const cls = pickEnter();
  if (it.kind === 'image') {
    const img = document.createElement('img');
    img.src = it.url;
    img.className = cls;
    lbContent.appendChild(img);
  } else {
    const v = document.createElement('video');
    v.src = it.url;
    v.controls = true;
    v.autoplay = true;
    v.playsInline = true;
    v.className = cls;
    lbContent.appendChild(v);
  }
  const items = visibleItems();
  lbDate.innerHTML = `<span class="lb-date-text" title="點此修改拍攝時間">${escapeHtml(fmtDateTime(it.captured_at))}</span><span class="lb-date-pos">  ·  ${state.lbIndex + 1} / ${items.length}</span>`;
  if (lbDateEdit) lbDateEdit.hidden = true;
  // caption display (read-only by default, click to edit)
  lbCaptionEdit.hidden = true;
  lbCaptionDisplay.hidden = false;
  if (it.caption) {
    lbCaptionDisplay.textContent = it.caption;
    lbCaptionDisplay.classList.remove('empty');
  } else {
    lbCaptionDisplay.textContent = '';
    lbCaptionDisplay.classList.add('empty');
  }
  lbFav.textContent = it.favorite ? '❤️' : '♡';
  lbFav.classList.toggle('active', !!it.favorite);
  renderLbTags(it);
}

const lbCaptionActions = $('#lb-caption-actions');
function startCaptionEdit() {
  const it = currentLbItem();
  if (!it) return;
  lbCaptionEdit.value = it.caption || '';
  lbCaptionDisplay.hidden = true;
  lbCaptionEdit.hidden = false;
  lbCaptionActions.hidden = false;
  lbCaptionEdit.focus();
}
async function commitCaptionEdit() {
  const it = currentLbItem();
  if (!it) return;
  const newVal = lbCaptionEdit.value;
  try {
    await apiPatchMeta(it.name, { caption: newVal });
    it.caption = newVal;
    cuteToast('saved', '已儲存 ✓', 'ok');
    render();
  } catch { cuteToast('error', '更新失敗', 'error'); }
  lbCaptionActions.hidden = true;
  showLb();
}
function cancelCaptionEdit() {
  lbCaptionEdit.hidden = true;
  lbCaptionActions.hidden = true;
  lbCaptionDisplay.hidden = false;
}
lbCaptionDisplay.addEventListener('click', startCaptionEdit);
$('#lb-caption-save').addEventListener('click', commitCaptionEdit);
$('#lb-caption-cancel').addEventListener('click', cancelCaptionEdit);
lbCaptionEdit.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    e.preventDefault();
    cancelCaptionEdit();
  } else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    commitCaptionEdit();
  }
});

function renderLbTags(it) {
  lbTags.innerHTML = '';
  for (const t of it.tags || []) {
    const span = document.createElement('span');
    span.className = 'tag';
    span.innerHTML = `# ${escapeHtml(t)} <span class="x" title="移除">✕</span>`;
    span.querySelector('.x').addEventListener('click', async () => {
      const newTags = (it.tags || []).filter(x => x !== t);
      try {
        await apiPatchMeta(it.name, { tags: newTags });
        it.tags = newTags;
        renderLbTags(it);
        render();
      } catch { toast('更新失敗', 'error'); }
    });
    lbTags.appendChild(span);
  }
  const addBtn = document.createElement('button');
  addBtn.className = 'add-tag';
  addBtn.textContent = '+ 加標籤';
  addBtn.addEventListener('click', () => {
    addBtn.replaceWith(makeTagInput(it));
  });
  lbTags.appendChild(addBtn);
}

function makeTagInput(it) {
  const inp = document.createElement('input');
  inp.className = 'tag-input';
  inp.placeholder = '輸入標籤 ↵';
  inp.maxLength = 30;
  inp.setAttribute('list', 'all-tags');
  setTimeout(() => inp.focus(), 0);
  const commit = async () => {
    const v = inp.value.trim();
    if (!v) { renderLbTags(it); return; }
    const tags = [...new Set([...(it.tags||[]), v])];
    try {
      await apiPatchMeta(it.name, { tags });
      it.tags = tags;
      renderLbTags(it);
      render();
    } catch { toast('更新失敗', 'error'); renderLbTags(it); }
  };
  inp.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') commit();
    else if (e.key === 'Escape') renderLbTags(it);
  });
  inp.addEventListener('blur', commit);
  return inp;
}

function lbStep(d) {
  const items = visibleItems();
  if (!items.length) return;
  state.lbIndex = (state.lbIndex + d + items.length) % items.length;
  showLb();
  syncStateToUrl();
}

// favorite from lightbox
lbFav.addEventListener('click', async () => {
  const it = currentLbItem();
  if (!it) return;
  const newFav = !it.favorite;
  if (newFav) spawnHearts(lbFav);
  try {
    await apiPatchMeta(it.name, { favorite: newFav });
    it.favorite = newFav;
    lbFav.textContent = newFav ? '❤️' : '♡';
    lbFav.classList.toggle('active', newFav);
    cuteToast(newFav ? 'fav_on' : 'fav_off', newFav ? '已收藏' : '取消收藏', 'ok');
    render();
  } catch { cuteToast('error', '更新失敗', 'error'); }
});

// ---------- Download / Share ----------
const lbDownload = $('#lb-download');
async function downloadOrShare() {
  const it = currentLbItem();
  if (!it || lbDownload.classList.contains('busy')) return;
  lbDownload.classList.add('busy');
  try {
    // Try Web Share API with the actual file first — on iOS this opens
    // the native share sheet ("Save to Photos" / "Save to Files" / AirDrop).
    if (navigator.canShare) {
      try {
        const r = await fetch(it.url);
        if (r.ok) {
          const blob = await r.blob();
          const file = new File([blob], it.name, { type: blob.type || 'application/octet-stream' });
          if (navigator.canShare({ files: [file] })) {
            await navigator.share({ files: [file], title: it.caption || it.name });
            return;
          }
        }
      } catch (err) {
        if (err && err.name === 'AbortError') return;  // user cancelled
        // fall through to direct download
      }
    }
    // Fallback: plain <a download> — works on desktop browsers + Android.
    const a = document.createElement('a');
    a.href = it.url;
    a.download = it.name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    toast('開始下載 ⤓');
  } finally {
    lbDownload.classList.remove('busy');
  }
}
lbDownload.addEventListener('click', (e) => { e.stopPropagation(); downloadOrShare(); });

// ---------- Share link (deep link to this photo) ----------
const lbShare = $('#lb-share');
async function shareLink() {
  const it = currentLbItem();
  if (!it || lbShare.classList.contains('busy')) return;
  lbShare.classList.add('busy');
  try {
    const url = `${location.origin}/#v=${encodeURIComponent(it.name)}`;
    const title = it.caption || '貓貓相簿 🐾';
    const text  = it.caption ? `${it.caption} — 來自貓貓相簿` : '快來看這張喵喵 🐾';

    // Web Share API — iOS Safari / Android Chrome open native share sheet
    if (navigator.share) {
      try {
        await navigator.share({ url, title, text });
        return;
      } catch (e) {
        if (e && e.name === 'AbortError') return;  // user cancelled
        // fall through to clipboard
      }
    }
    // Desktop fallback: copy to clipboard
    try {
      await navigator.clipboard.writeText(url);
      toast('連結已複製 🔗 (' + url.replace(location.origin, '') + ')', 'ok');
      return;
    } catch {}
    // Last resort: prompt with the URL so user can copy manually
    window.prompt('連結（複製這個 URL）：', url);
  } finally {
    lbShare.classList.remove('busy');
  }
}
lbShare.addEventListener('click', (e) => { e.stopPropagation(); shareLink(); });

// slideshow
function startSlideshow() {
  if (state.slideshowTimer) return;
  lbSlideshow.classList.add('playing');
  lb.classList.add('playing');
  lbSlideshow.textContent = '⏸';
  state.slideshowTimer = setInterval(() => lbStep(1), SLIDESHOW_MS);
}
function stopSlideshow() {
  if (!state.slideshowTimer) return;
  clearInterval(state.slideshowTimer);
  state.slideshowTimer = null;
  lbSlideshow.classList.remove('playing');
  lb.classList.remove('playing');
  lbSlideshow.textContent = '▶';
}
lbSlideshow.addEventListener('click', () => {
  if (state.slideshowTimer) stopSlideshow(); else startSlideshow();
});

// navigation
$('.lb-close').addEventListener('click', closeLightbox);
$('.lb-prev').addEventListener('click', () => lbStep(-1));
$('.lb-next').addEventListener('click', () => lbStep(1));
lb.addEventListener('click', (e) => {
  if (e.target === lb) closeLightbox();
});
document.addEventListener('keydown', (e) => {
  if (lb.hidden) return;
  // don't intercept keys when user is typing in caption/tag input
  const tag = document.activeElement && document.activeElement.tagName;
  if (tag === 'TEXTAREA' || tag === 'INPUT') return;
  if (e.key === 'Escape') closeLightbox();
  else if (e.key === 'ArrowLeft') lbStep(-1);
  else if (e.key === 'ArrowRight') lbStep(1);
  else if (e.key === ' ') { e.preventDefault(); if (state.slideshowTimer) stopSlideshow(); else startSlideshow(); }
  else if (e.key === 'f' || e.key === 'F') lbFav.click();
  else if (e.key === 'd' || e.key === 'D') lbDownload.click();
  else if (e.key === 's' || e.key === 'S') lbShare.click();
});

// ---------- Batch meta (favorite / tag) ----------
$('#batch-fav-btn')?.addEventListener('click', async () => {
  if (!state.selected.size) return;
  const names = [...state.selected];
  // Toggle: if every selected is already favorite, unfavorite all; else favorite all.
  const itemsByName = new Map(state.items.map(it => [it.name, it]));
  const allFav = names.every(n => itemsByName.get(n)?.favorite);
  const next = !allFav;
  const result = await apiBatchMeta(names, { favorite: next });
  if (result) {
    toast(`${next ? '已收藏' : '取消收藏'} ${result.updated.length} 個`, 'ok');
    await apiList(); render();
  } else { toast('批次更新失敗', 'error'); }
});

$('#batch-tag-btn')?.addEventListener('click', async () => {
  if (!state.selected.size) return;
  const tag = prompt('輸入要新增的標籤：');
  if (!tag || !tag.trim()) return;
  const names = [...state.selected];
  const result = await apiBatchMeta(names, { tags_add: [tag.trim()] });
  if (result) {
    toast(`已加標籤到 ${result.updated.length} 個`, 'ok');
    await apiList(); render();
  } else { toast('批次更新失敗', 'error'); }
});

// ---------- Lightbox: editable captured_at ----------
const lbDateEdit = $('#lb-date-edit');
function startDateEdit() {
  const it = currentLbItem();
  if (!it) return;
  // datetime-local needs "YYYY-MM-DDTHH:MM"
  let initial = '';
  const d = new Date(it.captured_at);
  if (!isNaN(d)) {
    const p = (n) => String(n).padStart(2, '0');
    initial = `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
  }
  lbDateEdit.value = initial;
  lbDateEdit.hidden = false;
  lbDateEdit.focus();
}
async function commitDateEdit() {
  const it = currentLbItem();
  if (!it || lbDateEdit.hidden) return;
  const raw = lbDateEdit.value;
  if (!raw) { lbDateEdit.hidden = true; return; }
  // Convert datetime-local (local time, no tz) to ISO with seconds.
  const iso = raw.length === 16 ? raw + ':00' : raw;
  try {
    await apiPatchMeta(it.name, { captured_at: iso });
    it.captured_at = iso;
    toast('已更新拍攝時間', 'ok');
    await apiList(); render();
    showLb();
  } catch { toast('更新失敗', 'error'); }
  lbDateEdit.hidden = true;
}
lbDate.addEventListener('click', (e) => {
  // Only the date-text portion should trigger edit, not the position counter.
  if (e.target.closest('.lb-date-text')) startDateEdit();
});
lbDateEdit?.addEventListener('blur', commitDateEdit);
lbDateEdit?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); commitDateEdit(); }
  else if (e.key === 'Escape') { e.preventDefault(); lbDateEdit.hidden = true; }
});

// ---------- Lightbox: swipe gestures (touch) ----------
let _swipeX = 0, _swipeY = 0, _swipeT = 0;
lb.addEventListener('touchstart', (e) => {
  if (e.touches.length !== 1) { _swipeT = 0; return; }
  const t = e.target;
  // Don't hijack interactions on video controls / inputs / buttons.
  if (t.closest('video, input, textarea, button, .lb-tags, .lb-caption-area')) {
    _swipeT = 0; return;
  }
  _swipeX = e.touches[0].clientX;
  _swipeY = e.touches[0].clientY;
  _swipeT = Date.now();
}, { passive: true });
lb.addEventListener('touchend', (e) => {
  if (!_swipeT) return;
  const dt = Date.now() - _swipeT;
  _swipeT = 0;
  if (dt > 600) return;
  const c = e.changedTouches[0];
  const dx = c.clientX - _swipeX;
  const dy = c.clientY - _swipeY;
  const ax = Math.abs(dx), ay = Math.abs(dy);
  const THRESH = 50;
  if (ax > ay && ax > THRESH) {
    if (dx < 0) lbStep(1); else lbStep(-1);
  } else if (dy > THRESH && ay > ax * 1.2) {
    closeLightbox();
  }
}, { passive: true });

// ---------- URL state sync ----------
function syncStateToUrl() {
  if (state.applyingUrl) return;
  const params = new URLSearchParams();
  if (state.filter !== 'all') params.set('filter', state.filter);
  if (state.activeTag) params.set('tag', state.activeTag);
  if (state.search) params.set('q', state.search);
  if (state.lbIndex >= 0) {
    const items = visibleItems();
    const it = items[state.lbIndex];
    if (it) params.set('v', it.name);
  }
  const s = params.toString();
  const newHash = s ? '#' + s : '';
  if (newHash !== location.hash) {
    history.replaceState(null, '', location.pathname + (newHash || ''));
  }
}
function applyUrlState() {
  state.applyingUrl = true;
  try {
    const params = new URLSearchParams(location.hash.slice(1));
    state.filter = params.get('filter') || 'all';
    state.activeTag = params.get('tag') || null;
    state.search = params.get('q') || '';
    $$('.chip').forEach(c => c.classList.toggle('active', c.dataset.filter === state.filter));
    $('#search').value = state.search;
    render();
    const v = params.get('v');
    if (v) {
      const items = visibleItems();
      const i = items.findIndex(it => it.name === v);
      if (i >= 0) { state.lbIndex = i; lb.hidden = false; document.body.style.overflow='hidden'; showLb(); }
    } else if (!lb.hidden) {
      closeLightbox();
    }
  } finally {
    state.applyingUrl = false;
  }
}
window.addEventListener('hashchange', applyUrlState);

// ---------- Polling: refresh while any item is processing ----------
function ensurePolling() {
  const anyProcessing = state.items.some(it => it.processing);
  if (anyProcessing && !state.pollTimer) {
    state.pollTimer = setInterval(async () => {
      await apiList();
      render();
    }, 3000);
  } else if (!anyProcessing && state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

// ---------- Service worker (PWA install on iOS / Android) ----------
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch((err) => {
      console.warn('SW register failed', err);
    });
  });
}

// ---------- Init ----------
(async () => {
  await apiList();
  if (location.hash) applyUrlState();
  else render();
})();
