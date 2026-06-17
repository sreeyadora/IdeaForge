const API = window.location.origin;

// Schema embedded at generation time — single source of truth.
const SCHEMA_FIELDS = [
  { name: "subject", type: "string", required: false },
  { name: "topic", type: "string", required: false },
  { name: "duration_minutes", type: "integer", required: false },
  { name: "study_date", type: "date", required: false },
  { name: "completed", type: "boolean", required: false },
  { name: "notes", type: "text", required: false }
];

// ── Derived field lists (computed once from SCHEMA_FIELDS) ───────────────────
const NUMERIC_FIELDS  = SCHEMA_FIELDS.filter(f => f.type === 'integer' || f.type === 'float');
const BOOLEAN_FIELDS  = SCHEMA_FIELDS.filter(f => f.type === 'boolean');
const STRING_FIELDS   = SCHEMA_FIELDS.filter(f => f.type === 'string');
// First numeric field used for bar chart values; first string for bar chart labels
const FIRST_NUMERIC   = NUMERIC_FIELDS[0] || null;
const FIRST_STRING    = STRING_FIELDS[0]  || null;
// Category-like field: first string field whose name suggests grouping
const CAT_KEYWORDS    = ['category','status','type','group','tag','priority','label'];
const CAT_FIELD       = STRING_FIELDS.find(f => CAT_KEYWORDS.some(k => f.name.includes(k)))
                        || STRING_FIELDS[1] || FIRST_STRING;

// Update static labels in the analytics bar to match detected fields
if (FIRST_NUMERIC) document.getElementById('stat-numeric-label').textContent =
  'Total ' + FIRST_NUMERIC.name.replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
if (BOOLEAN_FIELDS[0]) document.getElementById('stat-bool-label').textContent =
  BOOLEAN_FIELDS[0].name.replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());

// Chart instances (kept for destroy-on-redraw)
let _barChart = null;
let _pieChart = null;

// ── Utilities ─────────────────────────────────────────────────────────────────

function escHtml(str) {
  return String(str == null ? '' : str)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function showToast(msg) {
  const t = document.createElement('div');
  t.className = 'toast'; t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2800);
}

function readField(field, prefix) {
  const el = document.getElementById(prefix + '-' + field.name);
  if (!el) return undefined;
  if (field.type === 'boolean')  return el.checked;
  if (field.type === 'integer') {
    const v = parseInt(el.value, 10);
    return isNaN(v) ? 0 : v;
  }
  if (field.type === 'float') {
    const v = parseFloat(el.value);
    return isNaN(v) ? 0.0 : v;
  }
  if (field.type === 'date' || field.type === 'datetime') return el.value || '';
  return el.value.trim();
}

