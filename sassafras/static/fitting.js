'use strict';
/* Optional BUMPS fitting UI. Loaded after app.js; switched on with the "Fit"
   toggle in the header. With the toggle off the page behaves exactly like
   the plain chi-by-eye viewer.

   - each float parameter (and each PD width) gets a "fit" tick box;
   - the slider's min/max boxes are the fit bounds;
   - Fit runs in the background; progress is polled; Cancel keeps the best
     point found so far; Undo restores the pre-fit values;
   - ± uncertainties are shown while the values still match the fit, and
     disappear as soon as anything is changed by hand. */

const FIT_KEY = 'sassafras.fit.v1';
const F = {
  available: false, enabled: false, method: 'lm',
  free: {},          // per sasmodels name: Set of fitted names ("radius", "radius_pd", ...)
  job: null, timer: null, result: null, before: null,
};

function fitStore() {
  try {
    const free = {}; for (const [k, v] of Object.entries(F.free)) free[k] = [...v];
    localStorage.setItem(FIT_KEY, JSON.stringify({ enabled: F.enabled, method: F.method, free }));
  } catch { /* no storage */ }
}
function fitRecall() {
  try {
    const d = JSON.parse(localStorage.getItem(FIT_KEY) || 'null'); if (!d) return;
    F.enabled = !!d.enabled; F.method = d.method || 'lm';
    for (const [k, v] of Object.entries(d.free || {})) F.free[k] = new Set(v);
  } catch { /* no storage */ }
}
const freeSet = () => (F.free[fullName()] ||= new Set());

/* ---------------------------------------------------------------- hooks */
function onState(st) {
  const cap = st.fit || {};
  F.available = !!cap.available;
  fitRecall();
  const t = $('fit-toggle');
  t.disabled = !F.available;
  $('fit-toggle-label').title = F.available ? 'Show BUMPS fitting controls'
    : 'Fitting needs the bumps package (pip install bumps); restart sassafras afterwards.';
  if (!F.available) F.enabled = false;
  t.checked = F.enabled;
  const sel = $('fit-method'); sel.replaceChildren();
  for (const m of cap.methods || []) sel.append(el('option', { value: m.id }, m.label));
  if ([...sel.options].some(o => o.value === F.method)) sel.value = F.method;
  applyFitMode();
}

function onControlsBuilt() {
  const set = freeSet();
  for (const p of S.desc.parameters) {
    if (p.kind !== 'float') continue;
    const wrap = document.querySelector(`.param[data-name="${CSS.escape(p.name)}"]`);
    if (!wrap) continue;
    // fit tick before the name
    const tick = el('input', { type: 'checkbox', class: 'fit-tick fit-only', title: `Vary ${p.name} in the fit (bounds = slider min/max)`, 'aria-label': `Fit ${p.name}` });
    tick.checked = set.has(p.name);
    tick.onchange = () => { tick.checked ? set.add(p.name) : set.delete(p.name); fitStore(); updateFitButtons(); };
    wrap.querySelector('.row1').prepend(tick);
    // uncertainty readout after the value box
    const unc = el('span', { class: 'unc fit-only', 'data-for': p.name });
    wrap.querySelector('.row1 .right').append(unc);
    // PD width tick
    const pdHead = wrap.querySelector('.pd .pdhead');
    if (pdHead) {
      const name = `${p.name}_pd`;
      const lab = el('label', { class: 'fit-only pdfit', title: 'Vary this polydispersity width in the fit (bounds 0–1)' });
      const t2 = el('input', { type: 'checkbox', class: 'fit-tick', 'aria-label': `Fit ${name}` });
      t2.checked = set.has(name);
      t2.onchange = () => { t2.checked ? set.add(name) : set.delete(name); fitStore(); updateFitButtons(); };
      lab.append(t2, document.createTextNode('fit'), el('span', { class: 'unc', 'data-for': name }));
      const slot = wrap.querySelector('.pd .row2 > span:last-child');   // spare grid cell after the 0–0.5 hint
      slot ? slot.replaceWith(lab) : pdHead.append(lab);
    }
  }
  updateFitButtons();   // the Fit button must reflect this model's remembered ticks
  onRendered();
}

