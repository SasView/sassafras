'use strict';
/* sassafras front end: plain JS + SVG, no external libraries. */

const TOKEN = document.querySelector('meta[name="gui-token"]').content;
const $ = id => document.getElementById(id);
const SVGNS = 'http://www.w3.org/2000/svg';
const SLOTS = 8;            // categorical colours; later datasets fall back to grey
const STORE_KEY = 'sassafras.last.v1';

const S = {
  catalogue: null, datasets: [], activeId: null, visible: new Set(), slotOf: {}, nextSlot: 0,
  model: null, sf: '', desc: null, params: {}, pd: {}, controls: {}, ranges: {}, memory: {},
  current: null, pinned: null, view: null, serial: 0, timer: null, stopped: false,
};

/* ------------------------------------------------------------------ utils */
const fmt = (v, n = 5) => {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  if (v === 0) return '0';
  const a = Math.abs(v);
  const s = (a >= 1e5 || a < 1e-3) ? v.toExponential(n - 1) : v.toPrecision(n);
  return s.replace(/(\.\d*?[1-9])0+(?=e|$)/, '$1').replace(/\.0+(?=e|$)/, '');
};
const fmtFull = v => fmt(v, 10);
const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
const finite = v => typeof v === 'number' && Number.isFinite(v);

function el(tag, attrs = {}, text) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') e.className = v; else if (k === 'style') e.style.cssText = v;
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? '' : v);
  }
  if (text !== undefined) e.textContent = text;
  return e;
}
function sv(tag, attrs = {}, text) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v));
  if (text !== undefined) e.textContent = text;
  return e;
}
function setStatus(text, kind = '') { const s = $('status'); s.textContent = text; s.className = kind; }

async function api(path, body, raw = null, headers = {}) {
  let res;
  try {
    res = await fetch(path, {
      method: 'POST',
      headers: raw ? { 'X-GUI-Token': TOKEN, ...headers } : { 'Content-Type': 'application/json', 'X-GUI-Token': TOKEN },
      body: raw || JSON.stringify(body || {}),
    });
  } catch {
    throw new Error('Lost contact with the sassafras server: it may have stopped. Check its terminal window, then restart it.');
  }
  let out;
  try { out = await res.json(); } catch { throw new Error(`Server returned ${res.status}`); }
  if (!res.ok) throw new Error(out.error || `Request failed (${res.status})`);
  return out;
}
function store() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify({
      model: S.model, sf: S.sf, params: S.params, pd: S.pd, controls: S.controls, ranges: S.ranges,
      logx: $('plot-mode').value === 'iq' ? $('logx').checked : logSaved.logx,
      logy: $('plot-mode').value === 'iq' ? $('logy').checked : logSaved.logy,
      plotMode: $('plot-mode').value,
      grid: [$('grid-min').value, $('grid-max').value, $('grid-n').value],
    }));
  } catch { /* storage unavailable: fine */ }
}
function recall() { try { return JSON.parse(localStorage.getItem(STORE_KEY) || 'null'); } catch { return null; } }
function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = el('a', { href: url, download: name }); document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}
const colourOf = id => {
  const slot = S.slotOf[id];
  return slot < SLOTS ? `var(--s${slot + 1})` : 'var(--ink-3)';
};
const fullName = () => S.sf ? `${S.model}@${S.sf}` : S.model;

/* -------------------------------------------------------------- datasets */
function addDatasets(list, makeActive = true) {
  for (const d of list) {
    const old = S.datasets.findIndex(x => x.id === d.id);
    if (old >= 0) { S.datasets[old] = d; continue; }
    S.datasets.push(d);
    // lowest colour slot not used by a currently loaded dataset (freed slots are reused)
    const used = new Set(S.datasets.filter(x => x.id !== d.id).map(x => S.slotOf[x.id]));
    let slot = 0; while (used.has(slot)) slot++;
    S.slotOf[d.id] = slot;
    S.visible.add(d.id);
  }
  // makeActive: true = newest load becomes active; 'ifnone' = only when nothing is active
  const none = !S.activeId || !S.datasets.some(d => d.id === S.activeId);
  if (list.length && (makeActive === true || (makeActive === 'ifnone' && none))) S.activeId = list[0].id;
  S.view = null;
  renderDatasets();
}
const BKG_KEY = 'sassafras.bkgApplied';
/* Put the active dataset's high-Q background estimate into the background box.
   Done once per newly loaded dataset (remembered per tab, so a page reload
   does not overwrite a background you have since adjusted). */
function suggestBackground(force = false) {
  const ds = activeDataset(), c = controls.background;
  if (!ds || !c || !finite(ds.bkg_estimate)) return false;
  let done = [];
  try { done = JSON.parse(sessionStorage.getItem(BKG_KEY) || '[]'); } catch { /* no storage */ }
  const key = `${ds.id}:${ds.source}`;
  if (!force && done.includes(key)) return false;
  S.params.background = roundSig(ds.bkg_estimate, 4);
  ensureInRange(c.p, S.ranges.background, S.params.background);
  c.sync(); refreshChanged();
  try { sessionStorage.setItem(BKG_KEY, JSON.stringify([...done, key].slice(-200))); } catch { /* no storage */ }
  return true;
}
const bkgNote = () => ` Background estimated at ${fmt(S.params.background, 4)}.`;

function activeDataset() { return S.datasets.find(d => d.id === S.activeId) || null; }

function renderDatasets() {
  const box = $('dataset-list'); box.replaceChildren();
  const none = el('div', { class: 'ds' + (S.activeId ? '' : ' active') });
  const r0 = el('input', { type: 'radio', name: 'active', 'aria-label': 'No active dataset' }); r0.checked = !S.activeId;
  r0.onchange = () => { S.activeId = null; S.view = null; renderDatasets(); queueEval(0); };
  none.append(r0, el('span'), el('div', {}, ''), el('span'));
  none.children[2].append(el('div', { class: 'name' }, 'Model only'));
  box.append(none);
  for (const d of S.datasets) {
    const row = el('div', { class: 'ds' + (d.id === S.activeId ? ' active' : ''), title: d.source + (d.notes.length ? '\n' + d.notes.join('\n') : '') });
    const radio = el('input', { type: 'radio', name: 'active', 'aria-label': `Make ${d.label} active` });
    radio.checked = d.id === S.activeId;
    radio.onchange = () => { S.activeId = d.id; S.visible.add(d.id); S.view = null; renderDatasets(); queueEval(0); };
    const sw = el('input', { type: 'checkbox', class: 'swatch', style: `--c:${colourOf(d.id)}`, 'aria-label': `Show ${d.label}` });
    sw.checked = S.visible.has(d.id);
    sw.onchange = () => { sw.checked ? S.visible.add(d.id) : S.visible.delete(d.id); S.view = null; render(); };
    const text = el('div');
    const res = d.resolution === 'none' ? 'no resolution' : d.resolution;
    text.append(el('div', { class: 'name' }, d.label),
      el('div', { class: 'meta' }, `${d.n} pts · Q ${fmt(d.qmin, 3)}–${fmt(d.qmax, 3)} Å⁻¹ · ${res}${d.has_di ? '' : ' · no dI'}`));
    const acts = el('div', { class: 'acts' });
    const rl = el('button', { title: d.reloadable ? 'Re-read file from disk' : 'Uploaded files cannot be re-read; load by path to enable', 'aria-label': 'Reload' }, '↻');
    rl.disabled = !d.reloadable;
    rl.onclick = async () => {
      try { const out = await api('/api/reload', { id: d.id }); addDatasets([out.dataset], false); setStatus(`Reloaded ${d.label}.`); queueEval(0); }
      catch (e) { setStatus(e.message, 'error'); }
    };
    const rm = el('button', { title: 'Remove', 'aria-label': 'Remove' }, '✕');
    rm.onclick = async () => {
      try {
        await api('/api/remove', { id: d.id });
        S.datasets = S.datasets.filter(x => x.id !== d.id); S.visible.delete(d.id); delete S.slotOf[d.id];
        if (S.activeId === d.id) S.activeId = S.datasets[0]?.id || null;
        if (S.pinned?.dataset === d.id) unpin();
        S.view = null; renderDatasets(); queueEval(0);
      } catch (e) { setStatus(e.message, 'error'); }
    };
    acts.append(rl, rm);
    row.append(radio, sw, text, acts);
    box.append(row);
  }
  const ds = activeDataset();
  $('window-group').hidden = !ds;
  $('grid-group').hidden = !!ds;
  $('match-scale').disabled = $('match-both').disabled = $('bkg-est').disabled = !ds;
}

