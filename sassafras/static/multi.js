'use strict';
/* "Multi": several datasets at once, in the same page. Loaded last; uses the
   globals of app.js, fitting.js, notebook.js, copy.js and guess.js.

   - Members are the ticked (drawn) datasets; the active (radio) one is the one
     whose values the parameter panel shows. Click another radio to edit it.
   - Every member has its own values, polydispersity and slider ranges. Each
     parameter is either "all" (one value, range and PD for every member) or
     "each" (per dataset); an "each" parameter can instead follow an expression
     such as 0.8 * h2o.scale or (h2o.background + d2o.background) / 2.
   - Every member's model curve is drawn in its colour, χ² is combined, Fit
     runs one joint BUMPS fit, and the IQuana notebook uses MultiFitter. */

const MULTI_KEY = 'sassafras.multi.v1';
const EACH_DEFAULT = n => n === 'scale' || n === 'background' || /solvent/.test(n);
const M = {
  enabled: false,
  snap: {},        // dataset id -> { params, pd, ranges }  (the active one is S.params/S.pd/S.ranges itself)
  model: null,     // fullName() the snapshots belong to
  mode: {},        // fullName() -> { param: 'all' | 'each' }
  expr: {},        // dataset id -> { param: expression }
  curves: {},      // dataset id -> /api/evaluate result
  lastActive: null, before: null, errors: {},
};

function multiStore() {
  try { localStorage.setItem(MULTI_KEY, JSON.stringify({ enabled: M.enabled, mode: M.mode })); } catch { /* no storage */ }
}
function multiRecall() {
  try { const d = JSON.parse(localStorage.getItem(MULTI_KEY) || 'null'); if (d) { M.enabled = !!d.enabled; M.mode = d.mode || {}; } }
  catch { /* no storage */ }
}

/* ------------------------------------------------------------- members */
function multiMembers() {
  if (!M.enabled) return [];
  const ids = S.datasets.filter(d => S.visible.has(d.id)).map(d => d.id);
  return S.activeId && ids.includes(S.activeId) ? ids : [];
}
const multiOn = () => multiMembers().length >= 2;
function aliases() {     // identifiers IQuana accepts, from the file names, unique, in load order
  const out = {}, used = new Set();
  for (const d of S.datasets) {
    let a = d.label.replace(/\.(dat|xml|h5|hdf5|nxs|txt|csv|abs|cor|pdh|ses)$/i, '').replace(/[^A-Za-z0-9_]+/g, '_').replace(/^_+|_+$/g, '');
    if (!/^[A-Za-z]/.test(a)) a = 'd_' + a;
    a = a.slice(0, 24) || 'data';
    let k = a, i = 2; while (used.has(k)) k = `${a}_${i++}`;
    used.add(k); out[d.id] = k;
  }
  return out;
}
const modeOf = name => (M.mode[fullName()] ||= {})[name] || (EACH_DEFAULT(name) ? 'each' : 'all');
const floatParams = () => (S.desc?.parameters || []).filter(p => p.kind === 'float');

function cloneSnap(src) { return { params: { ...src.params }, pd: structuredClone(src.pd), ranges: structuredClone(src.ranges) }; }
function ensureSnaps() {
  M.snap[S.activeId] = { params: S.params, pd: S.pd, ranges: S.ranges };
  for (const id of multiMembers()) if (!M.snap[id]) M.snap[id] = cloneSnap(M.snap[S.activeId]);
}

/* After a model change the active member already has the new parameter set;
   rebuild the others from it, keeping their own values of "each" parameters. */
function rebuildSnaps() {
  const full = fullName();
  if (M.model === full) { ensureSnaps(); return; }
  const old = M.snap; M.snap = {};
  ensureSnaps();
  for (const id of multiMembers()) {
    if (id === S.activeId) continue;
    const s = cloneSnap(M.snap[S.activeId]), o = old[id];
    if (o) for (const p of floatParams()) {
      if (modeOf(p.name) === 'each' && finite(o.params[p.name])) {
        s.params[p.name] = o.params[p.name];
        if (o.ranges[p.name]) s.ranges[p.name] = { ...o.ranges[p.name] };
        ensureInRange(p, s.ranges[p.name], s.params[p.name]);
      }
    }
    M.snap[id] = s;
  }
  M.model = full;
}