function onRendered() {
  const cur = fitCurrent();
  document.querySelectorAll('.unc[data-for]').forEach(s => {
    const k = s.dataset.for;
    const e = cur && F.result.stderr[k];
    s.textContent = cur && k in F.result.stderr ? (e === null || e === undefined ? '± ?' : `± ${fmt(e, 2)}`) : '';
    s.classList.toggle('bound', !!(cur && F.result.at_bound.includes(k)));
    s.title = cur && F.result.at_bound.includes(k) ? 'At a fit bound: widen the slider range and refit' : '';
  });
  const box = $('fit-chi');
  if (F.result) {
    box.textContent = fmt(F.result.chisq_reduced, 4);
    box.classList.toggle('stale', !cur);
    box.title = cur ? `BUMPS reduced χ² = Σ((I−model)/dI)² / (N − p), N = ${F.result.n_points}, p = ${Object.keys(F.result.values).length}`
      : 'Parameters changed since the fit';
  } else { box.textContent = '—'; box.classList.remove('stale'); box.title = ''; }
}

/* --------------------------------------------------------------- state */
function applyFitMode() {
  document.body.classList.toggle('fit-on', F.enabled);
  updateFitButtons();
  onRendered();
}
function fitCurrent() {
  const r = F.result;
  if (!r || r.model !== fullName() || r.dataset !== S.activeId) return false;
  for (const [k, v] of Object.entries(r.values)) {
    const now = k.endsWith('_pd') ? (S.pd[k.slice(0, -3)]?.width ?? 0) : S.params[k];
    if (Math.abs(now - roundSig(v, 8)) > 1e-12 * Math.max(1, Math.abs(v))) return false;
  }
  return true;
}
function fitInfo() {   // used by Save parameters / Copy
  if (!fitCurrent()) return null;
  const r = F.result;
  return { engine: `bumps ${r.bumps}`, method: r.method_label, chisq_reduced: r.chisq_reduced,
    n_points: r.n_points, dof: r.dof, stderr: r.stderr, at_bound: r.at_bound, cancelled: r.cancelled,
    q_window: r.window };
}
function running() { return F.job && ['starting', 'running'].includes(F.job.state); }
function updateFitButtons() {
  const ds = activeDataset();
  const n = freeSet().size;
  $('fit-run').disabled = running() || !ds || !ds.has_di || n === 0;
  $('fit-run').title = !ds ? 'Select an active dataset' : !ds.has_di ? 'This dataset has no dI'
    : n === 0 ? 'Tick the parameters to vary' : `Fit ${n} parameter${n > 1 ? 's' : ''} to ${ds.label}`;
  $('fit-cancel').hidden = !running();
  $('fit-undo').disabled = running() || !F.before;
  $('fit-method').disabled = running();
  $('params').classList.toggle('busy', running());
  $('fit-count').textContent = n ? `${n} free` : '';
}