async function uploadFiles(files) {
  const msgs = [];
  let added = 0;
  for (const f of files) {
    setStatus(`Reading ${f.name}…`);
    try {
      const out = await api('/api/upload', null, await f.arrayBuffer(), { 'X-Filename': encodeURIComponent(f.name) });
      addDatasets(out.added); added += out.added.length; msgs.push(...out.messages);
    } catch (e) { msgs.push(`${f.name}: ${e.message}`); }
  }
  const bk = added && suggestBackground();
  setStatus(`Loaded ${added} dataset(s).` + (bk ? bkgNote() : '') + (msgs.length ? ' ' + msgs.join(' | ') : ''), msgs.length ? 'warn' : '');
  queueEval(0);
}

/* ---------------------------------------------------------------- models */
function populateModels() {
  const filter = $('model-filter').value.trim().toLowerCase();
  const sel = $('model-select'); sel.replaceChildren();
  const groups = {};
  for (const m of S.catalogue.form_factors) {
    if (filter && !(m.name.includes(filter) || m.title.toLowerCase().includes(filter))) continue;
    (groups[m.category] ||= []).push(m);
  }
  const order = ['Spheres', 'Cylinders', 'Ellipsoids', 'Parallelepipeds', 'Lamellae', 'Polyhedra', 'Paracrystals', 'Shape-independent', 'Structure factor only'];
  const cats = Object.keys(groups).sort((a, b) => (order.indexOf(a) + 99 * (order.indexOf(a) < 0)) - (order.indexOf(b) + 99 * (order.indexOf(b) < 0)));
  for (const c of cats) {
    const g = el('optgroup', { label: c });
    for (const m of groups[c]) g.append(el('option', { value: m.name, title: m.title }, m.name));
    sel.append(g);
  }
  if (S.model && [...sel.options].some(o => o.value === S.model)) sel.value = S.model;
  else if (S.model) { const o = el('option', { value: S.model, 'data-current': '1' }, S.model + ' (current)'); sel.prepend(o); sel.value = S.model; }
}
function populateSq() {
  const sel = $('sq-select');
  for (const m of S.catalogue.structure_factors) sel.append(el('option', { value: m.name, title: m.title }, m.name));
}
function modelEntry(name) { return S.catalogue.form_factors.find(m => m.name === name); }

function rememberModel() {
  if (!S.desc) return;
  S.memory[S.desc.model] = { params: { ...S.params }, pd: structuredClone(S.pd), ranges: structuredClone(S.ranges), controls: { ...S.controls } };
}

async function selectModel(model, sf, carry = true, preset = null) {
  rememberModel();
  const entry = modelEntry(model);
  if (!entry) { setStatus(`Unknown model ${model}`, 'error'); return; }
  if (!entry.accepts_sq) sf = '';
  const prev = { params: S.params, pd: S.pd, ranges: S.ranges, desc: S.desc };
  S.model = model; S.sf = sf || '';
  $('model-select').value = model; $('sq-select').value = S.sf; $('sq-select').disabled = !entry.accepts_sq;
  $('sq-select').title = entry.accepts_sq ? '' : 'No S(Q) for this model: it already includes inter-particle structure, or describes no finite particle.';
  const full = fullName();
  const mem = preset || S.memory[full];
  setStatus(`Compiling ${full}…`);
  let desc;
  try { desc = await api('/api/describe', { model, structure_factor: S.sf, controls: mem?.controls || {} }); }
  catch (e) { setStatus(e.message, 'error'); return; }
  S.desc = desc; S.controls = { ...desc.controls };
  const params = {}, ranges = {}, pd = {};
  for (const p of desc.parameters) {
    let v = p.default;
    if (mem && finite(mem.params?.[p.name])) v = mem.params[p.name];
    else if (carry && prev.desc && finite(prev.params[p.name]) && p.kind === 'float') v = prev.params[p.name];
    if (p.kind === 'float') {
      if (p.min !== null && v < p.min) v = p.min;
      if (p.max !== null && v > p.max) v = p.max;
    }
    params[p.name] = v;
    if (p.kind === 'float') {
      const r = mem?.ranges?.[p.name] || (carry && prev.ranges?.[p.name] && v === prev.params[p.name] ? prev.ranges[p.name] : null);
      ranges[p.name] = r ? { ...r } : initialRange(p, v);
      ensureInRange(p, ranges[p.name], v);
    }
    if (p.polydisperse) {
      const src = mem?.pd?.[p.name];  // PD is not carried between models: it is easy to miss
      pd[p.name] = src ? { ...src } : { width: 0, type: 'gaussian' };
    }
  }
  S.params = params; S.ranges = ranges; S.pd = pd;
  $('model-summary').textContent = desc.title.replace(/\.$/, '');
  $('model-summary').title = desc.summary || '';
  buildControls();
  setStatus(`${full} ready` + (desc.compile_ms > 200 ? ` (compiled in ${(desc.compile_ms / 1000).toFixed(1)} s).` : '.'));
  queueEval(0);
}

/* ------------------------------------------------------------ parameters */
function initialRange(p, v) {
  const lo = p.min, hi = p.max;
  if (p.name === 'scale') {
    const c = v > 0 ? v : 1;
    return { lo: c / 1000, hi: Math.min(c * 1000, hi ?? Infinity), log: true };
  }
  if (p.name === 'background') return { lo: Math.min(0, v), hi: Math.max(3 * Math.abs(v), 0.01), log: false };
  if (p.type === 'sld') return { lo: Math.max(lo ?? -Infinity, Math.min(-1, v - 3)), hi: Math.min(hi ?? Infinity, Math.max(8, v + 3)), log: false };
  if (lo !== null && hi !== null) {
    if (lo > 0 && hi / lo > 100) return { lo, hi, log: true };
    return { lo, hi, log: false };
  }
  if (v > 0 && (lo === null ? false : lo >= 0)) {
    return { lo: Math.max(lo > 0 ? lo : 0, v / 10) || v / 10, hi: Math.min(hi ?? Infinity, v * 10), log: true };
  }
  if (v !== 0) {
    const a = Math.abs(v);
    return { lo: Math.max(lo ?? -Infinity, v - 2 * a), hi: Math.min(hi ?? Infinity, v + 2 * a), log: false };
  }
  return { lo: Math.max(lo ?? -Infinity, lo !== null && lo >= 0 ? 0 : -1), hi: Math.min(hi ?? Infinity, 1), log: false };
}
function ensureInRange(p, r, v) {
  if (r.log && !(r.lo > 0)) r.log = false;
  if (v < r.lo || v > r.hi) {
    if (r.log && v > 0) { if (v < r.lo) r.lo = v / 3; else r.hi = v * 3; }
    else { const span = (r.hi - r.lo) || Math.abs(v) || 1; if (v < r.lo) r.lo = v - 0.25 * span; else r.hi = v + 0.25 * span; }
    if (p.min !== null) r.lo = Math.max(r.lo, p.min);
    if (p.max !== null) r.hi = Math.min(r.hi, p.max);
    if (r.log && !(r.lo > 0)) r.log = false;
  }
}
const toPos = (r, v) => {
  const t = r.log ? (Math.log10(v) - Math.log10(r.lo)) / (Math.log10(r.hi) - Math.log10(r.lo)) : (v - r.lo) / (r.hi - r.lo);
  return Math.round(1000 * clamp(Number.isFinite(t) ? t : 0, 0, 1));
};
const fromPos = (r, pos) => {
  const t = pos / 1000;
  return r.log ? 10 ** (Math.log10(r.lo) + t * (Math.log10(r.hi) - Math.log10(r.lo))) : r.lo + t * (r.hi - r.lo);
};
const roundSig = (v, n = 6) => v === 0 ? 0 : Number(v.toPrecision(n));

