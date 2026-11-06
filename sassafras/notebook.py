"""Export the current sassafras state as a Jupyter notebook that uses IQuana.

The notebook reproduces what is on screen (data, model, structure factor,
parameter values, polydispersity, Q window and resolution), then sets up the
same fit (free parameters with the slider ranges as bounds, same method) so it
can be refined, reported and saved with IQuana's tools.

Translation notes (sassafras/sasmodels -> IQuana 0.5):

* ``model@sf`` -> ``set_model(model)`` + ``set_structure_factor(sf, mode)``.
  IQuana always makes the kernel read ``radius_effective`` and offers two
  modes: ``'unconstrained'`` (use the given value) and ``'link_radius'``
  (radius_effective = radius). sasmodels' ``radius_effective_mode`` choices
  therefore map as: "unconstrained" -> unconstrained; "radius" -> link_radius;
  any other computed mode (equivalent volume sphere, excluded volume, ...) ->
  unconstrained with ``radius_effective`` fixed at the value sasmodels
  computes for the current geometry.
* Choice parameters (e.g. ``structure_factor_mode``) are written as their
  integer index with ``set_param``.
* Polydispersity -> ``set_pd_param(name, pd_width, pd_n, pd_nsigma, pd_type, vary)``.
  IQuana sets PD-width bounds itself (``set_param`` cannot reach them).
* Fit methods: lm / amoeba / de -> ``fit(engine='bumps', method=...)``;
  DREAM -> ``fit_bayesian(method='dream')``.
* Models with vector parameters (multi-shell ``sld[n]``, ``thickness[n]``)
  cannot be addressed per shell in IQuana 0.5 and are refused.
"""
from __future__ import annotations

import datetime as _dt
import importlib.metadata
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

from .backend import Session, UserError

FIT_CALLS = {
    "lm": "fitter.fit(engine='bumps', method='lm')",
    "amoeba": "fitter.fit(engine='bumps', method='amoeba')",
    "de": "fitter.fit(engine='bumps', method='de')",
    "dream": "fitter.fit_bayesian(method='dream', samples=10000, burn=200)",
}
METHOD_NAMES = {"lm": "Levenberg–Marquardt", "amoeba": "Nelder–Mead simplex",
                "de": "Differential evolution", "dream": "DREAM (MCMC)"}


def _num(v: float) -> str:
    """Python literal for a float that round-trips exactly."""
    v = float(v)
    if v == int(v) and abs(v) < 1e15:
        return f"{v:.1f}"
    return repr(v)


def _bound(v: float) -> str:
    """Readable literal for a fit bound (bounds need no last-bit precision)."""
    v = float(v)
    return f"{v:.10g}" if "e" in f"{v:.10g}" or "." in f"{v:.10g}" else f"{v:.10g}.0"


def _version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _safe(text: str) -> str:
    text = re.sub(r"\.(dat|xml|h5|hdf5|nxs|txt|csv|abs|cor|pdh|ses)$", "", text, flags=re.I)
    return re.sub(r"[^\w.-]+", "_", text).strip("_") or "data"


def effective_radius(session: Session, model: str, params: dict, pd: dict, mode: int) -> float:
    """R_eff that sasmodels computes for ``model`` with radius_effective_mode=mode."""
    from sasmodels.direct_model import call_Fq
    kernel_model = session.kernel(model)          # form factor only
    calc = kernel_model.make_kernel([np.array([0.01])])
    own = {p.name for p in session.catalogue.infos[model].parameters.kernel_parameters} | {"scale", "background"}
    pars = Session._parameters({k: v for k, v in params.items() if k in own},
                               {k: v for k, v in (pd or {}).items() if k in own})
    pars["radius_effective_mode"] = float(mode)
    try:
        out = call_Fq(calc, pars)
        return float(out[2])
    finally:
        calc.release()