/* ----------------------------------------------------------------- run */
function fitRequest() {
  const free = {};
  for (const name of freeSet()) {
    if (name.endsWith('_pd')) {
      if (S.pd[name.slice(0, -3)]) free[name] = [0, 1];
    } else if (S.ranges[name]) {
      free[name] = [S.ranges[name].lo, S.ranges[name].hi];
    }
  }
  return { ...request(), method: $('fit-method').value, free };
}
async function startFit() {
  if (running()) return;
  const req = fitRequest();
  F.method = req.method; fitStore();
  // pin the starting curve and remember it for Undo
  F.before = { model: fullName(), params: { ...S.params }, pd: structuredClone(S.pd) };
  if (S.current) {
    S.pinned = { ...S.current, label: 'before fit', params: { ...S.params } };
    if (S.pinned.dataset !== S.activeId) S.pinned.residuals = null;
    $('unpin').disabled = false;
  }
  try {
    F.job = await api('/api/fit/start', req);
    F.job.window = req.window;
    setStatus(`Fitting ${Object.keys(req.free).length} parameter(s) with ${$('fit-method').selectedOptions[0].text}…`);
    updateFitButtons(); render();
    poll();
  } catch (e) { F.job = null; setStatus(e.message, 'error'); updateFitButtons(); }
}
function poll() {
  clearTimeout(F.timer);
  F.timer = setTimeout(async () => {
    try {
      const st = await api('/api/fit/status', { id: F.job.id });
      const win = F.job.window; F.job = st; F.job.window = win;
      if (running()) {
        $('fit-progress').textContent = `step ${st.step}` + (finite(st.chisq_reduced) ? ` · χ²ᵣ ${fmt(st.chisq_reduced, 4)}` : '') + ` · ${st.elapsed_s.toFixed(1)} s`;
        poll();
      } else finishFit(st);
    } catch (e) { setStatus(e.message, 'error'); F.job = null; updateFitButtons(); }
  }, 250);
}
function finishFit(st) {
  $('fit-progress').textContent = '';
  if (st.state === 'error') { setStatus(`Fit failed: ${st.error}`, 'error'); updateFitButtons(); return; }
  const r = st.result;
  for (const [k, v] of Object.entries(r.values)) {
    if (k.endsWith('_pd')) {
      const base = k.slice(0, -3);
      S.pd[base] ||= { width: 0, type: 'gaussian' };
      S.pd[base].width = roundSig(v, 8);
      document.querySelector(`.param[data-name="${CSS.escape(base)}"]`)?.classList.add('pd-open');
    } else {
      S.params[k] = roundSig(v, 8);
      const c = controls[k]; if (c) ensureInRange(c.p, S.ranges[k], S.params[k]);
    }
  }
  F.result = { ...r, model: fullName(), dataset: S.activeId, window: F.job.window };
  syncAll(); updateFitButtons(); queueEval(0);
  const warn = r.at_bound.length ? ` At a bound: ${r.at_bound.join(', ')}.` : '';
  setStatus(`${r.cancelled ? 'Fit cancelled — best point so far' : 'Fit done'} (${r.method_label}, ${st.elapsed_s.toFixed(1)} s): ` +
    `χ²ᵣ = ${fmt(r.chisq_reduced, 4)} with ${Object.keys(r.values).length} free, N = ${r.n_points}.` + warn, warn ? 'warn' : '');
}
async function cancelFit() {
  if (!running()) return;
  try { await api('/api/fit/cancel', { id: F.job.id }); setStatus('Cancelling…'); } catch (e) { setStatus(e.message, 'error'); }
}
function undoFit() {
  const b = F.before; if (!b || running()) return;
  if (b.model !== fullName()) { setStatus('Undo applies to the model that was fitted; switch back to it first.', 'warn'); return; }
  S.params = { ...b.params }; S.pd = structuredClone(b.pd);
  for (const [k, c] of Object.entries(controls)) if (c.p.kind === 'float') ensureInRange(c.p, S.ranges[k], S.params[k]);
  F.before = null;
  syncAll(); updateFitButtons(); queueEval(0);
  setStatus('Restored the values from before the fit.');
}

/* --------------------------------------------------------------- wiring */
$('fit-toggle').onchange = () => { F.enabled = $('fit-toggle').checked; fitStore(); applyFitMode(); };
$('fit-run').onclick = startFit;
$('fit-cancel').onclick = cancelFit;
$('fit-undo').onclick = undoFit;
$('fit-method').onchange = () => { F.method = $('fit-method').value; fitStore(); };
// keep the Fit button state in step with dataset/model changes
const _renderDatasets = renderDatasets;
renderDatasets = function () { _renderDatasets(); updateFitButtons(); };