const controls = {};
function buildControls() {
  const box = $('params'); box.replaceChildren();
  for (const k of Object.keys(controls)) delete controls[k];
  for (const p of S.desc.parameters) box.append(makeControl(p));
  refreshChanged();
  if (typeof onControlsBuilt === 'function') onControlsBuilt();   // fitting.js hook
}
function makeControl(p) {
  const wrap = el('div', { class: 'param', 'data-name': p.name });
  const row1 = el('div', { class: 'row1' });
  const label = el('label', { for: `v-${p.name}`, title: `${p.description}${p.min !== null || p.max !== null ? `\nAllowed: ${p.min ?? '−∞'} to ${p.max ?? '∞'}` : ''}` }, p.name);
  if (p.units) label.append(el('span', { class: 'units' }, prettyUnits(p.units)));
  const right = el('div', { class: 'right' });
  row1.append(label, right);
  wrap.append(row1);

  if (p.kind === 'choice') {
    const sel = el('select', { id: `v-${p.name}`, class: 'choice' });
    p.choices.forEach((c, i) => sel.append(el('option', { value: i }, c)));
    sel.value = String(Math.round(S.params[p.name]));
    sel.onchange = () => { S.params[p.name] = Number(sel.value); refreshChanged(); queueEval(); };
    right.append(sel);
    controls[p.name] = { p, sync: () => { sel.value = String(Math.round(S.params[p.name])); } };
    return wrap;
  }
  if (p.kind === 'control') {
    const num = el('input', { type: 'number', id: `v-${p.name}`, class: 'value', step: 1, min: p.min ?? 0, max: p.max ?? 100 });
    num.value = S.params[p.name];
    num.onchange = () => {
      const v = Math.round(Number(num.value));
      if (!Number.isFinite(v) || (p.min !== null && v < p.min) || (p.max !== null && v > p.max)) { setStatus(`${p.name}: enter an integer from ${p.min} to ${p.max}.`, 'error'); num.value = S.params[p.name]; return; }
      S.params[p.name] = v; S.controls[p.name] = v;
      rememberModel(); S.memory[S.desc.model].controls = { ...S.controls };
      selectModel(S.model, S.sf, true);
    };
    right.append(num);
    controls[p.name] = { p, sync: () => { num.value = S.params[p.name]; } };
    return wrap;
  }

  const r = S.ranges[p.name];
  const num = el('input', { type: 'number', id: `v-${p.name}`, class: 'value', step: 'any', 'aria-label': `${p.name} value` });
  right.append(num);
  let pdBtn = null;
  if (p.polydisperse) {
    pdBtn = el('button', { class: 'toggle', title: 'Polydispersity', 'aria-expanded': 'false' }, 'PD');
    right.prepend(pdBtn);
  }
  const row2 = el('div', { class: 'row2' });
  const lo = el('input', { type: 'number', class: 'lim', step: 'any', 'aria-label': `${p.name} slider minimum` });
  const slider = el('input', { type: 'range', min: 0, max: 1000, step: 1, 'aria-label': `${p.name} slider` });
  const hi = el('input', { type: 'number', class: 'lim', step: 'any', 'aria-label': `${p.name} slider maximum` });
  const logBtn = el('button', { class: 'toggle', title: 'Logarithmic slider' }, 'log');
  row2.append(lo, slider, hi, logBtn);
  wrap.append(row2);

  const sync = () => {
    const rr = S.ranges[p.name];
    num.value = fmtFull(S.params[p.name]);
    lo.value = fmt(rr.lo, 4); hi.value = fmt(rr.hi, 4);
    slider.value = toPos(rr, S.params[p.name]);
    logBtn.classList.toggle('on', rr.log);
    logBtn.disabled = !(rr.lo > 0);
  };
  slider.oninput = () => {
    const v = roundSig(fromPos(S.ranges[p.name], Number(slider.value)));
    S.params[p.name] = v; num.value = fmtFull(v); refreshChanged(); queueEval();
  };
  num.onchange = () => {
    const v = Number(num.value);
    if (num.value === '' || !Number.isFinite(v)) { sync(); return; }
    if ((p.min !== null && v < p.min) || (p.max !== null && v > p.max)) {
      setStatus(`${p.name}: sasmodels allows ${p.min ?? '−∞'} to ${p.max ?? '∞'}.`, 'error'); sync(); return;
    }
    S.params[p.name] = v; ensureInRange(p, S.ranges[p.name], v); sync(); refreshChanged(); queueEval();
  };
  const setLimit = (which, input) => {
    const v = Number(input.value), rr = S.ranges[p.name];
    const ok = Number.isFinite(v) && (which === 'lo' ? v < rr.hi : v > rr.lo)
      && (p.min === null || v >= p.min) && (p.max === null || v <= p.max);
    if (!ok) { setStatus(`${p.name}: slider ${which === 'lo' ? 'minimum' : 'maximum'} must stay inside the allowed range and below/above the other end.`, 'error'); sync(); return; }
    rr[which] = v; if (rr.log && !(rr.lo > 0)) rr.log = false;
    const cur = S.params[p.name];
    if (cur < rr.lo || cur > rr.hi) { S.params[p.name] = clamp(cur, rr.lo, rr.hi); refreshChanged(); queueEval(); }
    sync();
  };
  lo.onchange = () => setLimit('lo', lo);
  hi.onchange = () => setLimit('hi', hi);
  logBtn.onclick = () => { const rr = S.ranges[p.name]; if (rr.lo > 0) { rr.log = !rr.log; sync(); } };

  if (p.polydisperse) {
    const pdBox = el('div', { class: 'pd' });
    const head = el('div', { class: 'pdhead' });
    const type = el('select', { 'aria-label': `${p.name} distribution` });
    for (const t of S.desc.pd_types) type.append(el('option', { value: t }, t));
    head.append(el('span', {}, 'Polydispersity (σ / mean)'), type);
    const pr = el('div', { class: 'row2' });
    const pnum = el('input', { type: 'number', class: 'lim', step: 'any', min: 0, 'aria-label': `${p.name} polydispersity` });
    const pslider = el('input', { type: 'range', min: 0, max: 1000, step: 1, 'aria-label': `${p.name} polydispersity slider` });
    pr.append(pnum, pslider, el('span', { class: 'units', style: 'font-size:11px;color:var(--ink-3)' }, '0–0.5'), el('span'));
    pdBox.append(head, pr);
    wrap.append(pdBox);
    const syncPd = () => {
      const d = S.pd[p.name];
      pnum.value = fmt(d.width, 4); pslider.value = Math.round(clamp(d.width / 0.5, 0, 1) * 1000); type.value = d.type;
      pdBtn.classList.toggle('on', d.width > 0);
    };
    pdBtn.onclick = () => { const open = wrap.classList.toggle('pd-open'); pdBtn.setAttribute('aria-expanded', String(open)); };
    pslider.oninput = () => { S.pd[p.name].width = roundSig(Number(pslider.value) / 1000 * 0.5, 4); syncPd(); queueEval(); };
    pnum.onchange = () => { const v = Number(pnum.value); if (Number.isFinite(v) && v >= 0) { S.pd[p.name].width = v; } syncPd(); queueEval(); };
    type.onchange = () => { S.pd[p.name].type = type.value; queueEval(); };
    if (S.pd[p.name].width > 0) { wrap.classList.add('pd-open'); pdBtn.setAttribute('aria-expanded', 'true'); }
    const base = sync;
    controls[p.name] = { p, sync: () => { base(); syncPd(); } };
  } else {
    controls[p.name] = { p, sync };
  }
  controls[p.name].sync();
  return wrap;
}
function prettyUnits(u) {
  return u.replace(/1e-6\/Ang\^2/g, '10⁻⁶ Å⁻²').replace(/1\/Ang\^2/g, 'Å⁻²').replace(/\/Ang/g, ' Å⁻¹').replace(/Ang/g, 'Å')
    .replace(/\^2/g, '²').replace(/\^3/g, '³').replace(/\^-1/g, '⁻¹').replace(/degrees/g, '°');
}
function refreshChanged() {
  for (const [name, c] of Object.entries(controls)) {
    const w = document.querySelector(`.param[data-name="${CSS.escape(name)}"]`);
    if (w) w.classList.toggle('changed', S.params[name] !== c.p.default);
  }
  // radius_effective is ignored unless radius_effective_mode is "unconstrained" (0)
  const re = document.querySelector('.param[data-name="radius_effective"]');
  if (re && 'radius_effective_mode' in S.params) {
    const tied = S.params.radius_effective_mode !== 0;
    re.classList.toggle('inactive', tied);
    re.title = tied ? 'Ignored: radius_effective_mode computes it from the form factor. Set the mode to "unconstrained" to use this value.' : '';
  }
}
function syncAll() { for (const c of Object.values(controls)) c.sync(); refreshChanged(); }