def build(session: Session, req: dict) -> dict:
    """Return {'notebook': nbformat dict, 'files': {name: bytes}, 'notes': [...], 'stem': str}."""
    model = req.get("model", "")
    sf = req.get("structure_factor") or None
    full = session.catalogue.full_name(model, sf)
    ds = session.datasets.get(req.get("dataset") or "")
    if ds is None:
        raise UserError("Select an active dataset to export a notebook.")
    desc = session.catalogue.describe(full, req.get("controls") or {})
    if any(p["kind"] == "control" for p in desc["parameters"]):
        raise UserError(f"{model} has per-shell vector parameters, which IQuana 0.5 cannot set "
                        "individually, so it can't be exported as an IQuana notebook.")
    params = dict(req.get("params") or {})
    pd = {k: v for k, v in (req.get("pd") or {}).items() if float(v.get("width", 0) or 0) > 0 or f"{k}_pd" in (req.get("free") or {})}
    free = req.get("free") or {}
    window = req.get("window") or [None, None]
    method = req.get("method") or "lm"
    fit = req.get("fit") or None
    chisq_n = req.get("chisq_n")
    notes, files = [], {}
    stem = f"{_safe(ds.label)}_{full.replace('@', '_')}"

    # ------------------------------------------------------------ data
    src = Path(ds.source) if not ds.source.startswith("upload:") else None
    use_original = src is not None and src.is_file() and not ds.altered
    if use_original:
        data_line = f"fitter.load_data({str(src)!r}" + (f", dataset={ds.entry}" if ds.entry else "") + ")"
    else:
        csv_name = f"{stem}_data.csv"
        cols = [ds.q, ds.i] + ([ds.di] if ds.di is not None else []) + ([ds.dq] if ds.dq is not None and ds.di is not None else [])
        header = "Q_invA,I" + (",dI" if ds.di is not None else "") + (",dQ" if ds.dq is not None and ds.di is not None else "")
        lines = [header] + [",".join(repr(float(c[k])) for c in cols) for k in range(ds.q.size)]
        files[csv_name] = ("\n".join(lines) + "\n").encode()
        data_line = f"fitter.load_data({csv_name!r})   # columns Q, I, dI, dQ (Q in 1/Å), exported by sassafras"
        why = "it was uploaded through the browser" if src is None else \
              "sassafras rescaled Q or dropped points on load" if ds.altered else "the original file is no longer at its path"
        notes.append(f"Data are read from `{csv_name}`, a snapshot of exactly what sassafras plotted, because {why}."
                     + (f" Original: `{ds.source}`." if src is not None else ""))

    # ------------------------------------------------------------ resolution
    if ds.dxl is not None:
        if use_original:
            res_line = "fitter.set_resolution('data')   # slit smearing from the file"
        else:
            res_line = (f"fitter.set_resolution('slit', slit_length={_num(np.median(ds.dxl))}"
                        + (f", slit_width={_num(np.median(ds.dxw))}" if ds.dxw is not None else "") + ")")
            notes.append("Slit smearing is set from the median slit length/width (the CSV snapshot cannot carry slit columns).")
    elif ds.dq is not None:
        res_line = "fitter.set_resolution('data')   # pinhole dQ from the data, as in sassafras"
    else:
        res_line = "fitter.set_resolution('none')   # the data carry no dQ"

    # ------------------------------------------------------------ model + SF
    model_lines = [f"fitter.set_model({model!r})"]
    by_name = {p["name"]: p for p in desc["parameters"]}
    reff_mode_line = None
    skip = set()
    if sf:
        mode_idx = int(round(params.get("radius_effective_mode", 0)))
        choices = by_name.get("radius_effective_mode", {}).get("choices") or ["unconstrained"]
        mode_name = choices[mode_idx] if 0 <= mode_idx < len(choices) else str(mode_idx)
        if mode_idx == 0:
            iq_mode = "unconstrained"
        elif mode_name == "radius" and "radius" in by_name:
            iq_mode = "link_radius"
        else:
            iq_mode = "unconstrained"
            r_eff = effective_radius(session, model, params, pd, mode_idx)
            params["radius_effective"] = r_eff
            if "radius_effective" in free:
                free = {k: v for k, v in free.items() if k != "radius_effective"}
            reff_mode_line = (f"# sassafras used radius_effective_mode = '{mode_name}', computed by sasmodels from the "
                              f"form factor. IQuana 0.5 offers only 'unconstrained' and 'link_radius', so\n"
                              f"# radius_effective is fixed at the value that mode gives for the current geometry "
                              f"({r_eff:.6g} Å). If you fit the size parameters, consider refitting radius_effective.")
            notes.append(f"`radius_effective_mode = '{mode_name}'` has no IQuana equivalent: R_eff is fixed at "
                         f"{r_eff:.6g} Å, the value sasmodels computes for the current geometry.")
        model_lines.append(f"fitter.set_structure_factor({sf!r}, radius_effective_mode={iq_mode!r})")
        skip.add("radius_effective_mode")             # IQuana manages the kernel switch itself
        if iq_mode == "link_radius":
            skip.add("radius_effective")              # follows radius

    # ------------------------------------------------------------ parameters
    p_lines = []
    width = max((len(p["name"]) for p in desc["parameters"]), default=4) + 2
    if reff_mode_line:
        p_lines.append(reff_mode_line)
    for p in desc["parameters"]:
        name = p["name"]
        if name in skip or name not in params:
            continue
        value = params[name]
        unit = f"  # {p['units']}" if p["units"] else ""
        if p["kind"] == "choice":
            label = p["choices"][int(round(value))] if 0 <= int(round(value)) < len(p["choices"]) else value
            p_lines.append(f"fitter.set_param({name!r},{' ' * (width - len(name))}value={int(round(value))}, vary=False)  # {label}")
        elif name in free:
            lo, hi = free[name]
            p_lines.append(f"fitter.set_param({name!r},{' ' * (width - len(name))}value={_num(value)}, "
                           f"min={_bound(lo)}, max={_bound(hi)}, vary=True){unit}")
        else:
            p_lines.append(f"fitter.set_param({name!r},{' ' * (width - len(name))}value={_num(value)}, vary=False){unit}")
    pd_lines = []
    for base, spec in pd.items():
        vary = f"{base}_pd" in free
        pd_lines.append(f"fitter.set_pd_param({base!r}, pd_width={_num(spec.get('width', 0))}, pd_n={int(spec.get('n', 35))}, "
                        f"pd_nsigma={_num(spec.get('nsigma', 3))}, pd_type={spec.get('type', 'gaussian')!r}, vary={vary})")
    if any(f"{b}_pd" in free for b in pd):
        notes.append("PD widths that sassafras fitted between 0 and 1 use IQuana's own PD bounds.")

    q_line = None
    if window and (window[0] is not None or window[1] is not None):
        q_line = "fitter.set_q_range(" + ", ".join(
            f"{k}={_num(v)}" for k, v in (("qmin", window[0]), ("qmax", window[1])) if v is not None) + ")"

    # ------------------------------------------------------------ notebook
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    versions = {k: _version(k) for k in ("sasmodels", "sasdata", "bumps", "iquana")}
    from . import __version__
    md = [f"# {ds.label} · {full}",
          "",
          f"Exported from **sassafras {__version__}** on {now} as **IQuana** code. Running it reproduces the "
          "sassafras state (data, model, parameter values, polydispersity, Q window, resolution) and sets up "
          "the same fit.",
          "",
          "| | |", "|---|---|",
          f"| Data | `{ds.source}` ({ds.q.size} points, {ds.q.min():.4g}–{ds.q.max():.4g} Å⁻¹) |",
          f"| Model | `{full}` |",
          f"| Q window | {'all Q' if q_line is None else q_line.replace('fitter.set_q_range', '').strip('()')} |"]
    if chisq_n is not None:
        md.append(f"| χ²/N in sassafras | {chisq_n:.6g} |")
    if fit:
        md.append(f"| sassafras fit | {fit.get('method', '')}, reduced χ² = {float(fit.get('chisq_reduced', float('nan'))):.6g}, "
                  f"dof = {fit.get('dof', '')}{' (cancelled)' if fit.get('cancelled') else ''} |")
    md.append(f"| Free parameters | {', '.join(free) if free else 'none ticked in sassafras'} |")
    md += ["", "Notes:", "",
           "* IQuana builds sasmodels kernels in single precision and sassafras in double, so curves agree "
           "to roughly 1 part in 10⁴; χ² can differ in the last digits."]
    md += [f"* {n}" for n in notes]
    md += ["", f"<sub>Versions at export: " + ", ".join(f"{k} {v}" for k, v in versions.items() if v) + "</sub>"]

    setup = ["from iquana import IQuana", "", "fitter = IQuana()", data_line, res_line]
    if q_line:
        setup.append(q_line)
    model_cell = model_lines + [""] + p_lines + ([""] + pd_lines if pd_lines else []) + ["", "fitter.get_params()"]
    check = ["import numpy as np", "",
             "# model at the exported values, with the data's resolution, inside the Q window",
             "model_I = fitter.calculate()",
             "d = fitter.data",
             "sel = np.isfinite(model_I)",
             "chi2_N = np.mean(((d.y[sel] - model_I[sel]) / d.dy[sel]) ** 2)",
             f"print(f'χ²/N = {{chi2_N:.6g}} over {{sel.sum()}} points'" +
             (f" + '   (sassafras: {chisq_n:.6g})'" if chisq_n is not None else "") + ")",
             "fitter.plot_model(show_residuals=True, log_scale=True)"]
    if free:
        fit_md = [f"## Fit", "", f"Same free parameters and bounds as in sassafras, with "
                  f"{METHOD_NAMES.get(method, method)}. Bounds are the sassafras slider ranges."]
        fit_cell = [f"result = {FIT_CALLS.get(method, FIT_CALLS['lm'])}", "fitter.plot_results(show_residuals=True)"]
    else:
        fit_md = ["## Fit", "", "No parameters were ticked for fitting in sassafras. Set `vary=True` (and `min`/`max`) "
                  "on the parameters to refine above, then run this cell."]
        fit_cell = ["# result = fitter.fit(engine='bumps', method='lm')", "# fitter.plot_results(show_residuals=True)"]
    report_cell = ["fitter.get_fit_report()"] if free else ["# fitter.get_fit_report()"]
    save_cell = [f"# fitter.save_analysis({stem + '_analysis.json'!r})", f"# fitter.report({stem + '_report.html'!r})"]

    def code(lines):
        return {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
                "source": "\n".join(lines)}

    def mdcell(lines):
        return {"cell_type": "markdown", "metadata": {}, "source": "\n".join(lines)}

    cells = [mdcell(md), code(setup), mdcell(["## Model and parameters (as in sassafras)"]), code(model_cell),
             mdcell(["## Check: reproduce the sassafras curve"]), code(check),
             mdcell(fit_md), code(fit_cell), code(report_cell),
             mdcell(["## Save (optional)"]), code(save_cell)]
    nb = {"nbformat": 4, "nbformat_minor": 5,
          "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
                       "language_info": {"name": "python"},
                       "sassafras": {"version": __version__, "exported": now, "model": full, "dataset": ds.source}},
          "cells": cells}
    for i, c in enumerate(nb["cells"]):
        c["id"] = f"cell-{i}"
    return {"notebook": nb, "files": files, "notes": notes, "stem": stem}


