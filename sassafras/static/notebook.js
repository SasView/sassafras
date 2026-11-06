'use strict';
/* "IQuana notebook" button: export the current state as a Jupyter notebook
   (written by the server to its notebook folder) and open it in JupyterLab.
   Loaded after app.js / fitting.js and uses their globals. */

function notebookRequest() {
  const fitOn = typeof F !== 'undefined' && F.enabled && typeof fitRequest === 'function';
  const base = fitOn ? fitRequest() : { ...request(), free: {}, method: 'lm' };
  const cur = S.current, ds = activeDataset();
  return {
    ...base, controls: S.controls,
    fit: typeof fitInfo === 'function' ? fitInfo() : null,
    chisq_n: cur && ds && cur.dataset === ds.id ? cur.chisq : null,
  };
}

function statusWithLink(text, href, label, kind = '') {
  setStatus(text, kind);
  if (href) {
    const a = el('a', { href, target: '_blank', rel: 'noopener' }, label);
    $('status').append(' ', a);
  }
}

async function exportNotebook() {
  const ds = activeDataset();
  if (!ds) { setStatus('Select an active dataset to export a notebook.', 'error'); return; }
  // open the tab now, inside the click, so pop-up blockers allow it; fill it in when Jupyter is ready
  let tab = null;
  try {
    tab = window.open('', '_blank');
    if (tab) { tab.document.title = 'Starting Jupyter…'; tab.document.body.textContent = 'Writing the notebook and starting JupyterLab…'; }
  } catch { tab = null; }
  setStatus('Writing the IQuana notebook…');
  $('export-nb').disabled = true;
  try {
    const out = await api('/api/notebook', notebookRequest());
    const extra = (out.notes.length ? ` ${out.notes.length} translation note(s) are at the top of the notebook.` : '')
      + (out.iquana_installed ? '' : ' IQuana is not installed in this Python environment (pip install iquana).');
    if (out.url) {
      if (tab) tab.location.href = out.url;
      statusWithLink(`Saved ${out.path}.${extra}`, out.url, tab ? 'Reopen in JupyterLab' : 'Open in JupyterLab', out.iquana_installed ? '' : 'warn');
    } else {
      if (tab) tab.close();
      setStatus(`Saved ${out.path}. ${out.launch_error || ''}${extra}`, 'warn');
    }
  } catch (e) {
    if (tab) tab.close();
    setStatus(e.message, 'error');
  } finally { $('export-nb').disabled = false; }
}

$('export-nb').onclick = exportNotebook;