/* ------------------------------------------------------------ evaluation */
function windowRange() {
  const a = parseFloat($('win-min').value), b = parseFloat($('win-max').value);
  return [Number.isFinite(a) ? a : null, Number.isFinite(b) ? b : null];
}
function gridSpec() {
  return { qmin: parseFloat($('grid-min').value), qmax: parseFloat($('grid-max').value), n: parseInt($('grid-n').value, 10) };
}
function request() {
  const pd = {};
  for (const [k, v] of Object.entries(S.pd)) if (v.width > 0) pd[k] = v;
  return { model: S.model, structure_factor: S.sf || null, params: S.params, pd,
    dataset: S.activeId, grid: gridSpec(), window: windowRange() };
}
function queueEval(delay = 50) {
  if (!S.desc || S.stopped) return;
  const serial = ++S.serial;
  clearTimeout(S.timer);
  S.timer = setTimeout(() => evaluate(serial), delay);
}
async function evaluate(serial) {
  try {
    const out = await api('/api/evaluate', request());
    if (serial !== S.serial) return;
    S.current = out;
    render();
    const w = out.warnings || [];
    if (w.length) setStatus(w.join(' | '), 'warn');
    else if (/^(Compiling|Updating)/.test($('status').textContent) || ($('status').className && !/fit/i.test($('status').textContent))) setStatus(`${out.model}: updated.`);
    store();
  } catch (e) {
    if (serial !== S.serial) return;
    setStatus(e.message, 'error');
  }
}
async function match(withBackground) {
  try {
    const out = await api('/api/match', { ...request(), background: withBackground });
    for (const [name, v] of Object.entries({ scale: out.scale, background: out.background })) {
      const c = controls[name]; if (!c) continue;
      S.params[name] = roundSig(v, 8); ensureInRange(c.p, S.ranges[name], S.params[name]);
    }
    syncAll(); queueEval(0);
    setStatus(`Matched: scale ${fmt(out.scale)}, background ${fmt(out.background)} (linear solve with the shape fixed).`);
  } catch (e) { setStatus(e.message, 'error'); }
}

/* -------------------------------------------------------------- plotting */
const MAIN = { W: 940, H: 420, L: 76, R: 16, T: 12, B: 46 };
const RES = { W: 940, H: 200, L: 76, R: 16, T: 10, B: 40 };
let lastScale = null;

function logTicks(a, b) {        // a,b are log10 bounds
  const out = [], span = b - a;
  const mults = span <= 1.2 ? [1, 1.5, 2, 3, 5, 7] : span <= 3.5 ? [1, 2, 5] : [1];
  const step = span > 10 ? Math.ceil(span / 8) : 1;
  for (let e = Math.floor(a); e <= Math.ceil(b); e += step) for (const m of mults) {
    const v = m * 10 ** e; if (Math.log10(v) >= a - 1e-9 && Math.log10(v) <= b + 1e-9) out.push(v);
  }
  return out;
}
function linTicks(lo, hi, n = 6) {
  const raw = (hi - lo) / n, mag = 10 ** Math.floor(Math.log10(raw)), r = raw / mag;
  const step = (r < 1.5 ? 1 : r < 3 ? 2 : r < 7 ? 5 : 10) * mag;
  const out = []; for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  return out;
}
const tickLabel = v => { const a = Math.abs(v); return (a !== 0 && (a < 1e-3 || a >= 1e5)) ? Number(v.toPrecision(3)).toExponential().replace('e+', 'e') : String(Number(v.toPrecision(4))); };

function shownSets() { return S.datasets.filter(d => S.visible.has(d.id)); }

/* Plot types. x transforms map Q to the plotted abscissa; y transforms map
   (Q, I) to the plotted ordinate. Scales, zoom, hover and the χ² window all
   keep working in Q, so the transforms only touch drawing. */
const PLOT_MODES = {
  iq: { xf: q => q, xinv: x => x, yf: (q, i) => i, xlab: 'Q (Å⁻¹)', ylab: u => `I(Q) (${u})`, log: true, zeroY: true },
  kratky: { xf: q => q, xinv: x => x, yf: (q, i) => q * q * i, xlab: 'Q (Å⁻¹)', ylab: u => `Q²·I(Q) (${u} Å⁻²)`, log: true, zeroY: true },
  guinier: { xf: q => q * q, xinv: x => Math.sqrt(Math.max(x, 0)), yf: (q, i) => (i > 0 ? Math.log(i) : NaN),
    xlab: 'Q² (Å⁻²)', ylab: () => 'ln I(Q)', log: false, zeroY: false },
};
const plotMode = () => PLOT_MODES[$('plot-mode').value] || PLOT_MODES.iq;
const logSaved = { logx: true, logy: true };   // I(Q) log choices, restored when returning to I(Q)
function setPlotMode(mode) {
  const prev = $('plot-mode').dataset.prev || 'iq';
  if (prev === 'iq' && mode !== 'iq') { logSaved.logx = $('logx').checked; logSaved.logy = $('logy').checked; }
  $('plot-mode').value = mode; $('plot-mode').dataset.prev = mode;
  if (mode === 'iq') { $('logx').checked = logSaved.logx; $('logy').checked = logSaved.logy; }
  else if (mode === 'kratky') { $('logx').checked = false; $('logy').checked = false; }
  const can = PLOT_MODES[mode].log;
  $('logx').disabled = $('logy').disabled = !can;
  if (!can) { $('logx').checked = false; $('logy').checked = false; }
  S.view = null;
}
function guinierQmax(d) {
  const n = d.q.length, ok = d.i.map((v, k) => [d.q[k], v]).filter(([, v]) => v > 0);
  const fallback = d.q[Math.min(n - 1, Math.max(9, Math.floor(n / 4)))];
  if (ok.length < 8) return fallback;
  const head = ok.slice(0, 5).map(([, v]) => v).sort((a, b) => a - b), i0 = head[2];
  let k = ok.findIndex(([, v], j) => j >= 5 && v < i0 * Math.exp(-0.8) && (ok[j + 1]?.[1] ?? 0) < i0 * Math.exp(-0.8));
  if (k < 0) return fallback;
  return ok[Math.max(k, 7)][0];
}
const transformed = (q, ys) => { const m = plotMode(); return ys.map((y, k) => (y === null ? null : m.yf(q[k], y))); };

