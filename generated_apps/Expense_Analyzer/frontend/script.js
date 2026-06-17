const API = window.location.origin;

// App metadata embedded at generation time
const APP_TYPE = "Expense Tracker";
console.log('%c IdeaForge ', 'background:#6366f1;color:#fff;font-weight:bold;border-radius:4px', 'App Type:', APP_TYPE);

// Schema embedded at generation time — single source of truth.
const SCHEMA_FIELDS = [
  { name: "title", type: "string", required: false },
  { name: "amount", type: "float", required: false },
  { name: "category", type: "string", required: false },
  { name: "expense_date", type: "date", required: false },
  { name: "is_recurring", type: "boolean", required: false }
];

// ── Derived field lists ───────────────────────────────────────────────────────
const NUMERIC_FIELDS = SCHEMA_FIELDS.filter(f => f.type === 'integer' || f.type === 'float');
const BOOLEAN_FIELDS = SCHEMA_FIELDS.filter(f => f.type === 'boolean');
const STRING_FIELDS  = SCHEMA_FIELDS.filter(f => f.type === 'string' || f.type === 'text');
const FIRST_NUMERIC  = NUMERIC_FIELDS[0] || null;
const FIRST_STRING   = SCHEMA_FIELDS.filter(f => f.type === 'string')[0] || null;
const CAT_KEYWORDS   = ['category','status','type','group','tag','priority','label','billing'];
const CAT_FIELD      = STRING_FIELDS.find(f => CAT_KEYWORDS.some(k => f.name.includes(k)))
                       || STRING_FIELDS[1] || FIRST_STRING;

// Set dynamic analytics labels
if (FIRST_NUMERIC) {
  const label = FIRST_NUMERIC.name.replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
  document.getElementById('stat-numeric-label').textContent = 'Total ' + label;
  document.getElementById('stat-avg-label').textContent = 'Avg ' + label;
}
if (BOOLEAN_FIELDS[0]) {
  document.getElementById('stat-bool-label').textContent =
    BOOLEAN_FIELDS[0].name.replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
}
if (CAT_FIELD) {
  document.getElementById('stat-top-label').textContent =
    'Top ' + CAT_FIELD.name.replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
}

// Chart instances
let _barChart = null;
let _pieChart = null;

// All items cache for client-side filtering
let _allItems = [];

// ── Auth ──────────────────────────────────────────────────────────────────────

async function initAuth() {
  try {
    const res = await fetch(API + '/api/me');
    if (!res.ok) { window.location.href = '/login'; return; }
    const data = await res.json();
    if (!data.authenticated) { window.location.href = '/login'; return; }
    const el = document.getElementById('header-user');
    if (el) el.textContent = '&#128100; ' + data.user;
  } catch(e) { /* offline — don't redirect */ }
}

async function doLogout() {
  await fetch(API + '/logout', { method: 'POST' });
  window.location.href = '/login';
}

// ── Utilities ─────────────────────────────────────────────────────────────────

function escHtml(str) {
  return String(str == null ? '' : str)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function showToast(msg, type) {
  const t = document.createElement('div');
  t.className = 'toast' + (type ? ' toast-' + type : '');
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2800);
}

function readField(field, prefix) {
  const el = document.getElementById(prefix + '-' + field.name);
  if (!el) return undefined;
  if (field.type === 'boolean') return el.checked;
  if (field.type === 'integer') { const v = parseInt(el.value, 10); return isNaN(v) ? 0 : v; }
  if (field.type === 'float')   { const v = parseFloat(el.value);   return isNaN(v) ? 0.0 : v; }
  if (field.type === 'date' || field.type === 'datetime') return el.value || '';
  return el.value.trim();
}

function buildPayload(prefix) {
  return SCHEMA_FIELDS.reduce((acc, field) => {
    acc[field.name] = readField(field, prefix); return acc;
  }, {});
}

function resetForm() {
  SCHEMA_FIELDS.forEach(field => {
    const el = document.getElementById('inp-' + field.name);
    if (!el) return;
    if (field.type === 'boolean') el.checked = false;
    else el.value = '';
  });
}

function validateForm() {
  for (const field of SCHEMA_FIELDS) {
    const el = document.getElementById('inp-' + field.name);
    if (!el) continue;
    const raw = el.value;
    const label = field.name.replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
    if (field.required && field.type !== 'boolean') {
      if (!raw || !raw.trim()) { showToast(label + ' is required', 'error'); el.focus(); return false; }
    }
    if (raw && raw.trim()) {
      if (field.type === 'integer' && isNaN(parseInt(raw, 10))) { showToast(label + ' must be a whole number', 'error'); el.focus(); return false; }
      if (field.type === 'float'   && isNaN(parseFloat(raw)))   { showToast(label + ' must be a number', 'error'); el.focus(); return false; }
    }
  }
  return true;
}

// ── Advanced Analytics ────────────────────────────────────────────────────────

