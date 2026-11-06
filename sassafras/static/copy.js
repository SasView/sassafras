'use strict';
/* Copy to notebook: figure (PNG), parameter summary, data table, or all three.
   Each copy puts rich HTML plus a plain-text version on the clipboard, so it
   pastes sensibly into OneNote, ELNs, Word, Excel or a plain-text cell.
   Loaded after app.js and uses its globals (S, $, fmt, controls, ...). */

/* ---------------------------------------------------------------- content */
function copyContext() {
  const ds = activeDataset(), cur = S.current;
  const scored = !!(cur && ds && cur.dataset === ds.id && cur.chisq !== null && cur.chisq !== undefined);
  return { ds, cur, scored };
}
function windowText() {
  const [a, b] = windowRange();
  if (a === null && b === null) return 'all Q';
  return `Q ${a === null ? 'min' : fmt(a, 4)}–${b === null ? 'max' : fmt(b, 4)} Å⁻¹`;
}
function titleLine() {
  const { ds, cur, scored } = copyContext();
  const bits = [ds ? ds.label : 'Model only', cur ? cur.model : fullName()];
  if (scored) bits.push(`χ²/N = ${fmt(cur.chisq, 4)} (N = ${cur.chisq_n}, ${windowText()})`);
  return bits.join(' · ');
}
function paramRows() {
  const rows = [];
  for (const p of S.desc?.parameters || []) {
    let value = S.params[p.name];
    let shown = p.kind === 'choice' ? `${p.choices[Math.round(value)]} (${value})` : fmt(value, 6);
    const pd = S.pd[p.name];
    const pdText = pd && pd.width > 0 ? `PD ${fmt(pd.width, 3)} ${pd.type}` : '';
    rows.push({ name: p.name, value: shown, units: p.units ? prettyUnits(p.units) : '', pd: pdText,
      changed: value !== p.default });
  }
  return rows;
}
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function summaryText() {
  const { ds, cur, scored } = copyContext();
  const lines = [`sassafras ${$('versions').dataset.version || ''} · ${new Date().toLocaleString()}`];
  if (ds) lines.push(`Dataset: ${ds.label} (${ds.source}; ${ds.resolution})`);
  lines.push(`Model: ${cur ? cur.model : fullName()}`);
  if (scored) lines.push(`χ²/N: ${fmt(cur.chisq, 4)}  (N = ${cur.chisq_n}, ${windowText()}; manual values, not a fit)`);
  const rows = paramRows(), w = Math.max(...rows.map(r => r.name.length), 4);
  lines.push('Parameters:');
  for (const r of rows) lines.push(`  ${r.name.padEnd(w)}  ${r.value}${r.units ? ' ' + r.units : ''}${r.pd ? '  [' + r.pd + ']' : ''}`);
  return lines.join('\n');
}
function summaryHtml() {
  const { ds, cur, scored } = copyContext();
  const cell = 'style="padding:2px 10px 2px 0;border-bottom:1px solid #e5eae2;text-align:left"';
  let h = `<p style="margin:0 0 4px"><b>${esc(titleLine())}</b></p>`;
  h += `<p style="margin:0 0 6px;color:#4e5d49;font-size:90%">sassafras ${esc($('versions').dataset.version || '')} · ${esc(new Date().toLocaleString())}`;
  if (ds) h += `<br>${esc(ds.source)} (${esc(ds.resolution)})`;
  if (scored) h += '<br>Manual (chi-by-eye) values, not a fit; no uncertainties.';
  h += '</p><table style="border-collapse:collapse;font-size:90%"><tr>' +
    ['Parameter', 'Value', 'Units', 'Polydispersity'].map(t => `<th ${cell}>${t}</th>`).join('') + '</tr>';
  for (const r of paramRows()) {
    h += `<tr><td ${cell}>${r.changed ? '<b>' : ''}${esc(r.name)}${r.changed ? '</b>' : ''}</td><td ${cell}>${esc(r.value)}</td>` +
      `<td ${cell}>${esc(r.units)}</td><td ${cell}>${esc(r.pd)}</td></tr>`;
  }
  return h + '</table>';
}
function dataTable() {
  const { ds, cur } = copyContext();
  if (cur && ds && cur.dataset === ds.id) {
    const head = ['Q (Å⁻¹)', 'I', 'dI', 'I_model', '(I−model)/dI', 'in_window'];
    const rows = ds.q.map((q, i) => [q, ds.i[i], ds.di ? ds.di[i] : '', cur.theory[i] ?? '',
      cur.residuals ? (cur.residuals[i] ?? '') : '', inWindow(q) ? 1 : 0]);
    return { head, rows };
  }
  if (cur) return { head: ['Q (Å⁻¹)', 'I_model'], rows: cur.q.map((q, i) => [q, cur.theory[i] ?? '']) };
  return null;
}
function tableTsv(t) { return [t.head, ...t.rows].map(r => r.join('\t')).join('\n'); }
function tableHtml(t) {
  const td = v => `<td>${typeof v === 'number' ? Number(v.toPrecision(7)) : esc(v)}</td>`;
  return `<table><tr>${t.head.map(h => `<th>${esc(h)}</th>`).join('')}</tr>` +
    t.rows.map(r => `<tr>${r.map(td).join('')}</tr>`).join('') + '</table>';
}