function domains() {
  const m = plotMode();
  const logx = m.log && $('logx').checked, logy = m.log && $('logy').checked;
  const sets = shownSets(), cur = S.current;
  let xs = [];
  for (const d of sets) xs.push(...d.q);
  if (!xs.length && cur) xs = cur.q.slice();
  xs = xs.filter(v => logx ? v > 0 : Number.isFinite(v));
  if (!xs.length) xs = [1e-3, 0.5];
  let x0 = Math.min(...xs), x1 = Math.max(...xs);
  if (!S.view?.x && m === PLOT_MODES.guinier) {
    // Guinier region is at low Q: open where ln I has dropped by ~0.8 from its low-Q value
    // (a little past Q·Rg ≈ 1.3), else on the lowest quarter of the points.
    const ref = activeDataset() && S.visible.has(S.activeId) ? activeDataset() : sets[0];
    if (ref) x1 = guinierQmax(ref);
  }
  if (S.view?.x) [x0, x1] = S.view.x;
  else if (logx) { const pad = (Math.log10(x1) - Math.log10(x0)) * 0.03 || 0.1; x0 /= 10 ** pad; x1 *= 10 ** pad; }
  else if (m === PLOT_MODES.guinier) { x0 = 0; x1 = Math.sqrt(x1 * x1 * 1.04); }
  else { const pad = (x1 - x0) * 0.03 || Math.abs(x0) * 0.1 || 1; x0 -= pad; x1 += pad; if (xs.every(v => v >= 0)) x0 = Math.max(x0, 0); }

  let y0, y1;
  if (S.view?.y) [y0, y1] = S.view.y;
  else {
    let ys = [];
    const inX = q => q >= x0 && q <= x1;
    for (const d of sets) d.q.forEach((q, i) => {
      if (!inX(q)) return;
      ys.push(m.yf(q, d.i[i]));
      if (d.di) { ys.push(m.yf(q, d.i[i] + d.di[i])); if (!logy || d.i[i] - d.di[i] > 0) ys.push(m.yf(q, d.i[i] - d.di[i])); }
    });
    if (!ys.length && cur) cur.q.forEach((q, i) => { if (inX(q) && cur.theory[i] !== null) ys.push(m.yf(q, cur.theory[i])); });
    ys = ys.filter(v => logy ? v > 0 : Number.isFinite(v));
    if (!ys.length) ys = [1e-3, 1];
    y0 = Math.min(...ys); y1 = Math.max(...ys);
    if (logy) { const pad = Math.max((Math.log10(y1) - Math.log10(y0)) * 0.05, 0.05); y0 /= 10 ** pad; y1 *= 10 ** pad; }
    else { const pad = (y1 - y0) * 0.06 || Math.abs(y0) * 0.1 || 1; y0 = m.zeroY && ys.every(v => v >= 0) ? Math.min(0, y0 - pad) : y0 - pad; y1 += pad; }
  }
  return { logx, logy, x0, x1, y0, y1, xf: m.xf, xinv: m.xinv, xlab: m.xlab };
}

function makeScale(dom, box) {
  const xf = dom.xf || (v => v), xinv = dom.xinv || (v => v);
  const fx = v => dom.logx ? Math.log10(xf(v)) : xf(v), fy = v => dom.logy ? Math.log10(v) : v;
  const a = fx(dom.x0), b = fx(dom.x1), c = fy(dom.y0), d = fy(dom.y1);
  const X = v => box.L + (fx(v) - a) / (b - a) * (box.W - box.L - box.R);
  const Y = v => box.T + (d - fy(v)) / (d - c) * (box.H - box.T - box.B);
  const invX = px => { const t = a + (px - box.L) / (box.W - box.L - box.R) * (b - a); return xinv(dom.logx ? 10 ** t : t); };
  const invY = py => { const t = d - (py - box.T) / (box.H - box.T - box.B) * (d - c); return dom.logy ? 10 ** t : t; };
  const okX = v => finite(v) && (!dom.logx || v > 0), okY = v => finite(v) && (!dom.logy || v > 0);
  return { X, Y, invX, invY, okX, okY, a, b, c, d };
}

function pathD(xs, ys, s, okY = s.okY) {
  let d = '', pen = false;
  for (let i = 0; i < xs.length; i++) {
    const y = ys[i];
    if (y === null || !s.okX(xs[i]) || !okY(y)) { pen = false; continue; }
    d += (pen ? 'L' : 'M') + s.X(xs[i]).toFixed(2) + ',' + s.Y(y).toFixed(2);
    pen = true;
  }
  return d;
}

function drawFrame(svg, box, s, dom, xTicks, yTicks, yLabel, yfmt = tickLabel) {
  const clipId = svg.id + '-clip';
  const defs = sv('defs'); const cp = sv('clipPath', { id: clipId });
  cp.append(sv('rect', { x: box.L, y: box.T, width: box.W - box.L - box.R, height: box.H - box.T - box.B }));
  defs.append(cp); svg.append(defs);
  const xf = dom.xf || (v => v);
  // one notation per axis: all-exponential when every non-zero tick is small
  const xv = xTicks.map(xf), small = xv.some(v => v !== 0) && xv.every(v => Math.abs(v) < 1e-2 || v === 0) && !dom.logx;
  const xlabel = v => (small && v !== 0 ? Number(v.toPrecision(3)).toExponential().replace('e+', 'e') : tickLabel(v));
  for (const v of xTicks) { const x = s.X(v); svg.append(sv('line', { x1: x, x2: x, y1: box.T, y2: box.H - box.B, class: 'gridline' })); svg.append(sv('text', { x, y: box.H - box.B + 17, 'text-anchor': 'middle' }, xlabel(xf(v)))); }
  for (const v of yTicks) { const y = s.Y(v); svg.append(sv('line', { x1: box.L, x2: box.W - box.R, y1: y, y2: y, class: 'gridline' })); svg.append(sv('text', { x: box.L - 8, y: y + 4, 'text-anchor': 'end' }, yfmt(v))); }
  svg.append(sv('line', { x1: box.L, x2: box.L, y1: box.T, y2: box.H - box.B, class: 'axisline' }));
  svg.append(sv('line', { x1: box.L, x2: box.W - box.R, y1: box.H - box.B, y2: box.H - box.B, class: 'axisline' }));
  svg.append(sv('text', { x: (box.L + box.W - box.R) / 2, y: box.H - 6, 'text-anchor': 'middle', class: 'axis-title' }, dom.xlab || 'Q (Å⁻¹)'));
  svg.append(sv('text', { transform: `translate(16 ${(box.T + box.H - box.B) / 2}) rotate(-90)`, 'text-anchor': 'middle', class: 'axis-title' }, yLabel));
  const g = sv('g', { 'clip-path': `url(#${clipId})` }); svg.append(g);
  return g;
}
function xTicksFor(dom, s) {
  if (dom.logx) return logTicks(s.a, s.b);
  const xf = dom.xf || (v => v), xinv = dom.xinv || (v => v);
  return linTicks(xf(dom.x0), xf(dom.x1), 8).filter(t => t >= 0 || xf === PLOT_MODES.iq.xf).map(xinv);
}

function drawMasked(g, box, s) {
  const [wa, wb] = windowRange();
  if (!activeDataset()) return;
  const top = box.T, h = box.H - box.T - box.B;
  if (wa !== null && s.okX(wa)) { const x = clamp(s.X(wa), box.L, box.W - box.R); g.append(sv('rect', { x: box.L, y: top, width: x - box.L, height: h, class: 'masked' })); }
  if (wb !== null && s.okX(wb)) { const x = clamp(s.X(wb), box.L, box.W - box.R); g.append(sv('rect', { x, y: top, width: box.W - box.R - x, height: h, class: 'masked' })); }
}
function inWindow(q) { const [a, b] = windowRange(); return (a === null || q >= a) && (b === null || q <= b); }

