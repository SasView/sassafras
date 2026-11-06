'use strict';
/* "Guess": rank sasmodels models for the active dataset with the ml-sans
   network, load the top suggestion with starting values from a prior search,
   and keep the ranked list so another suggestion can be loaded with one click. */

const G = { available: false, reason: '', result: null, loaded: null, busy: false };

function onGuessState(st) {
  const g = st.guess || {};
  G.available = !!g.available; G.reason = g.reason || '';
  // stays clickable when unavailable, so a click can say why
  $('guess').classList.toggle('unavailable', !G.available);
  $('guess').title = G.available ? 'Ask the ml-sans network which sasmodels model fits the active dataset (inside the χ² window), and load the top suggestion with starting values'
    : `Guess unavailable: ${G.reason}`;
}

function guessRequest() {
  const ds = activeDataset();
  return { dataset: ds?.id, window: windowRange() };
}

function renderGuess() {
  const box = $('guess-box'), r = G.result;
  box.hidden = !r;
  if (!r) return;
  box.replaceChildren();
  const ds = S.datasets.find(d => d.id === r.dataset);
  const head = el('div', { class: 'ghead' });
  head.append(el('span', {}, `ML suggestions for ${ds ? ds.label : 'dataset'}`));
  const x = el('button', { title: 'Close', 'aria-label': 'Close suggestions' }, '✕');
  x.onclick = () => { G.result = null; renderGuess(); };
  head.append(x); box.append(head);
  const ol = el('ol');
  for (const sgn of r.suggestions) {
    const li = el('li', { class: sgn.loadable ? 'load' : 'off', title: sgn.loadable ? `Load ${sgn.model} with starting values` : sgn.reason });
    if (G.loaded === sgn.model) li.classList.add('current');
    li.append(el('span', { class: 'gname' }, sgn.model), el('span', { class: 'gp' }, `${(100 * sgn.probability).toFixed(sgn.probability < 0.1 ? 1 : 0)}%`));
    const bar = el('span', { class: 'gbar' }); const fill = el('i'); fill.style.width = `${Math.max(1, 100 * sgn.probability)}%`; bar.append(fill); li.append(bar);
    if (sgn.loadable) li.onclick = () => loadSuggestion(sgn);
    ol.append(li);
  }
  box.append(ol);
  box.append(el('div', { class: 'gnote' }, 'Probabilities from the network; treat the top 3 as the answer. Click a row to load it. Starting values come from the ml-sans priors (plus a short LM polish when bumps is installed): a starting point, not a final fit.'));
}

async function applySuggestion(sgn, v) {
  const preset = { params: v.params || {}, pd: v.pd || {}, ranges: {}, controls: {} };
  delete S.memory[sgn.structure_factor ? `${sgn.form}@${sgn.structure_factor}` : sgn.form];
  await selectModel(sgn.form, sgn.structure_factor || '', false, preset);
  G.loaded = sgn.model; renderGuess();
  const how = v.draws && v.chisq_n !== null
    ? `starting values: best of ${v.draws} prior draws${v.polished ? ' + short LM polish' : ''} (χ²/N ${fmt(v.chisq_n, 3)}; a starting point, not a final fit)`
    : (v.note || 'sasmodels defaults');
  setStatus(`Loaded ML suggestion ${sgn.model} (${(100 * sgn.probability).toFixed(0)}%); ${how}.`);
}

async function loadSuggestion(sgn) {
  if (G.busy) return;
  G.busy = true; setStatus(`Finding starting values for ${sgn.model}…`);
  try {
    const v = await api('/api/guess/values', { ...guessRequest(), model: sgn.form, structure_factor: sgn.structure_factor });
    await applySuggestion(sgn, v);
  } catch (e) { setStatus(e.message, 'error'); } finally { G.busy = false; }
}

async function runGuess() {
  if (!G.available) { setStatus(`Guess unavailable: ${G.reason}`, 'warn'); return; }
  const ds = activeDataset();
  if (!ds) { setStatus('Select an active dataset to guess a model for.', 'error'); return; }
  if (G.busy) return;
  G.busy = true; $('guess').disabled = true;
  setStatus('Asking the ML model… (the first call loads PyTorch and can take a few seconds)');
  try {
    const r = await api('/api/guess', guessRequest());
    G.result = { ...r, dataset: ds.id }; G.loaded = null; renderGuess();
    const first = r.suggestions.find(s => s.loadable);
    if (first && r.values) await applySuggestion(first, r.values);
    else setStatus('No suggestion that sassafras can load; see the list.', 'warn');
  } catch (e) { setStatus(e.message, 'error'); }
  finally { G.busy = false; $('guess').disabled = false; }
}

$('guess').onclick = runGuess;