/* ----------------------------------------------------------------- figure */
// Plot styles as plain CSS (the page's classes use CSS variables, which an
// SVG rendered to an image cannot see), resolved from the light theme so
// pasted figures read well on a white notebook page.
const FIG_CSS = `
text{fill:var(--ink-3);font:12px system-ui,-apple-system,"Segoe UI",sans-serif}
.axis-title{fill:var(--ink-2);font-size:13px}
.gridline{stroke:var(--grid);stroke-width:1}.axisline{stroke:var(--axis);stroke-width:1}
.zero{stroke:var(--axis);stroke-width:1.2}.band{fill:var(--band)}.masked{fill:var(--masked)}
.model{fill:none;stroke:var(--model);stroke-width:2;stroke-linejoin:round}
.pinned{fill:none;stroke:var(--pinned);stroke-width:1.6;stroke-dasharray:6 4}
.err{stroke-width:1}.pt{stroke:var(--surface);stroke-width:1}.pt.out,.err.out{opacity:.35}
.title{fill:var(--ink);font-size:14px;font-weight:600}.legend{fill:var(--ink-2);font-size:12px}`;

function lightVars() {
  const root = document.documentElement, prev = root.dataset.theme;
  root.dataset.theme = 'light';           // read light values; restored before any repaint
  const cs = getComputedStyle(root), out = {};
  for (const n of ['ink', 'ink-2', 'ink-3', 'grid', 'axis', 'band', 'masked', 'model', 'pinned', 'surface',
    's1', 's2', 's3', 's4', 's5', 's6', 's7', 's8']) out[n] = cs.getPropertyValue('--' + n).trim();
  if (prev) root.dataset.theme = prev; else delete root.dataset.theme;
  return out;
}
const resolveVars = (text, vars) => text.replace(/var\(--([\w-]+)\)/g, (m, n) => vars[n] ?? m);