function render() {
  renderMain(); renderResiduals(); renderLegend(); renderScore();
  if (typeof onRendered === 'function') onRendered();             // fitting.js hook
}
function renderMain() {
  const svg = $('main-plot'); svg.replaceChildren();
  const dom = domains(), s = makeScale(dom, MAIN);
  lastScale = { dom, s };
  const yTicks = dom.logy ? logTicks(s.c, s.d) : linTicks(dom.y0, dom.y1, 6);
  const ds0 = activeDataset();
  const yUnit = ds0?.i_unit ? prettyUnits(ds0.i_unit.replace('cm^{-1}', 'cm⁻¹')) : 'cm⁻¹';
  const m = plotMode();
  const g = drawFrame(svg, MAIN, s, dom, xTicksFor(dom, s), yTicks, m.ylab(yUnit));
  drawMasked(g, MAIN, s);
  // data: inactive sets first so the active one sits on top
  const sets = shownSets().sort((a, b) => (a.id === S.activeId) - (b.id === S.activeId));
  for (const d of sets) {
    const colour = colourOf(d.id), active = d.id === S.activeId;
    const eg = sv('g', { stroke: colour });
    const pg = sv('g', { fill: colour });
    for (let i = 0; i < d.q.length; i++) {
      const q = d.q[i], y = m.yf(q, d.i[i]);
      if (!s.okX(q) || !s.okY(y)) continue;
      const out = active && !inWindow(q) ? ' out' : '';
      const x = s.X(q);
      if (d.di) {
        const lo = m.yf(q, d.i[i] - d.di[i]), hi = m.yf(q, d.i[i] + d.di[i]);
        const ylo = s.okY(lo) ? s.Y(lo) : MAIN.H - MAIN.B, yhi = s.Y(hi);
        eg.append(sv('line', { x1: x, x2: x, y1: ylo, y2: yhi, class: 'err' + out }));
      }
      pg.append(sv('circle', { cx: x, cy: s.Y(y), r: active ? 2.8 : 2.2, class: 'pt' + out }));
    }
    g.append(eg, pg);
  }
  if (S.pinned) g.append(sv('path', { d: pathD(S.pinned.q, transformed(S.pinned.q, S.pinned.theory), s), class: 'pinned' }));
  if (typeof drawModels === 'function' && drawModels(g, s)) { /* multi.js drew every member's curve */ }
  else if (S.current) g.append(sv('path', { d: pathD(S.current.q, transformed(S.current.q, S.current.theory), s), class: 'model' }));
  const hover = sv('g', { id: 'hover-layer' }); g.append(hover);
}
function renderResiduals() {
  const ds = activeDataset(), cur = S.current;
  const show = ds && ds.has_di && cur && cur.dataset === ds.id && cur.residuals;
  $('resid-wrap').hidden = !show;
  if (!show) return;
  const svg = $('resid-plot'); svg.replaceChildren();
  const dom0 = lastScale.dom;
  const r = cur.residuals;
  let peak = 3;
  ds.q.forEach((q, i) => { if (q >= dom0.x0 && q <= dom0.x1 && inWindow(q) && r[i] !== null) peak = Math.max(peak, Math.abs(r[i])); });
  peak = Math.min(peak * 1.08, 1e6);
  const dom = { ...dom0, logy: false, y0: -peak, y1: peak };
  const s = makeScale(dom, RES);
  const yt = linTicks(-peak, peak, 4);
  const g = drawFrame(svg, RES, s, dom, xTicksFor(dom, s), yt, 'ΔI / dI', v => tickLabel(v));
  g.append(sv('rect', { x: RES.L, y: s.Y(1), width: RES.W - RES.L - RES.R, height: s.Y(-1) - s.Y(1), class: 'band' }));
  g.append(sv('line', { x1: RES.L, x2: RES.W - RES.R, y1: s.Y(0), y2: s.Y(0), class: 'zero' }));
  drawMasked(g, RES, s);
  if (S.pinned?.residuals && S.pinned.dataset === ds.id) g.append(sv('path', { d: pathD(ds.q, S.pinned.residuals, s, finite), class: 'pinned' }));
  const colour = colourOf(ds.id), pg = sv('g', { fill: colour });
  if (typeof drawResiduals === 'function') drawResiduals(g, s, peak);   // multi.js: the other members
  ds.q.forEach((q, i) => { if (r[i] !== null && s.okX(q)) pg.append(sv('circle', { cx: s.X(q), cy: s.Y(clamp(r[i], -peak, peak)), r: 2.4, class: 'pt' + (inWindow(q) ? '' : ' out') })); });
  g.append(pg);
}
function renderLegend() {
  const box = $('legend'); box.replaceChildren();
  for (const d of shownSets()) {
    const k = el('span', { class: 'key', style: `--c:${colourOf(d.id)}` }); k.append(el('span', { class: 'dot' }), document.createTextNode(d.label + (d.id === S.activeId ? ' (active)' : '')));
    box.append(k);
  }
  if (S.current) {
    const label = typeof multiLegend === 'function' && multiLegend();
    const k = el('span', { class: 'key', style: '--c:var(--model)' }); k.append(el('span', { class: 'mark' }), document.createTextNode(label || `Model: ${S.current.model}`)); box.append(k);
  }
  if (S.pinned) { const k = el('span', { class: 'key', style: '--c:var(--pinned)' }); k.append(el('span', { class: 'mark dash' }), document.createTextNode(`Pinned: ${S.pinned.label}`)); box.append(k); }
}
function renderScore() {
  const cur = S.current, ds = activeDataset();
  const has = cur && ds && cur.dataset === ds.id && cur.chisq !== null && cur.chisq !== undefined;
  $('chisq').textContent = has ? fmt(cur.chisq, 4) : '—';
  $('npts').textContent = has ? String(cur.chisq_n) : (ds && !ds.has_di ? 'no dI' : '—');
  $('timing').textContent = cur ? `${cur.elapsed_ms.toFixed(cur.elapsed_ms < 10 ? 1 : 0)} ms` : '—';
  const pinShown = S.pinned && ds && S.pinned.dataset === ds.id && S.pinned.chisq !== null;
  $('pin-score').hidden = !pinShown;
  if (pinShown) $('pin-chisq').textContent = fmt(S.pinned.chisq, 4);
  if (typeof onScore === 'function') onScore();     // multi.js: combined χ²
}