/* "all" parameters: copy the active member's value, range and PD to the others */
function syncShared() {
  ensureSnaps();
  const a = M.snap[S.activeId];
  for (const p of S.desc?.parameters || []) {
    if (p.kind === 'float' && modeOf(p.name) === 'each') continue;
    for (const id of multiMembers()) {
      if (id === S.activeId) continue;
      const s = M.snap[id];
      s.params[p.name] = a.params[p.name];
      if (a.ranges[p.name]) s.ranges[p.name] = { ...a.ranges[p.name] };
      if (a.pd[p.name]) s.pd[p.name] = { ...a.pd[p.name] };
    }
  }
}

/* ----------------------------------------------------------- expressions */
function tokenize(text) {
  const out = [], re = /\s*(?:(\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?)|([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)|(\*\*|[-+*/^()]))/y;
  let m, i = 0;
  text = text.trim();
  while (i < text.length) {
    re.lastIndex = i; m = re.exec(text);
    if (!m || m[0].length === 0) throw new Error(`cannot read "${text.slice(i)}"`);
    out.push(m[1] !== undefined ? { t: 'num', v: Number(m[1]) } : m[2] !== undefined ? { t: 'ref', v: m[2] } : { t: 'op', v: m[3] === '**' ? '^' : m[3] });
    i = re.lastIndex;
  }
  return out;
}
function parseExpr(text) {    // → AST; same grammar as sassafras/multi.py
  const tk = tokenize(text); let k = 0;
  const peek = () => tk[k], take = v => { if (tk[k]?.v === v) { k++; return true; } return false; };
  const atom = () => {
    const t = tk[k++];
    if (!t) throw new Error('unexpected end');
    if (t.t === 'num') return { n: t.v };
    if (t.t === 'ref') return { r: t.v };
    if (t.v === '(') { const e = sum(); if (!take(')')) throw new Error('missing )'); return e; }
    if (t.v === '-') return { neg: unary() };
    if (t.v === '+') return unary();
    throw new Error(`unexpected "${t.v}"`);
  };
  const unary = () => atom();
  const power = () => {
    const b = unary();
    if (take('^')) {
      const e = power();
      const ev = e.n ?? (e.neg && e.neg.n !== undefined ? -e.neg.n : NaN);
      if (!Number.isInteger(ev)) throw new Error('only integer powers are allowed');
      return { op: '^', a: b, b: e };
    }
    return b;
  };
  const prod = () => { let a = power(); for (;;) { const o = peek()?.v; if (o === '*' || o === '/') { k++; a = { op: o, a, b: power() }; } else return a; } };
  const sum = () => { let a = prod(); for (;;) { const o = peek()?.v; if (o === '+' || o === '-') { k++; a = { op: o, a, b: prod() }; } else return a; } };
  const ast = sum();
  if (k < tk.length) throw new Error(`unexpected "${tk[k].v}"`);
  return ast;
}
function refsOf(ast, own, out = []) {
  if (ast.r !== undefined) { const [a, n] = ast.r.includes('.') ? ast.r.split('.') : [own, ast.r]; out.push([a, n]); }
  for (const c of [ast.a, ast.b, ast.neg]) if (c) refsOf(c, own, out);
  return out;
}
function evalExpr(ast, own, look) {
  if (ast.n !== undefined) return ast.n;
  if (ast.r !== undefined) { const [a, n] = ast.r.includes('.') ? ast.r.split('.') : [own, ast.r]; return look(a, n); }
  if (ast.neg) return -evalExpr(ast.neg, own, look);
  const x = evalExpr(ast.a, own, look), y = evalExpr(ast.b, own, look);
  return ast.op === '+' ? x + y : ast.op === '-' ? x - y : ast.op === '*' ? x * y : ast.op === '/' ? x / y : x ** y;
}
function liveExprs() {   // [{id, alias, name, text, ast}] for members, "each" parameters only
  const al = aliases(), out = [];
  for (const id of multiMembers()) for (const [name, text] of Object.entries(M.expr[id] || {})) {
    if (!text || modeOf(name) !== 'each' || !(name in (M.snap[id]?.params || {}))) continue;
    out.push({ id, alias: al[id], name, text });
  }
  return out;
}
/* Put the expression values into the members' parameters; errors go to M.errors. */
function resolveAll() {
  const al = aliases(), byAlias = {};
  for (const id of multiMembers()) byAlias[al[id]] = id;
  const look = (a, n) => {
    const id = byAlias[a];
    if (!id) throw new Error(`no ticked dataset "${a}"`);
    const s = M.snap[id];
    if (n.endsWith('_pd') && s.pd[n.slice(0, -3)]) return s.pd[n.slice(0, -3)].width;
    if (!(n in s.params)) throw new Error(`${a} has no parameter "${n}"`);
    return s.params[n];
  };
  M.errors = {};
  const list = liveExprs();
  for (const e of list) {
    try { e.ast = parseExpr(e.text); if (refsOf(e.ast, e.alias).some(([a, n]) => a === e.alias && n === e.name)) throw new Error('refers to itself'); }
    catch (err) { M.errors[`${e.id}:${e.name}`] = err.message; e.ast = null; }
  }
  const ok = list.filter(e => e.ast);
  for (let pass = 0; pass <= ok.length; pass++) {       // enough passes for any acyclic chain
    let changed = false;
    for (const e of ok) {
      try {
        const v = evalExpr(e.ast, e.alias, look);
        if (!Number.isFinite(v)) throw new Error('not a finite number');
        if (M.snap[e.id].params[e.name] !== v) { M.snap[e.id].params[e.name] = v; changed = true; }
      } catch (err) { M.errors[`${e.id}:${e.name}`] = err.message; }
    }
    if (!changed) break;
    if (pass === ok.length) for (const e of ok) M.errors[`${e.id}:${e.name}`] ||= 'circular expressions';
  }
}

/* ---------------------------------------------------------------- panel */
function decorateParams() {
  document.querySelectorAll('.multi-ctl, .multi-expr').forEach(n => n.remove());
  if (!S.desc) return;
  for (const p of floatParams()) {
    const wrap = document.querySelector(`.param[data-name="${CSS.escape(p.name)}"]`);
    if (!wrap) continue;
    const each = modeOf(p.name) === 'each';
    const btn = el('button', { class: 'toggle multi-ctl multi-only' + (each ? ' on' : ''),
      title: each ? 'Per dataset: each ticked dataset has its own value (click the dataset radio to edit it). Click for one shared value.'
        : 'Shared: one value, range and polydispersity for all ticked datasets. Click to set it per dataset.' }, each ? 'each' : 'all');
    btn.onclick = () => {
      const m = (M.mode[fullName()] ||= {});
      m[p.name] = each ? 'all' : 'each';
      if (!each) syncShared(); else { syncShared(); }   // going shared: the active value wins
      multiStore(); decorateParams(); queueEval(0);
    };
    wrap.querySelector('.row1 .right').prepend(btn);
    if (each) {
      const text = (M.expr[S.activeId] || {})[p.name] || '';
      const fx = el('button', { class: 'toggle multi-ctl multi-only fx' + (text ? ' on' : ''),
        title: 'Set this dataset\'s value by an expression of other parameters, e.g. 0.8 * h2o.scale' }, 'ƒ');
      btn.after(fx);
      const row = el('div', { class: 'multi-expr multi-only' + (text ? '' : ' closed') });
      fx.onclick = () => { row.classList.toggle('closed'); if (!row.classList.contains('closed')) row.querySelector('input').focus(); };
      const inp = el('input', { type: 'text', spellcheck: 'false', placeholder: 'value, or expression e.g. 0.8 * h2o.scale',
        'aria-label': `${p.name} expression for this dataset`, value: text });
      inp.onchange = () => {
        (M.expr[S.activeId] ||= {})[p.name] = inp.value.trim();
        fx.classList.toggle('on', !!inp.value.trim());
        queueEval(0);
      };
      row.append(el('span', {}, 'ƒ ='), inp);
      wrap.append(row);
      wrap.classList.toggle('derived', !!text);
    } else wrap.classList.remove('derived');
  }
  showExprErrors();
}
function showExprErrors() {
  document.querySelectorAll('.param').forEach(w => {
    const err = M.errors[`${S.activeId}:${w.dataset.name}`];
    const inp = w.querySelector('.multi-expr input');
    if (inp) { inp.classList.toggle('bad', !!err); inp.title = err ? `Not used: ${err}` : 'Leave empty to set this dataset\'s value with the box/slider'; }
    w.classList.toggle('derived', !!(inp && inp.value.trim() && !err));
  });
}
function renderInfo() {
  const box = $('multi-info'); if (!box) return;
  const ids = multiMembers(), al = aliases();
  if (!M.enabled) { box.textContent = ''; return; }
  if (ids.length < 2) { box.textContent = 'Multi: tick two or more datasets (and keep the active one ticked).'; box.className = 'multi-only warn'; return; }
  box.className = 'multi-only';
  box.replaceChildren(el('b', {}, `Editing ${al[S.activeId]}`),
    document.createTextNode(` · ${ids.length} datasets: ${ids.map(i => al[i]).join(', ')} · click a radio to edit another`));
}

/* ---------------------------------------------------------- evaluation */
const _evaluate = evaluate;
evaluate = async function (serial) {
  if (!multiOn()) { M.curves = {}; return _evaluate(serial); }
  ensureSnaps(); syncShared(); resolveAll();
  for (const name of Object.keys(M.expr[S.activeId] || {})) controls[name]?.sync();   // show expression values
  showExprErrors();
  const base = request(), ids = multiMembers();
  try {
    const outs = await Promise.all(ids.map(id => {
      const s = M.snap[id], pd = {};
      for (const [k, v] of Object.entries(s.pd)) if (v.width > 0) pd[k] = v;
      return api('/api/evaluate', { ...base, params: s.params, pd, dataset: id });
    }));
    if (serial !== S.serial) return;
    M.curves = {}; ids.forEach((id, k) => { M.curves[id] = outs[k]; });
    S.current = M.curves[S.activeId];
    render();
    const errs = Object.values(M.errors);
    if (errs.length) setStatus(`Expression not used: ${errs[0]}`, 'warn');
    else if (/^(Compiling|Updating)/.test($('status').textContent) || $('status').className === 'warn' && /^Expression/.test($('status').textContent)) setStatus(`${S.current.model}: updated (${ids.length} datasets).`);
    store();
  } catch (e) { if (serial === S.serial) setStatus(e.message, 'error'); }
};

/* ------------------------------------------------------------- drawing */
function drawModels(g, s) {
  if (!multiOn() || !Object.keys(M.curves).length) return false;
  const ids = multiMembers().sort((a, b) => (a === S.activeId) - (b === S.activeId));
  for (const id of ids) {
    const c = M.curves[id]; if (!c) continue;
    g.append(sv('path', { d: pathD(c.q, transformed(c.q, c.theory), s), class: 'model multi' + (id === S.activeId ? ' active' : ''),
      style: `stroke:${colourOf(id)}` }));
  }
  return true;
}
function drawResiduals(g, s, peak) {
  if (!multiOn()) return;
  for (const id of multiMembers()) {
    if (id === S.activeId) continue;
    const c = M.curves[id], d = S.datasets.find(x => x.id === id);
    if (!c?.residuals || !d) continue;
    const pg = sv('g', { fill: colourOf(id), class: 'multi-res' });
    d.q.forEach((q, i) => { const r = c.residuals[i]; if (r !== null && s.okX(q) && inWindow(q)) pg.append(sv('circle', { cx: s.X(q), cy: s.Y(clamp(r, -peak, peak)), r: 1.8, class: 'pt' })); });
    g.append(pg);
  }
}
function multiTotals() {
  let chi = 0, n = 0; const per = {}, al = aliases();
  for (const id of multiMembers()) {
    const c = M.curves[id];
    if (!c || !finite(c.chisq) || !c.chisq_n) return null;
    chi += c.chisq * c.chisq_n; n += c.chisq_n; per[al[id]] = c.chisq;
  }
  return n ? { chisq: chi / n, n, per } : null;
}
function onScore() {
  const box = $('chisq').parentElement;
  if (!multiOn()) { box.querySelector('small').textContent = 'χ² / N in Q window'; return; }
  const t = multiTotals();
  box.querySelector('small').textContent = `χ² / N, ${multiMembers().length} datasets`;
  $('chisq').textContent = t ? fmt(t.chisq, 4) : '—';
  $('npts').textContent = t ? String(t.n) : '—';
  $('chisq').title = t ? Object.entries(t.per).map(([a, v]) => `${a}: ${fmt(v, 4)}`).join('\n') : '';
}
function multiLegend() { return multiOn() ? `Model: ${S.current.model} × ${multiMembers().length} (each in its dataset colour)` : null; }

/* ------------------------------------------------------- active switch */
function switchActive() {
  if (!M.enabled) { M.lastActive = S.activeId; return; }
  const prev = M.lastActive, next = S.activeId;
  if (prev === next || !next) { M.lastActive = next; return; }
  if (prev && M.snap[prev] && S.desc) {
    if (!M.snap[next]) M.snap[next] = cloneSnap(M.snap[prev]);
    const s = M.snap[next];
    S.params = s.params; S.pd = s.pd; S.ranges = s.ranges;
    for (const p of S.desc.parameters) if (!(p.name in S.params)) S.params[p.name] = M.snap[prev].params[p.name];
    syncAll();
    for (const [k, c] of Object.entries(controls)) {
      const w = document.querySelector(`.param[data-name="${CSS.escape(k)}"]`);
      if (w && S.pd[k]) w.classList.toggle('pd-open', S.pd[k].width > 0);
      c.sync();
    }
    if (F.result?.multi) setFitView();
  }
  M.lastActive = next;
  decorateParams();
}
const _renderDatasetsM = renderDatasets;
renderDatasets = function () {
  switchActive();
  _renderDatasetsM();
  if (M.enabled) {
    const al = aliases(), ids = new Set(multiMembers());
    document.querySelectorAll('#dataset-list .ds').forEach((row, k) => {
      const d = S.datasets[k - 1]; if (!d) return;
      const meta = row.querySelector('.meta');
      const stem = d.label.replace(/\.[^.]+$/, '');
      if (meta && ids.has(d.id) && al[d.id] !== stem) meta.prepend(el('span', { class: 'alias' }, `${al[d.id]} · `));
    });
  }
  renderInfo();
};
const _onControlsBuilt = onControlsBuilt;
onControlsBuilt = function () {
  _onControlsBuilt();
  if (M.enabled && S.activeId) rebuildSnaps();
  decorateParams();
  renderInfo();
};

/* --------------------------------------------------------------- fitting */
function multiFitRequest(withFree = true) {
  ensureSnaps(); syncShared(); resolveAll();
  const al = aliases(), names = withFree ? [...freeSet()] : [];
  const members = multiMembers().map(id => {
    const s = M.snap[id], pd = {}, free = {};
    for (const [k, v] of Object.entries(s.pd)) if (v.width > 0 || names.includes(`${k}_pd`)) pd[k] = { n: 35, nsigma: 3, ...v };
    for (const name of names) {
      const base = name.endsWith('_pd') ? name.slice(0, -3) : name;
      if (modeOf(base) === 'each' && (M.expr[id] || {})[base] && !M.errors[`${id}:${base}`]) continue;
      if (name.endsWith('_pd')) { if (s.pd[base]) free[name] = [0, 1]; }
      else if (s.ranges[name]) free[name] = [s.ranges[name].lo, s.ranges[name].hi];
    }
    return { id, alias: al[id], params: s.params, pd, free, window: windowRange() };
  });
  const shared = [];
  for (const p of S.desc.parameters) {
    if (p.kind !== 'float' || modeOf(p.name) === 'all') shared.push(p.name);
    if (p.polydisperse && modeOf(p.name) === 'all') shared.push(`${p.name}_pd`);
  }
  const constraints = {};
  for (const e of liveExprs()) if (!M.errors[`${e.id}:${e.name}`]) constraints[`${e.alias}.${e.name}`] = e.text;
  const free = {}; for (const m of members) for (const [k, v] of Object.entries(m.free)) free[`${m.alias}.${k}`] = v;
  return { ...request(), method: $('fit-method').value, members, shared, constraints, free, controls: S.controls };
}
const _fitRequest = fitRequest;
fitRequest = function () { return multiOn() ? multiFitRequest(true) : _fitRequest(); };

const _startFit = startFit;
startFit = async function () {
  M.before = multiOn() ? Object.fromEntries(multiMembers().map(id => [id, cloneSnap(M.snap[id])])) : null;
  return _startFit();
};
$('fit-run').onclick = startFit;

function setFitView() {   // the joint result seen from the active dataset (for ± and "values still match")
  const r = F.result?.raw; if (!r) return;
  const a = aliases()[S.activeId], values = {}, stderr = {};
  for (const [k, v] of Object.entries(r.values)) {
    const [al, name] = k.includes('.') ? k.split('.') : [null, k];
    if (al === null || al === a) { values[name] = v; stderr[name] = r.stderr[k]; }
  }
  F.result.values = values; F.result.stderr = stderr; F.result.dataset = S.activeId;
  F.result.at_bound = r.at_bound.filter(k => !k.includes('.') || k.startsWith(a + '.')).map(k => k.split('.').pop());
}
const _finishFit = finishFit;
finishFit = function (st) {
  if (st.state === 'error' || !st.result?.multi) return _finishFit(st);
  $('fit-progress').textContent = '';
  const r = st.result, al = aliases(), byAlias = {};
  for (const id of multiMembers()) byAlias[al[id]] = id;
  for (const [k, v] of Object.entries(r.values)) {
    const [a, name] = k.includes('.') ? k.split('.') : [null, k];
    for (const id of a ? [byAlias[a]] : multiMembers()) {
      const s = M.snap[id]; if (!s) continue;
      if (name.endsWith('_pd')) { const b = name.slice(0, -3); s.pd[b] ||= { width: 0, type: 'gaussian' }; s.pd[b].width = roundSig(v, 8); }
      else { s.params[name] = roundSig(v, 8); const c = controls[name]; if (c && s.ranges[name]) ensureInRange(c.p, s.ranges[name], s.params[name]); }
    }
  }
  resolveAll();
  F.result = { ...r, raw: r, model: fullName(), window: F.job.window };
  setFitView();
  syncAll(); updateFitButtons(); queueEval(0);
  const warn = r.at_bound.length ? ` At a bound: ${r.at_bound.join(', ')}.` : '';
  setStatus(`${r.cancelled ? 'Joint fit cancelled — best point so far' : 'Joint fit done'} (${r.method_label}, ${multiMembers().length} datasets, ${st.elapsed_s.toFixed(1)} s): ` +
    `χ²ᵣ = ${fmt(r.chisq_reduced, 4)} with ${Object.keys(r.values).length} free, N = ${r.n_points}.` + warn, warn ? 'warn' : '');
};
const _undoFit = undoFit;
undoFit = function () {
  if (!M.before || running()) return _undoFit();
  for (const [id, s] of Object.entries(M.before)) {
    if (!M.snap[id]) continue;
    Object.assign(M.snap[id].params, s.params); M.snap[id].pd = s.pd; M.snap[id].ranges = s.ranges;
  }
  S.pd = M.snap[S.activeId].pd; S.ranges = M.snap[S.activeId].ranges;
  M.before = null; F.before = null;
  syncAll(); updateFitButtons(); queueEval(0);
  setStatus('Restored every dataset\'s values from before the joint fit.');
};
$('fit-undo').onclick = undoFit;
const _updateFitButtons = updateFitButtons;
updateFitButtons = function () {
  _updateFitButtons();
  if (!multiOn()) return;
  const noDi = multiMembers().map(id => S.datasets.find(d => d.id === id)).filter(d => !d.has_di);
  const n = freeSet().size;
  $('fit-run').disabled = running() || noDi.length > 0 || n === 0;
  $('fit-run').title = noDi.length ? `${noDi[0].label} has no dI` : n === 0 ? 'Tick the parameters to vary'
    : `Joint fit of ${multiMembers().length} datasets (shared, per-dataset and expression parameters)`;
};

/* ------------------------------------------------------ notebook & copy */
const _notebookRequest = notebookRequest;
notebookRequest = function () {
  if (!multiOn()) return _notebookRequest();
  const req = multiFitRequest(F.enabled);
  const t = multiTotals(), chisq = {};
  if (t) Object.assign(chisq, t.per);
  return { ...req, fit: typeof fitInfo === 'function' ? fitInfo() : null, chisq_n: chisq };
};
const _summaryText = summaryText;
summaryText = function () {
  if (!multiOn()) return _summaryText();
  const t = multiTotals(), al = aliases(), ids = multiMembers(), fit = fitNow();
  const lines = [`sassafras ${$('versions').dataset.version || ''} · ${new Date().toLocaleString()}`,
    `Multi: ${ids.map(id => `${al[id]} = ${S.datasets.find(d => d.id === id).label}`).join(', ')}`, `Model: ${fullName()}`];
  if (fit) lines.push(`Joint fit: ${fit.engine}, ${fit.method}; χ²ᵣ = ${fmt(fit.chisq_reduced, 4)} (N = ${fit.n_points}, dof = ${fit.dof})`);
  else if (t) lines.push(`χ²/N: ${fmt(t.chisq, 4)} combined (N = ${t.n}); ` + Object.entries(t.per).map(([a, v]) => `${a} ${fmt(v, 4)}`).join(', '));
  const table = multiTable(), all = [table.head, ...table.rows];
  const widths = table.head.map((_, k) => Math.max(...all.map(r => r[k].length)) + 2);
  lines.push('Parameters (= shared, ƒ expression' + (fit ? ', ± 1σ from BUMPS' : '') + '):');
  for (const r of all) lines.push('  ' + r.map((c, k) => c.padEnd(widths[k])).join('').trimEnd());
  return lines.join('\n');
};
const _summaryHtml = summaryHtml;
summaryHtml = function () {
  if (!multiOn()) return _summaryHtml();
  const t = multiTable();
  return `<pre style="font:12px ui-monospace,Menlo,monospace">${esc(summaryText())}</pre>` +
    `<table><tr>${t.head.map(h => `<th style="text-align:left;padding:2px 10px 2px 0">${esc(h)}</th>`).join('')}</tr>` +
    t.rows.map(r => `<tr>${r.map(c => `<td style="padding:2px 10px 2px 0">${esc(c)}</td>`).join('')}</tr>`).join('') + '</table>';
};
function multiTable() {
  const al = aliases(), ids = multiMembers(), raw = F.result?.raw && fitCurrent() ? F.result.raw : null;
  const head = ['Parameter', ...ids.map(id => al[id])], rows = [];
  for (const p of S.desc.parameters) {
    const each = p.kind === 'float' && modeOf(p.name) === 'each';
    const row = [`${each ? ' ' : '='} ${p.name}`];
    for (const id of ids) {
      const v = M.snap[id].params[p.name], ex = each && (M.expr[id] || {})[p.name];
      let c = p.kind === 'choice' ? p.choices[Math.round(v)] : fmt(v, 6);
      const key = each ? `${al[id]}.${p.name}` : p.name, e = raw?.stderr?.[key];
      if (raw && key in raw.stderr) c += e === null ? ' ± ?' : ` ± ${fmt(e, 2)}`;
      if (ex && !M.errors[`${id}:${p.name}`]) c += ` ƒ ${ex}`;
      const pd = M.snap[id].pd[p.name];
      if (pd && pd.width > 0) c += ` [PD ${fmt(pd.width, 3)}]`;
      row.push(c);
    }
    rows.push(row);
  }
  return { head, rows };
}
const _paramsDoc = paramsDoc;
paramsDoc = function () {
  const doc = _paramsDoc();
  if (!multiOn()) return doc;
  const al = aliases();
  doc.multi = {
    modes: Object.fromEntries(floatParams().map(p => [p.name, modeOf(p.name)])),
    datasets: multiMembers().map(id => {
      const d = S.datasets.find(x => x.id === id), s = M.snap[id];
      return { alias: al[id], label: d.label, source: d.source, params: s.params, pd: s.pd, slider_ranges: s.ranges, expressions: M.expr[id] || {} };
    }),
    combined_chisq_per_point: multiTotals()?.chisq ?? null,
  };
  return doc;
};
const _loadParams = loadParams;
loadParams = async function (file) {
  await _loadParams(file);
  let doc; try { doc = JSON.parse(await file.text()); } catch { return; }
  if (!doc.multi) return;
  M.enabled = true; $('multi-toggle').checked = true; multiStore(); applyMultiMode();
  M.mode[fullName()] = { ...doc.multi.modes };
  let matched = 0;
  for (const e of doc.multi.datasets) {
    const d = S.datasets.find(x => x.source === e.source) || S.datasets.find(x => x.label === e.label);
    if (!d) continue;
    S.visible.add(d.id); matched++;
    if (d.id === S.activeId) { Object.assign(S.params, e.params); Object.assign(S.pd, e.pd); Object.assign(S.ranges, e.slider_ranges); }
    else M.snap[d.id] = { params: { ...S.params, ...e.params }, pd: { ...structuredClone(S.pd), ...e.pd }, ranges: { ...structuredClone(S.ranges), ...e.slider_ranges } };
    M.expr[d.id] = { ...e.expressions };
  }
  M.model = fullName();
  renderDatasets(); syncAll(); decorateParams(); queueEval(0);
  setStatus(`Loaded multi parameters for ${matched} of ${doc.multi.datasets.length} datasets from ${file.name}` +
    (matched < doc.multi.datasets.length ? ' (load the missing files first, then load the JSON again).' : '.'), matched < doc.multi.datasets.length ? 'warn' : '');
};
$('load-params').onchange = () => { const f = $('load-params').files[0]; if (f) loadParams(f); $('load-params').value = ''; };

/* ------------------------------------------------------------ guess */
if (typeof runGuess === 'function') {
  const _runGuess = runGuess;
  runGuess = async function () {
    const n = multiMembers().length;
    await _runGuess();
    if (n >= 2) {
      const msg = ` Guess used only the active dataset (${aliases()[S.activeId]}); its model now applies to all ${n} ticked datasets, so check the others.`;
      setStatus($('status').textContent + msg, 'warn');
    }
  };
  $('guess').onclick = runGuess;
}

/* --------------------------------------------------------------- toggle */
function applyMultiMode() {
  document.body.classList.toggle('multi-on', M.enabled);
  if (M.enabled && S.desc) { M.model = null; rebuildSnaps(); }
  M.lastActive = S.activeId;
  renderDatasets(); decorateParams(); updateFitButtons(); queueEval(0);
}
$('multi-toggle').onchange = () => { M.enabled = $('multi-toggle').checked; multiStore(); applyMultiMode(); };
// ticking or unticking a dataset changes the members
document.addEventListener('change', e => { if (M.enabled && e.target.classList?.contains('swatch')) { ensureSnaps(); renderInfo(); updateFitButtons(); queueEval(0); } });
multiRecall();
$('multi-toggle').checked = M.enabled;
document.body.classList.toggle('multi-on', M.enabled);
M.lastActive = S.activeId;