function figureSvg() {
  const vars = lightVars();
  const W = 940, top = 56;
  const main = $('main-plot'), resid = $('resid-wrap').hidden ? null : $('resid-plot');
  const H = top + 420 + (resid ? 210 : 0) + 6;
  const parts = [`<rect width="${W}" height="${H}" fill="#ffffff"/>`,
    `<text class="title" x="14" y="22">${esc(titleLine())}</text>`];
  // legend row
  let x = 14;
  const items = shownSets().map(d => ({ label: d.label, colour: colourOf(d.id), kind: 'dot' }));
  if (S.current) items.push({ label: `Model: ${S.current.model}`, colour: 'var(--model)', kind: 'line' });
  if (S.pinned) items.push({ label: `Pinned: ${S.pinned.label}`, colour: 'var(--pinned)', kind: 'dash' });
  for (const it of items) {
    const mark = it.kind === 'dot' ? `<circle cx="${x + 5}" cy="40" r="4" fill="${it.colour}"/>`
      : `<line x1="${x}" x2="${x + 20}" y1="40" y2="40" stroke="${it.colour}" stroke-width="2"${it.kind === 'dash' ? ' stroke-dasharray="5 3"' : ''}/>`;
    const w = (it.kind === 'dot' ? 14 : 26);
    parts.push(mark, `<text class="legend" x="${x + w}" y="44">${esc(it.label)}</text>`);
    x += w + it.label.length * 6.6 + 18;
  }
  const inner = svg => {
    const c = svg.cloneNode(true);
    c.querySelectorAll('#hover-layer, .zoombox').forEach(e => e.remove());
    // clip-path ids must stay unique inside the combined figure
    c.querySelectorAll('clipPath').forEach(cp => { const id = cp.id; cp.id = 'fig-' + id;
      c.querySelectorAll(`[clip-path="url(#${id})"]`).forEach(g => g.setAttribute('clip-path', `url(#fig-${id})`)); });
    return c.innerHTML;
  };
  parts.push(`<g transform="translate(0 ${top})">${inner(main)}</g>`);
  if (resid) parts.push(`<g transform="translate(0 ${top + 420 + 4})">${inner(resid)}</g>`);
  const body = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">` +
    `<style>${FIG_CSS}</style>${parts.join('')}</svg>`;
  return { text: resolveVars(body, vars), W, H };
}
async function figurePng(scale = 2) {
  const { text, W, H } = figureSvg();
  const url = URL.createObjectURL(new Blob([text], { type: 'image/svg+xml' }));
  try {
    const img = new Image();
    await new Promise((ok, bad) => { img.onload = ok; img.onerror = () => bad(new Error('Could not render the figure.')); img.src = url; });
    const canvas = document.createElement('canvas');
    canvas.width = W * scale; canvas.height = H * scale;
    const ctx = canvas.getContext('2d'); ctx.scale(scale, scale); ctx.drawImage(img, 0, 0);
    return await new Promise(ok => canvas.toBlob(ok, 'image/png'));
  } finally { URL.revokeObjectURL(url); }
}
const blobToDataUrl = b => new Promise(ok => { const r = new FileReader(); r.onload = () => ok(r.result); r.readAsDataURL(b); });

/* -------------------------------------------------------------- clipboard */
// ClipboardItem values are promises so the write starts inside the click
// (required by Safari) while the PNG renders.
async function writeClipboard(items, what) {
  if (!navigator.clipboard || !window.ClipboardItem) throw new Error('This browser does not allow copying rich content here.');
  const entries = {};
  for (const [type, value] of Object.entries(items)) {
    entries[type] = Promise.resolve(value).then(v => v instanceof Blob ? v : new Blob([v], { type }));
  }
  await navigator.clipboard.write([new ClipboardItem(entries)]);
  setStatus(`Copied ${what} — paste into your notebook.`);
}
function guard(fn) {
  return () => {
    if (!S.current) { setStatus('Nothing to copy yet.', 'warn'); return; }
    fn().catch(e => setStatus(`Copy failed: ${e.message}`, 'error'));
  };
}
const copyFigure = guard(() => writeClipboard({ 'image/png': figurePng() }, 'figure'));
const copySummary = guard(() => writeClipboard({ 'text/html': summaryHtml(), 'text/plain': summaryText() }, 'parameters'));
const copyData = guard(() => {
  const t = dataTable();
  return writeClipboard({ 'text/html': tableHtml(t), 'text/plain': tableTsv(t) }, `data table (${t.rows.length} rows)`);
});
const copyAll = guard(() => {
  const png = figurePng();
  const html = png.then(blobToDataUrl).then(src =>
    `<div>${summaryHtml().replace('</p>', '</p>' + `<img src="${src}" width="940" alt="${esc(titleLine())}"><br>`)}</div>`);
  return writeClipboard({ 'text/html': html, 'text/plain': summaryText() }, 'figure and parameters');
});
async function savePng() {
  if (!S.current) return;
  try {
    const blob = await figurePng();
    const ds = activeDataset();
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement('a'), { href: url, download: `${ds ? safeName(ds.label) + '_' : ''}${S.current.model.replace('@', '_')}.png` });
    document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 1500);
  } catch (e) { setStatus(e.message, 'error'); }
}

$('copy-all').onclick = copyAll;
$('copy-figure').onclick = copyFigure;
$('copy-params').onclick = copySummary;
$('copy-data').onclick = copyData;
$('save-png').onclick = savePng;