/* --------------------------------------------------------- hover & zoom */
function svgPoint(svg, evt) {
  const pt = svg.createSVGPoint(); pt.x = evt.clientX; pt.y = evt.clientY;
  return pt.matrixTransform(svg.getScreenCTM().inverse());
}
function nearestIndex(arr, v, logx) {
  let best = -1, bd = Infinity;
  for (let i = 0; i < arr.length; i++) {
    const d = logx ? Math.abs(Math.log10(arr[i]) - Math.log10(v)) : Math.abs(arr[i] - v);
    if (d < bd) { bd = d; best = i; }
  }
  return best;
}
let drag = null;
function setupPlotInteraction() {
  const svg = $('main-plot');
  svg.addEventListener('pointermove', evt => {
    if (!lastScale) return;
    const p = svgPoint(svg, evt), { s, dom } = lastScale;
    if (drag) {
      drag.x1 = clamp(p.x, MAIN.L, MAIN.W - MAIN.R); drag.y1 = clamp(p.y, MAIN.T, MAIN.H - MAIN.B);
      drag.rect.setAttribute('x', Math.min(drag.x0, drag.x1)); drag.rect.setAttribute('y', Math.min(drag.y0, drag.y1));
      drag.rect.setAttribute('width', Math.abs(drag.x1 - drag.x0)); drag.rect.setAttribute('height', Math.abs(drag.y1 - drag.y0));
      return;
    }
    const layer = $('hover-layer'); if (!layer) return;
    layer.replaceChildren();
    if (p.x < MAIN.L || p.x > MAIN.W - MAIN.R || p.y < MAIN.T || p.y > MAIN.H - MAIN.B) return;
    const q = s.invX(p.x), ds = activeDataset(), cur = S.current;
    let text = `Q=${fmt(q, 4)} Å⁻¹`;
    if (ds && cur?.dataset === ds.id) {
      const i = nearestIndex(ds.q, q, dom.logx);
      const x = s.X(ds.q[i]);
      layer.append(sv('line', { x1: x, x2: x, y1: MAIN.T, y2: MAIN.H - MAIN.B, class: 'cross' }));
      const hy = plotMode().yf(ds.q[i], ds.i[i]);
      if (s.okY(hy)) layer.append(sv('circle', { cx: x, cy: s.Y(hy), r: 5, class: 'hl' }));
      text = `#${i + 1}  Q=${fmt(ds.q[i])} Å⁻¹  I=${fmt(ds.i[i])}${ds.di ? ' ± ' + fmt(ds.di[i], 3) : ''}  model=${fmt(cur.theory[i])}` +
        (cur.residuals ? `  (I−model)/dI=${fmt(cur.residuals[i], 3)}` : '') + (inWindow(ds.q[i]) ? '' : '  [outside χ² window]');
    } else if (cur) {
      const i = nearestIndex(cur.q, q, dom.logx);
      const x = s.X(cur.q[i]);
      layer.append(sv('line', { x1: x, x2: x, y1: MAIN.T, y2: MAIN.H - MAIN.B, class: 'cross' }));
      const hy = cur.theory[i] === null ? NaN : plotMode().yf(cur.q[i], cur.theory[i]);
      if (s.okY(hy)) layer.append(sv('circle', { cx: x, cy: s.Y(hy), r: 5, class: 'hl' }));
      text = `Q=${fmt(cur.q[i])} Å⁻¹  model=${fmt(cur.theory[i])}`;
    }
    $('readout').textContent = text;
  });
  svg.addEventListener('pointerleave', () => { const l = $('hover-layer'); if (l) l.replaceChildren(); });
  svg.addEventListener('pointerdown', evt => {
    if (!lastScale || evt.button !== 0) return;
    const p = svgPoint(svg, evt);
    if (p.x < MAIN.L || p.x > MAIN.W - MAIN.R || p.y < MAIN.T || p.y > MAIN.H - MAIN.B) return;
    const rect = sv('rect', { x: p.x, y: p.y, width: 0, height: 0, class: 'zoombox' });
    svg.append(rect);
    drag = { x0: p.x, y0: p.y, x1: p.x, y1: p.y, rect };
    svg.setPointerCapture(evt.pointerId);
  });
  svg.addEventListener('pointerup', () => {
    if (!drag) return;
    const { x0, x1, y0, y1, rect } = drag; drag = null; rect.remove();
    const { s } = lastScale;
    const w = Math.abs(x1 - x0), h = Math.abs(y1 - y0);
    if (w < 6) return;
    const xr = [s.invX(Math.min(x0, x1)), s.invX(Math.max(x0, x1))];
    S.view = { x: xr, y: h >= 6 ? [s.invY(Math.max(y0, y1)), s.invY(Math.min(y0, y1))] : null };
    render();
  });
  svg.addEventListener('dblclick', () => { S.view = null; render(); });
}

/* ------------------------------------------------------- pin & export */
function pin() {
  if (!S.current) return;
  const ds = activeDataset();
  S.pinned = { ...S.current, label: `${S.current.model}${ds ? '' : ''}`, params: { ...S.params } };
  if (S.pinned.dataset !== (ds?.id || null)) S.pinned.residuals = null;
  $('unpin').disabled = false; render();
  setStatus('Current curve pinned as a dashed reference.');
}
function unpin() { S.pinned = null; $('unpin').disabled = true; render(); }

function paramsDoc() {
  const ds = activeDataset(), cur = S.current;
  const pd = {}; for (const [k, v] of Object.entries(S.pd)) if (v.width > 0) pd[k] = v;
  return {
    sassafras: $('versions').dataset.version || '', saved: new Date().toISOString(),
    note: (typeof fitInfo === 'function' && fitInfo()) ? 'Values from a BUMPS fit (see "fit").'
      : 'Manual (chi-by-eye) parameter values; not a fit, no uncertainties.',
    fit: typeof fitInfo === 'function' ? fitInfo() : null,
    model: S.model, structure_factor: S.sf || null, sasmodels_name: fullName(),
    params: S.params, pd, controls: S.controls, slider_ranges: S.ranges,
    dataset: ds ? { label: ds.label, source: ds.source } : null,
    q_window: windowRange(), chisq_per_point: cur?.dataset && cur.chisq !== undefined ? cur.chisq : null,
  };
}
const safeName = s => s.replace(/[^\w.-]+/g, '_').replace(/\.(dat|xml|h5|hdf5|nxs|txt|csv|abs|cor|pdh)$/i, '');
function saveParams() {
  const ds = activeDataset();
  download(`${ds ? safeName(ds.label) + '_' : ''}${fullName().replace('@', '_')}_params.json`, JSON.stringify(paramsDoc(), null, 2), 'application/json');
}
async function loadParams(file) {
  try {
    const doc = JSON.parse(await file.text());
    if (!doc.model || typeof doc.params !== 'object') throw new Error('Not a sassafras parameter file (needs "model" and "params").');
    const sf = doc.structure_factor || '';
    const preset = { params: doc.params, pd: {}, ranges: doc.slider_ranges || {}, controls: doc.controls || {} };
    for (const [k, v] of Object.entries(doc.pd || {})) preset.pd[k] = { width: Number(v.width) || 0, type: v.type || 'gaussian' };
    if (doc.q_window) { $('win-min').value = doc.q_window[0] ?? ''; $('win-max').value = doc.q_window[1] ?? ''; }
    await selectModel(doc.model, sf, false, preset);
    setStatus(`Loaded parameters from ${file.name}.`);
  } catch (e) { setStatus(`Could not load ${file.name}: ${e.message}`, 'error'); }
}
function exportCsv() {
  const cur = S.current; if (!cur) return;
  const ds = S.datasets.find(d => d.id === cur.dataset);
  const doc = paramsDoc();
  let text = `# sassafras model curve (manual parameters, not a fit)\n# model: ${cur.model}\n# params: ${JSON.stringify(doc.params)}\n`;
  if (Object.keys(doc.pd).length) text += `# polydispersity: ${JSON.stringify(doc.pd)}\n`;
  if (doc.fit) text += `# fit: ${JSON.stringify(doc.fit)}\n`;
  if (ds) {
    text += `# dataset: ${ds.label} (${ds.source})\n# resolution: ${ds.resolution}\n# q_window: ${JSON.stringify(doc.q_window)}\n# chisq_per_point: ${cur.chisq}\n`;
    text += 'Q_invA,I,dI,I_model,residual_over_dI,in_window\n';
    ds.q.forEach((q, i) => { text += [q, ds.i[i], ds.di ? ds.di[i] : '', cur.theory[i] ?? '', cur.residuals ? (cur.residuals[i] ?? '') : '', inWindow(q) ? 1 : 0].join(',') + '\n'; });
  } else {
    text += 'Q_invA,I_model\n';
    cur.q.forEach((q, i) => { text += `${q},${cur.theory[i] ?? ''}\n`; });
  }
  download(`${ds ? safeName(ds.label) + '_' : ''}${cur.model.replace('@', '_')}_curve.csv`, text, 'text/csv');
}

