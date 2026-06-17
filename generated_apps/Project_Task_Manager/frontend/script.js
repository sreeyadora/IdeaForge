const API = window.location.origin;

// Schema embedded at generation time — single source of truth for field names.
// JS functions loop over this; no field name appears elsewhere in this file.
const SCHEMA_FIELDS = [
  { name: "title", type: "string", required: false },
  { name: "description", type: "text", required: false },
  { name: "priority", type: "string", required: false },
  { name: "status", type: "string", required: false }
];

// ── Utilities ─────────────────────────────────────────────────────────────────

function escHtml(str) {
  return String(str == null ? '' : str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function showToast(msg) {
  const t = document.createElement('div');
  t.className = 'toast'; t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2800);
}

/**
 * Read one field's element value and coerce it to the correct JS type.
 * Prefix is 'inp' (create form) or 'edit' (edit modal).
 */
function readField(field, prefix) {
  const el = document.getElementById(prefix + '-' + field.name);
  if (!el) return undefined;
  if (field.type === 'boolean') return el.checked;
  if (field.type === 'integer') return parseInt(el.value || '0', 10);
  if (field.type === 'float')   return parseFloat(el.value || '0');
  return el.value.trim();
}

/** Build a payload object from SCHEMA_FIELDS using the given element prefix. */
function buildPayload(prefix) {
  return SCHEMA_FIELDS.reduce((acc, field) => {
    acc[field.name] = readField(field, prefix);
    return acc;
  }, {});
}

/** Reset all create-form inputs to empty/default. */
function resetForm() {
  SCHEMA_FIELDS.forEach(field => {
    const el = document.getElementById('inp-' + field.name);
    if (!el) return;
    if (field.type === 'boolean') el.checked = false;
    else el.value = '';
  });
}

/** Validate the first required field. Returns true if OK, false + toast if not. */
function validateForm() {
  for (const field of SCHEMA_FIELDS) {
    if (!field.required) continue;
    const val = readField(field, 'inp');
    const empty = field.type === 'boolean'
      ? false   // booleans are never 'empty'
      : (field.type === 'integer' || field.type === 'float')
        ? isNaN(val) || document.getElementById('inp-' + field.name).value === ''
        : !val;
    if (empty) {
      const label = field.name.replace(/_/g, ' ')
        .replace(/\b\w/g, c => c.toUpperCase());
      showToast(label + ' is required');
      return false;
    }
  }
  return true;
}

// ── Fetch & render ────────────────────────────────────────────────────────────

async function fetchItems() {
  try {
    const res = await fetch(API + '/items');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const items = await res.json();
    const list  = document.getElementById('items-list');
    if (!items.length) {
      list.innerHTML = '<p class="empty">No items yet. Add one above!</p>';
      return;
    }
    window._itemCache = {};
    list.innerHTML = items.map(item => {
      window._itemCache[item.id] = item;
      const id = Number(item.id);
      // Render dynamically from what the server returned.
      // Look up the logical type from SCHEMA_FIELDS for correct formatting.
      const typeMap = {};
      SCHEMA_FIELDS.forEach(f => { typeMap[f.name] = f.type; });
      const SKIP = new Set(['id', 'created_at']);
      let fieldLines = '';
      Object.entries(item).forEach(([key, value]) => {
        if (SKIP.has(key)) return;
        const label = key.replace(/_/g, ' ')
          .replace(/\b\w/g, c => c.toUpperCase());
        const ftype = typeMap[key] || 'string';
        let display;
        if (ftype === 'boolean') display = value ? 'Yes' : 'No';
        else if (ftype === 'float') display = Number(value).toFixed(2);
        else display = escHtml(String(value == null ? '' : value));
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
  } catch (e) {
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
    if (!res.ok) throw new Error('HTTP ' + res.status);
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
  // Populate each edit field from the cached item
  SCHEMA_FIELDS.forEach(field => {
    const el = document.getElementById('edit-' + field.name);
    if (!el) return;
    if (field.type === 'boolean') {
      el.checked = !!item[field.name];
    } else {
      el.value = item[field.name] !== undefined ? item[field.name] : '';
    }
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
    if (!res.ok) throw new Error('HTTP ' + res.status);
    closeModal();
    showToast('Updated!');
    fetchItems();
  } catch (e) {
    showToast('Update failed: ' + e.message);
  }
}

window.addEventListener('load', fetchItems);