function buildPayload(prefix) {
  return SCHEMA_FIELDS.reduce((acc, field) => {
    acc[field.name] = readField(field, prefix);
    return acc;
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
    // Required check (skip booleans — a false checkbox is valid)
    if (field.required && field.type !== 'boolean') {
      if (!raw || !raw.trim()) {
        showToast(label + ' is required');
        el.focus();
        return false;
      }
    }
    // If a value was entered, validate its format
    if (raw && raw.trim()) {
      if (field.type === 'integer' && isNaN(parseInt(raw, 10))) {
        showToast(label + ' must be a whole number');
        el.focus();
        return false;
      }
      if (field.type === 'float' && isNaN(parseFloat(raw))) {
        showToast(label + ' must be a number');
        el.focus();
        return false;
      }
    }
  }
  return true;
}

// ── Analytics ─────────────────────────────────────────────────────────────────

function updateAnalytics(items) {
  document.getElementById('stat-total').textContent = items.length;

  // Sum first numeric field across all items
  if (FIRST_NUMERIC) {
    const total = items.reduce((s, item) => s + (Number(item[FIRST_NUMERIC.name]) || 0), 0);
    document.getElementById('stat-numeric').textContent =
      FIRST_NUMERIC.type === 'float' ? total.toFixed(2) : total;
  }

  // Count truthy boolean values in first boolean field
  if (BOOLEAN_FIELDS[0]) {
    const bfn = BOOLEAN_FIELDS[0].name;
    document.getElementById('stat-bool').textContent =
      items.filter(i => i[bfn]).length;
  }
}

// ── Charts ────────────────────────────────────────────────────────────────────

function updateCharts(items) {
  // Destroy previous chart instances before redrawing
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

  // ── Bar chart: label = first string field, value = first numeric field ──
  const barCtx = document.getElementById('barChart').getContext('2d');
  if (FIRST_STRING && FIRST_NUMERIC) {
    const labels = items.map(i => String(i[FIRST_STRING.name] || '').slice(0, 16));
    const values = items.map(i => Number(i[FIRST_NUMERIC.name]) || 0);
    _barChart = new Chart(barCtx, {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: FIRST_NUMERIC.name.replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase()),
          data: values,
          backgroundColor: 'rgba(99,102,241,0.7)',
          borderColor: '#818cf8',
          borderWidth: 1,
          borderRadius: 4,
        }]
      },
      options: { ...CHART_DEFAULTS, responsive: true, maintainAspectRatio: true },
    });
  } else {
    barCtx.canvas.parentElement.style.display = 'none';
  }

  // ── Pie chart: group by category-like string field ─────────────────────
  const pieCtx = document.getElementById('pieChart').getContext('2d');
  if (CAT_FIELD) {
    const counts = {};
    items.forEach(i => {
      const key = String(i[CAT_FIELD.name] || 'Unknown');
      counts[key] = (counts[key] || 0) + 1;
    });
    const COLORS = ['#6366f1','#f43f5e','#10b981','#f59e0b','#3b82f6','#a78bfa','#06b6d4','#84cc16'];
    _pieChart = new Chart(pieCtx, {
      type: 'pie',
      data: {
        labels: Object.keys(counts),
        datasets: [{
          data: Object.values(counts),
          backgroundColor: COLORS.slice(0, Object.keys(counts).length),
          borderWidth: 2,
          borderColor: '#1e293b',
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        plugins: { legend: { position: 'right', labels: { color: '#94a3b8', font: { size: 10 }, boxWidth: 12 } } },
      },
    });
  } else {
    pieCtx.canvas.parentElement.style.display = 'none';
  }
}

// ── Fetch & render ────────────────────────────────────────────────────────────

async function fetchItems() {
  const loading = document.getElementById('loading');
  const list    = document.getElementById('items-list');
  loading.classList.remove('hidden');
  list.innerHTML = '';
  try {
    const res = await fetch(API + '/items');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const items = await res.json();
    loading.classList.add('hidden');
    if (!items.length) {
      list.innerHTML = '<p class="empty">&#128218; No items yet — add your first one above!</p>';
      updateAnalytics([]);
      updateCharts([]);
      return;
    }
    window._itemCache = {};
    const typeMap = {};
    SCHEMA_FIELDS.forEach(f => { typeMap[f.name] = f.type; });
    const SKIP = new Set(['id', 'created_at']);
    list.innerHTML = items.map(item => {
      window._itemCache[item.id] = item;
      const id = Number(item.id);
      let fieldLines = '';
      Object.entries(item).forEach(([key, value]) => {
        if (SKIP.has(key)) return;
        const label = key.replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
        const ftype = typeMap[key] || 'string';
        let display;
        if (ftype === 'boolean')      display = value ? '&#10003; Yes' : '&#10007; No';
        else if (ftype === 'float')   display = Number(value).toFixed(2);
        else                          display = escHtml(String(value == null ? '' : value));
        fieldLines += `<div class="item-field"><span class="field-key">${label}:</span> `
          + `<span class="field-val">${display}</span></div>`;
      });
      return `
        <div class="item-card">
          <div class="item-info">
            <div class="item-id">#${id}</div>
            ${fieldLines}
          </div>
          <div class="item-actions">
            <button class="btn btn-edit"   onclick="openEdit(${id})">Edit</button>
            <button class="btn btn-danger" onclick="deleteItem(${id})">Delete</button>
          </div>
        </div>`;
    }).join('');
    updateAnalytics(items);
    updateCharts(items);
  } catch (e) {
    loading.classList.add('hidden');
    showToast('Cannot reach backend: ' + e.message);
  }
}

// ── Create ────────────────────────────────────────────────────────────────────

async function createItem() {
  if (!validateForm()) return;
  const payload = buildPayload('inp');
  try {
    const res = await fetch(API + '/items', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const detail = await res.text();
      showToast('Error ' + res.status + ': ' + (detail.slice(0,120) || 'Unknown error'));
      return;
    }
    resetForm();
    showToast('Item created!');
    fetchItems();
  } catch (e) {
    showToast('Create failed: ' + e.message);
  }
}

// ── Delete ────────────────────────────────────────────────────────────────────

async function deleteItem(id) {
  if (!confirm('Delete this item?')) return;
  try {
    const res = await fetch(API + '/items/' + id, { method: 'DELETE' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    showToast('Deleted');
    fetchItems();
  } catch (e) {
    showToast('Delete failed: ' + e.message);
  }
}

// ── Edit modal ────────────────────────────────────────────────────────────────

function openEdit(id) {
  const item = (window._itemCache || {})[id];
  if (!item) { showToast('Item not found'); return; }
  document.getElementById('edit-id').value = id;
  SCHEMA_FIELDS.forEach(field => {
    const el = document.getElementById('edit-' + field.name);
    if (!el) return;
    if (field.type === 'boolean') el.checked = !!item[field.name];
    else el.value = item[field.name] !== undefined ? item[field.name] : '';
  });
  document.getElementById('edit-modal').classList.remove('hidden');
}

function closeModal() {
  document.getElementById('edit-modal').classList.add('hidden');
}

async function saveEdit() {
  const id = document.getElementById('edit-id').value;
  const payload = buildPayload('edit');
  try {
    const res = await fetch(API + '/items/' + id, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const detail = await res.text();
      showToast('Error ' + res.status + ': ' + (detail.slice(0,120) || 'Unknown error'));
      return;
    }
    closeModal();
    showToast('Updated!');
    fetchItems();
  } catch (e) {
    showToast('Update failed: ' + e.message);
  }
}

window.addEventListener('load', fetchItems);