/* --------------------------------------------------------------- wiring */
function wire() {
  const drop = $('drop'), input = $('file-input');
  input.onchange = () => { if (input.files.length) uploadFiles([...input.files]); input.value = ''; };
  for (const ev of ['dragenter', 'dragover']) drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); });
  for (const ev of ['dragleave', 'drop']) drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); });
  drop.addEventListener('drop', e => { if (e.dataTransfer.files.length) uploadFiles([...e.dataTransfer.files]); });
  // also accept drops anywhere on the page
  document.addEventListener('dragover', e => e.preventDefault());
  document.addEventListener('drop', e => { if (!drop.contains(e.target)) { e.preventDefault(); if (e.dataTransfer.files.length) uploadFiles([...e.dataTransfer.files]); } });

  $('path-form').onsubmit = async e => {
    e.preventDefault();
    const path = $('path-input').value.trim(); if (!path) return;
    setStatus(`Loading ${path}…`);
    try {
      const out = await api('/api/load_path', { path });
      addDatasets(out.added);
      const bk = out.added.length && suggestBackground();
      setStatus(`Loaded ${out.added.length} dataset(s).` + (bk ? bkgNote() : '') + (out.messages.length ? ' ' + out.messages.join(' | ') : ''), out.messages.length ? 'warn' : '');
      queueEval(0);
    } catch (err) { setStatus(err.message, 'error'); }
  };

  $('model-filter').oninput = populateModels;
  $('model-filter').onkeydown = e => {
    if (e.key === 'Enter') {
      e.preventDefault();
      // exact name first ("cylinder" → cylinder, not barbell), then a name starting with the text, then the first match
      const f = $('model-filter').value.trim().toLowerCase();
      const opts = [...$('model-select').querySelectorAll('option:not([data-current])')];
      const pick = opts.find(o => o.value === f) || opts.find(o => o.value.startsWith(f)) || opts[0];
      if (pick && pick.value !== S.model) selectModel(pick.value, S.sf);
    }
  };
  $('model-select').onchange = () => selectModel($('model-select').value, S.sf);
  $('sq-select').onchange = () => selectModel(S.model, $('sq-select').value);

  $('reset').onclick = () => {
    if (!S.desc) return;
    delete S.memory[S.desc.model];
    for (const p of S.desc.parameters) {
      if (p.kind === 'control') continue;
      S.params[p.name] = p.default;
      if (p.kind === 'float') S.ranges[p.name] = initialRange(p, p.default);
      if (p.polydisperse) S.pd[p.name] = { width: 0, type: 'gaussian' };
    }
    syncAll(); queueEval(0);
  };
  $('match-scale').onclick = () => match(false);
  $('match-both').onclick = () => match(true);
  $('bkg-est').onclick = () => { if (suggestBackground(true)) { setStatus(bkgNote().trim()); queueEval(0); } };
  $('pin').onclick = pin;
  $('unpin').onclick = unpin;
  $('save-params').onclick = saveParams;
  $('load-params').onchange = e => { const f = e.target.files[0]; if (f) loadParams(f); e.target.value = ''; };
  $('export-csv').onclick = exportCsv;

  for (const id of ['logx', 'logy']) $(id).onchange = () => { S.view = null; render(); store(); };
  $('plot-mode').onchange = () => { setPlotMode($('plot-mode').value); render(); store(); };
  $('unzoom').onclick = () => { S.view = null; render(); };
  for (const id of ['win-min', 'win-max']) $(id).onchange = () => queueEval(0);
  for (const id of ['grid-min', 'grid-max', 'grid-n']) $(id).onchange = () => { S.view = null; queueEval(0); };

  $('stop').onclick = async () => {
    S.stopped = true; clearTimeout(S.timer);
    try { await api('/api/stop', {}); } catch { /* already gone */ }
    setStatus('Server stopped. You can close this tab.');
    for (const e of document.querySelectorAll('input,button,select')) e.disabled = true;
  };
  setupPlotInteraction();
  setupTheme();
  setupScheme();
}

const THEME_KEY = 'sassafras.theme';
function applyTheme(mode) {
  if (mode === 'light' || mode === 'dark') document.documentElement.dataset.theme = mode;
  else delete document.documentElement.dataset.theme;
  $('theme').textContent = `Theme: ${mode}`;
}
function setupTheme() {
  let mode = 'auto';
  try { mode = localStorage.getItem(THEME_KEY) || 'auto'; } catch { /* no storage */ }
  applyTheme(mode);
  $('theme').onclick = () => {
    mode = { auto: 'light', light: 'dark', dark: 'auto' }[mode] || 'auto';
    applyTheme(mode);
    try { localStorage.setItem(THEME_KEY, mode); } catch { /* no storage */ }
  };
}

const SCHEME_KEY = 'sassafras.scheme';
const SCHEMES = ['chopped', 'cooked', 'washed', 'chalked', 'girlypop'];
function applyScheme(name) {
  const root = document.documentElement;
  if (name === 'chopped') delete root.dataset.scheme; else root.dataset.scheme = name;
  const tag = name === 'chopped' ? '' : `${name}-`;
  for (const mode of ['light', 'dark']) document.querySelector(`.logo-${mode}`).src = `/static/logo-${tag}${mode}.png`;
  $('favicon').href = `/static/favicon${name === 'chopped' ? '' : '-' + name}.png`;
  $('scheme').textContent = `Colour: ${name}`;
}
function setupScheme() {
  let name = 'chopped';
  try { name = localStorage.getItem(SCHEME_KEY) || 'chopped'; } catch { /* no storage */ }
  if (!SCHEMES.includes(name)) name = 'chopped';
  applyScheme(name);
  $('scheme').onclick = () => {
    name = SCHEMES[(SCHEMES.indexOf(name) + 1) % SCHEMES.length];
    applyScheme(name);
    try { localStorage.setItem(SCHEME_KEY, name); } catch { /* no storage */ }
  };
}

async function start() {
  wire();
  try {
    const res = await fetch('/api/state');
    if (!res.ok) throw new Error('Could not reach the sassafras server.');
    const st = await res.json();
    S.catalogue = st.catalogue;
    if (typeof onState === 'function') onState(st);               // fitting.js hook
    if (typeof onGuessState === 'function') onGuessState(st);     // guess.js hook
    $('versions').textContent = Object.entries(st.versions).map(([k, v]) => `${k} ${v}`).join(' · ');
    $('versions').dataset.version = st.versions.sassafras || '';
    $('ext-hint').textContent = 'canSAS XML · NXcanSAS · ASCII/CSV · IGOR · pdh …';
    $('ext-hint').title = st.extensions.join(' ');
    $('file-input').setAttribute('accept', st.extensions.join(','));
    populateSq();
    addDatasets(st.datasets, 'ifnone');
    const last = recall();
    if (last) {
      $('logx').checked = last.logx !== false; $('logy').checked = last.logy !== false;
      if (last.plotMode && last.plotMode in PLOT_MODES) setPlotMode(last.plotMode);
      if (Array.isArray(last.grid)) [$('grid-min').value, $('grid-max').value, $('grid-n').value] = last.grid;
    }
    let model = st.startup.model, sf = st.startup.structure_factor || '';
    let preset = null;
    if (!model && last?.model && modelEntry(last.model)) {
      model = last.model; sf = last.sf || '';
      preset = { params: last.params || {}, pd: last.pd || {}, ranges: last.ranges || {}, controls: last.controls || {} };
    }
    model = model || 'sphere';
    S.model = model; populateModels();
    await selectModel(model, sf, false, preset);
    if (suggestBackground()) { setStatus(`${fullName()} ready.` + bkgNote()); queueEval(0); }
    if (st.startup.messages?.length) setStatus(st.startup.messages.join(' | '), 'warn');
  } catch (e) { setStatus(e.message, 'error'); }
}
start();