# ---------------------------------------------------------------- Jupyter
class Jupyter:
    """Find or start one JupyterLab (or Notebook) server rooted at the export folder."""

    def __init__(self, root: Path):
        self.root = root
        self.proc = None
        self.base = None      # e.g. http://localhost:8888/
        self.token = ""
        self.flavour = None   # 'lab' or 'notebook'
        self.lock = threading.Lock()

    @staticmethod
    def available() -> str | None:
        if importlib.util.find_spec("jupyterlab"):
            return "lab"
        if importlib.util.find_spec("notebook"):
            return "notebook"
        return None

    def _running(self):
        try:
            out = subprocess.run([sys.executable, "-m", "jupyter", "server", "list", "--json"],
                                 capture_output=True, text=True, timeout=15).stdout
        except Exception:
            return None
        for line in out.splitlines():
            try:
                info = json.loads(line)
            except ValueError:
                continue
            if Path(info.get("root_dir", "")).resolve() == self.root.resolve():
                return info
        return None

    def ensure(self) -> None:
        with self.lock:
            self.flavour = self.flavour or self.available()
            if not self.flavour:
                raise UserError("JupyterLab isn't installed in this Python environment (pip install jupyterlab).")
            if self.base and (self.proc is None or self.proc.poll() is None):
                return
            info = self._running()
            if info:
                self.base, self.token = info["url"], info.get("token", "")
                return
            module = "jupyterlab" if self.flavour == "lab" else "notebook"
            cmd = [sys.executable, "-m", module, "--no-browser", f"--ServerApp.root_dir={self.root}"]
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                cmd.append("--allow-root")
            log = self.root / ".sassafras_jupyter.log"
            with open(log, "w") as fh:
                self.proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, cwd=self.root,
                                             start_new_session=True)   # survives sassafras stopping
            for _ in range(120):
                time.sleep(0.25)
                if self.proc.poll() is not None:
                    tail = [l for l in log.read_text(errors="replace").splitlines() if l.strip()][-1:] if log.exists() else []
                    raise UserError("Jupyter exited straight away" + (f": {tail[0][-200:]}" if tail else "")
                                    + f" (log: {log}).")
                info = self._running()
                if info:
                    self.base, self.token = info["url"], info.get("token", "")
                    return
            raise UserError("Jupyter did not start within 30 s.")

    def url_for(self, name: str) -> str:
        path = "lab/tree/" if self.flavour == "lab" else "notebooks/"
        from urllib.parse import quote
        return f"{self.base.rstrip('/')}/{path}{quote(name)}" + (f"?token={self.token}" if self.token else "")


class Exporter:
    def __init__(self, session: Session, root: str | None = None):
        self.session = session
        self.root = Path(root or Path.home() / "sassafras_notebooks").expanduser()
        self.jupyter = Jupyter(self.root)

    def export(self, req: dict) -> dict:
        built = build(self.session, req)
        self.root.mkdir(parents=True, exist_ok=True)
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        name = f"{stamp}_{built['stem']}.ipynb"
        for fname, payload in built["files"].items():
            (self.root / fname).write_bytes(payload)
        text = json.dumps(built["notebook"], indent=1, ensure_ascii=False)
        (self.root / name).write_text(text, encoding="utf-8")
        out = {"path": str(self.root / name), "folder": str(self.root), "name": name,
               "files": [str(self.root / f) for f in built["files"]], "notes": built["notes"],
               "iquana_installed": importlib.util.find_spec("iquana") is not None,
               "launched": False, "url": None, "notebook": built["notebook"]}
        if req.get("launch", True):
            try:
                self.jupyter.ensure()
                out["url"] = self.jupyter.url_for(name)
                out["launched"] = True
            except UserError as exc:
                out["launch_error"] = str(exc)
        return out


def capabilities() -> dict:
    return {"jupyter": Jupyter.available(), "iquana": _version("iquana")}