function updateAnalytics(items) {
  document.getElementById('stat-total').textContent = items.length;

  if (FIRST_NUMERIC && items.length) {
    const vals = items.map(i => Number(i[FIRST_NUMERIC.name]) || 0);
    const total = vals.reduce((a, b) => a + b, 0);
    const avg   = total / vals.length;
    const fmt   = v => FIRST_NUMERIC.type === 'float' ? v.toFixed(2) : Math.round(v);
    document.getElementById('stat-numeric').textContent = fmt(total);
    document.getElementById('stat-avg').textContent     = fmt(avg);
  } else {
    document.getElementById('stat-numeric').textContent = '—';
    document.getElementById('stat-avg').textContent     = '—';
  }

  if (BOOLEAN_FIELDS[0] && items.length) {
    const bfn = BOOLEAN_FIELDS[0].name;
    const pct = Math.round(100 * items.filter(i => i[bfn]).length / items.length);
    document.getElementById('stat-bool').textContent = pct + '%';
  } else {
    document.getElementById('stat-bool').textContent = '—';
  }

  if (CAT_FIELD && items.length) {
    const counts = {};
    items.forEach(i => { const k = String(i[CAT_FIELD.name] || 'Unknown'); counts[k] = (counts[k]||0)+1; });
    const top = Object.entries(counts).sort((a,b)=>b[1]-a[1])[0];
    document.getElementById('stat-top').textContent = top ? top[0].slice(0,14) : '—';
  } else {
    document.getElementById('stat-top').textContent = '—';
  }
}

// ── Charts ────────────────────────────────────────────────────────────────────

function updateCharts(items) {
  if (_barChart) { _barChart.destroy(); _barChart = null; }
  if (_pieChart) { _pieChart.destroy(); _pieChart = null; }
  if (!items.length) return;
  const CHART_DEFAULTS = {
    plugins: { legend: { labels: { color: '#94a3b8', font: { size: 11 } } } },
    scales: {
      x: { ticks: { color: '#94a3b8' }, grid: { color: 'rgba(148,163,184,0.08)' } },
      y: { ticks: { color: '#94a3b8' }, grid: { color: 'rgba(148,163,184,0.08)' } },
    },
  };
  const barCtx = document.getElementById('barChart').getContext('2d');
  if (FIRST_STRING && FIRST_NUMERIC) {
    const slice = items.slice(0, 12);
    _barChart = new Chart(barCtx, {
      type: 'bar',
      data: {
        labels: slice.map(i => String(i[FIRST_STRING.name]||'').slice(0,14)),
        datasets: [{ label: FIRST_NUMERIC.name.replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase()),
          data: slice.map(i => Number(i[FIRST_NUMERIC.name])||0),
          backgroundColor: 'rgba(99,102,241,0.7)', borderColor: '#818cf8', borderWidth:1, borderRadius:4 }]
      },
      options: { ...CHART_DEFAULTS, responsive:true, maintainAspectRatio:true },
    });
  } else { barCtx.canvas.parentElement.style.display='none'; }

  const pieCtx = document.getElementById('pieChart').getContext('2d');
  if (CAT_FIELD) {
    const counts = {};
    items.forEach(i => { const k=String(i[CAT_FIELD.name]||'Unknown'); counts[k]=(counts[k]||0)+1; });
    const COLORS = ['#6366f1','#f43f5e','#10b981','#f59e0b','#3b82f6','#a78bfa','#06b6d4','#84cc16'];
    _pieChart = new Chart(pieCtx, {
      type: 'pie',
      data: { labels: Object.keys(counts),
        datasets: [{ data: Object.values(counts),
          backgroundColor: COLORS.slice(0,Object.keys(counts).length),
          borderWidth:2, borderColor:'#1e293b' }] },
      options: { responsive:true, maintainAspectRatio:true,
        plugins: { legend: { position:'right', labels:{ color:'#94a3b8', font:{size:10}, boxWidth:12 } } } },
    });
  } else { pieCtx.canvas.parentElement.style.display='none'; }
}

// ── Search / Filter / Sort ────────────────────────────────────────────────────

function applyFilters() {
  const q    = (document.getElementById('search-input')?.value || '').toLowerCase();
  const sort = document.getElementById('sort-select')?.value || 'newest';
  let items  = [..._allItems];
  if (q) {
    items = items.filter(item =>
      STRING_FIELDS.some(f => String(item[f.name]||'').toLowerCase().includes(q))
    );
  }
  if (sort === 'oldest') items = items.slice().reverse();
  renderItems(items);
  updateAnalytics(items);
  updateCharts(items);
}

// ── Render items list ─────────────────────────────────────────────────────────

function renderItems(items) {
  const list = document.getElementById('items-list');
  if (!items.length) {
    list.innerHTML = '<p class="empty">&#128218; No items match your search.</p>';
    return;
  }
  const typeMap = {};
  SCHEMA_FIELDS.forEach(f => { typeMap[f.name] = f.type; });
  const SKIP = new Set(['id', 'created_at']);
  list.innerHTML = items.map(item => {
    window._itemCache[item.id] = item;
    const id = Number(item.id);
    let fieldLines = '';
    Object.entries(item).forEach(([key, value]) => {
      if (SKIP.has(key)) return;
      const label  = key.replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
      const ftype  = typeMap[key] || 'string';
      let display;
      if (ftype === 'boolean')    display = value ? '&#10003; Yes' : '&#10007; No';
      else if (ftype === 'float') display = Number(value).toFixed(2);
      else                        display = escHtml(String(value == null ? '' : value));
      fieldLines += `<div class="item-field"><span class="field-key">${label}:</span> <span class="field-val">${display}</span></div>`;
    });
    return `<div class="item-card">
      <div class="item-info"><div class="item-id">#${id}</div>${fieldLines}</div>
      <div class="item-actions">
        <button class="btn btn-edit" onclick="openEdit(${id})">Edit</button>
        <button class="btn btn-danger" onclick="deleteItem(${id})">Delete</button>
      </div></div>`;
  }).join('');
}

// ── Fetch & render ────────────────────────────────────────────────────────────

async function fetchItems() {
  const loading = document.getElementById('loading');
  loading.classList.remove('hidden');
  document.getElementById('items-list').innerHTML = '';
  try {
    const res = await fetch(API + '/items');
    if (res.status === 401) { window.location.href = '/login'; return; }
    if (!res.ok) {
      const detail = await res.text();
      loading.classList.add('hidden');
      showToast('Error ' + res.status + ': ' + (detail.slice(0,120) || 'Failed to load'), 'error');
      return;
    }
    const items = await res.json();
    loading.classList.add('hidden');
    _allItems = items;
    window._itemCache = {};
    if (!items.length) {
      document.getElementById('items-list').innerHTML = '<p class="empty">&#128218; No items yet — add your first one above!</p>';
      updateAnalytics([]);
      updateCharts([]);
      return;
    }
    applyFilters();
  } catch (e) {
    loading.classList.add('hidden');
    showToast('Cannot reach backend: ' + e.message, 'error');
  }
}

// ── Create ────────────────────────────────────────────────────────────────────

async function createItem() {
  if (!validateForm()) return;
  const payload = buildPayload('inp');
  try {
    const res = await fetch(API + '/items', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    });
    if (res.status === 401) { window.location.href = '/login'; return; }
    if (!res.ok) {
      const detail = await res.text();
      showToast('Error ' + res.status + ': ' + (detail.slice(0,120) || 'Unknown error'), 'error');
      return;
    }
    resetForm();
    showToast('Item created!', 'success');
    fetchItems();
  } catch (e) { showToast('Create failed: ' + e.message, 'error'); }
}

// ── Delete ────────────────────────────────────────────────────────────────────

async function deleteItem(id) {
  if (!confirm('Delete this item?')) return;
  try {
    const res = await fetch(API + '/items/' + id, { method: 'DELETE' });
    if (res.status === 401) { window.location.href = '/login'; return; }
    if (!res.ok) {
      const detail = await res.text();
      showToast('Error ' + res.status + ': ' + (detail.slice(0,120) || 'Delete failed'), 'error');
      return;
    }
    showToast('Deleted', 'success');
    fetchItems();
  } catch (e) { showToast('Delete failed: ' + e.message, 'error'); }
}

// ── Edit modal ────────────────────────────────────────────────────────────────

function openEdit(id) {
  const item = (window._itemCache || {})[id];
  if (!item) { showToast('Item not found', 'error'); return; }
  document.getElementById('edit-id').value = id;
  SCHEMA_FIELDS.forEach(field => {
    const el = document.getElementById('edit-' + field.name);
    if (!el) return;
    const v = item[field.name];
    if (field.type === 'boolean')      el.checked = !!v;
    else if (field.type === 'integer')  el.value = (v != null) ? String(parseInt(v,10)||0) : '0';
    else if (field.type === 'float')    el.value = (v != null) ? String(parseFloat(v)||0) : '0';
    else                                el.value = (v != null) ? v : '';
  });
  document.getElementById('edit-modal').classList.remove('hidden');
}

function closeModal() { document.getElementById('edit-modal').classList.add('hidden'); }

async function saveEdit() {
  const id = document.getElementById('edit-id').value;
  const payload = buildPayload('edit');
  try {
    const res = await fetch(API + '/items/' + id, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    });
    if (res.status === 401) { window.location.href = '/login'; return; }
    if (!res.ok) {
      const detail = await res.text();
      showToast('Error ' + res.status + ': ' + (detail.slice(0,120) || 'Unknown error'), 'error');
      return;
    }
    closeModal();
    showToast('Updated!', 'success');
    fetchItems();
  } catch (e) { showToast('Update failed: ' + e.message, 'error'); }
}

// ── Export ────────────────────────────────────────────────────────────────────

function exportData(fmt) {
  const a = document.createElement('a');
  a.href = API + '/export/' + fmt;
  a.download = 'export.' + fmt;
  document.body.appendChild(a);
  a.click();
  a.remove();
  showToast('Downloading ' + fmt.toUpperCase() + '...', 'success');
}

// ── Init ──────────────────────────────────────────────────────────────────────

window._itemCache = {};
window.addEventListener('load', async () => {
  await initAuth();
  fetchItems();
});
