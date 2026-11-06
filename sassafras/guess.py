"""Optional "Guess" button: ML model suggestion via the ml-sans project.

The trained network (``sans_ml.predict.Suggester``) ranks sasmodels model
strings for a curve: form factor, optional structure factor, or a two-form
mixture. It does not predict parameters. To give a usable starting point,
:func:`starting_values` draws parameter sets for the chosen model from the
same physical priors the network was trained on (``sans_ml.priors``), scores
each against the data (sassafras' own resolution-smeared evaluation with the
closed-form scale/background solve) and returns the best. That is a quick
prior search, not a fit; refine with the sliders or the Fit toggle.

The trained model and the parts of ``sans_ml`` needed to run it are bundled in
``sassafras/ml``. ``--ml-sans`` or ``$SASSAFRAS_ML_SANS`` can point at a full
ml-sans folder instead (e.g. to try a newly trained model before copying it in).
It needs ``torch``, ``scipy`` and ``iquana`` in the same Python environment;
without them the Guess button explains what is missing.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import math
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

from .backend import Session, UserError

PRIOR_DRAWS = 300          # upper limit on parameter sets tried per model
PRIOR_BUDGET_S = 4.0       # ... and on time spent
TOP = 6
POLISH_TOP = 3             # best prior draws given a short LM polish (when bumps is installed)
POLISH_BUDGET_S = 6.0
SEARCH_PD_N = 15
WORKER = Path(__file__).with_name("_guess_worker.py")
WORKER_TIMEOUT_S = 120           # PD integration points while searching (the UI then uses 35)


BUNDLED = Path(__file__).with_name("ml")         # sans_ml/ (inference subset) + model.pt


def _candidates(explicit: str | None) -> list[Path]:
    out = []
    if explicit:
        out.append(Path(explicit).expanduser())
    if os.environ.get("SASSAFRAS_ML_SANS"):
        out.append(Path(os.environ["SASSAFRAS_ML_SANS"]).expanduser())
    return out + [BUNDLED]


class Guesser:
    def __init__(self, session: Session, folder: str | None = None):
        self.session = session
        self.folder = next((p.resolve() for p in _candidates(folder) if (p / "sans_ml").is_dir()), None)
        self.model_path = None
        if self.folder:
            pts = [self.folder / "model.pt"] + sorted(self.folder.glob("runs/*/model.pt"))
            self.model_path = next((p for p in pts if p.is_file()), None)
        self._mods = None
        self.lock = threading.Lock()

    # --------------------------------------------------------------- status
    def status(self) -> dict:
        if not self.folder:
            return {"available": False, "reason": f"the bundled ML model folder {BUNDLED} is missing "
                    "(reinstall sassafras, or start it with --ml-sans /path/to/ml-sans)"}
        if not self.model_path:
            return {"available": False, "reason": f"No model.pt in {self.folder}"}
        if importlib.util.find_spec("torch") is None:
            return {"available": False, "reason": "PyTorch is not installed in the Python running sassafras. "
                    f"Install it there: {sys.executable} -m pip install torch scipy iquana  "
                    "(then restart sassafras)"}
        return {"available": True, "folder": str(self.folder), "model": str(self.model_path)}

    def _load_priors(self):
        """The prior/simulation helpers (no torch needed)."""
        with self.lock:
            if self._mods is not None:
                return
            if not self.folder:
                raise UserError(self.status()["reason"])
            if str(self.folder) not in sys.path:
                sys.path.insert(0, str(self.folder))
            try:
                self._mods = {name: importlib.import_module(f"sans_ml.{name}")
                              for name in ("priors", "simulate", "catalogue")}
            except Exception as exc:
                raise UserError(f"Could not import sans_ml from {self.folder}: {type(exc).__name__}: {exc}") from None

    def _rank(self, q, i, di, top):
        """Run the network in a child process (see _guess_worker) and return its ranking."""
        st = self.status()
        if not st["available"]:
            raise UserError(st["reason"])
        env = dict(os.environ, KMP_DUPLICATE_LIB_OK="TRUE")
        req = {"folder": str(self.folder), "model_path": str(self.model_path), "top": int(top),
               "q": q.tolist(), "i": i.tolist(), "di": None if di is None else di.tolist()}
        try:
            proc = subprocess.run([sys.executable, str(WORKER)], input=json.dumps(req), capture_output=True,
                                  text=True, env=env, timeout=WORKER_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise UserError(f"The ML model took longer than {WORKER_TIMEOUT_S} s; try again "
                            "(the first run after installing PyTorch can be slow)") from None
        try:
            reply = json.loads(proc.stdout)
        except ValueError:
            tail = " ".join((proc.stderr or "").strip().splitlines()[-3:]) or f"exit code {proc.returncode}"
            raise UserError(f"The ML model process failed: {tail}") from None
        if "error" in reply:
            raise UserError(reply["error"])
        return reply["ranked"]

    # ------------------------------------------------------------- suggest
    def suggest(self, req: dict) -> dict:
        ds = self.session.datasets.get(req.get("dataset") or "")
        if ds is None:
            raise UserError("Select an active dataset to guess a model for.")
        t0 = time.perf_counter()
        mask = self.session._window_mask(ds, req.get("window"))
        ranked = self._rank(ds.q[mask], ds.i[mask], None if ds.di is None else ds.di[mask],
                            int(req.get("top", TOP)))
        listing = {m["name"]: m for m in self.session.catalogue.listing()["form_factors"]}
        out = []
        for r in ranked:
            forms, sf = list(r["forms"]), r["structure"]
            entry = {"model": r["model"], "probability": r["probability"], "form": forms[0],
                     "structure_factor": sf, "loadable": True, "reason": ""}
            if len(forms) > 1:
                entry.update(loadable=False, reason="two-component mixture (A+B); sassafras can't load mixtures yet")
            elif forms[0] not in listing:
                entry.update(loadable=False, reason="not in this sasmodels version")
            elif sf and not listing[forms[0]]["accepts_sq"]:
                entry.update(loadable=False, reason="sassafras applies no S(Q) to this form factor")
            out.append(entry)
        result = {"suggestions": out, "elapsed_s": time.perf_counter() - t0,
                  "used_points": int(mask.sum()), "window": req.get("window")}
        first = next((s for s in out if s["loadable"]), None)
        if first and req.get("with_values", True):
            result["values"] = self.starting_values({**req, "model": first["form"],
                                                     "structure_factor": first["structure_factor"]})
        return result

    # ------------------------------------------------------ starting values
    def starting_values(self, req: dict) -> dict:
        """Best of up to PRIOR_DRAWS prior draws, scored by χ²/N after the linear
        scale/background solve. Returns params, pd, controls and the score."""
        self._load_priors()
        s = self.session
        model, sf = req.get("model", ""), req.get("structure_factor") or None
        full = s.catalogue.full_name(model, sf)
        ds = s.datasets.get(req.get("dataset") or "")
        if ds is None:
            raise UserError("Select an active dataset first.")
        desc = s.describe(model, sf)
        if any(p["kind"] == "control" for p in desc["parameters"]):
            return {"model": model, "structure_factor": sf, "params": {}, "pd": {}, "draws": 0,
                    "chisq_n": None, "note": "multi-shell model: starting from sasmodels defaults"}
        defaults = {p["name"]: p["default"] for p in desc["parameters"]}
        limits = {p["name"]: (p["min"], p["max"]) for p in desc["parameters"]}
        pri, sim = self._mods["priors"], self._mods["simulate"]
        if not hasattr(pri, model):
            return {"model": model, "structure_factor": sf, "params": {}, "pd": {}, "draws": 0,
                    "chisq_n": None, "note": f"no prior for {model} in ml-sans: starting from sasmodels defaults"}
        mask = s._window_mask(ds, req.get("window"))
        w = 1.0 / ds.di[mask] if ds.di is not None else 1.0 / np.maximum(np.abs(ds.i[mask]), 1e-30)
        y = ds.i[mask]
        rng = np.random.default_rng(int(req.get("seed", 0)))
        ranked, tried, t0 = [], 0, time.perf_counter()
        while tried < PRIOR_DRAWS and time.perf_counter() - t0 < PRIOR_BUDGET_S:
            tried += 1
            try:
                p = pri.draw(model, rng)
                pd_raw = sim._pd(model, p, rng)
                if sf:
                    p.update(sim._draw_sq(model, p, sf, rng))
            except Exception:
                continue
            params = {**defaults}
            for k, v in p.items():
                if k in params and isinstance(v, (int, float)) and math.isfinite(float(v)):
                    lo, hi = limits[k]
                    if (lo is None or v >= lo) and (hi is None or v <= hi):
                        params[k] = float(v)
            params["scale"], params["background"] = 1.0, 0.0
            pd = {k[:-3]: {"width": float(v), "type": pd_raw.get(f"{k[:-3]}_pd_type", "gaussian"), "n": SEARCH_PD_N}
                  for k, v in pd_raw.items() if k.endswith("_pd") and k[:-3] in params}
            try:
                out = s.evaluate({"model": model, "structure_factor": sf, "params": params, "pd": pd,
                                  "dataset": ds.id})
            except UserError:
                continue
            shape = np.array([np.nan if v is None else v for v in out["theory"]])[mask]
            ok = np.isfinite(shape)
            if ok.sum() < 5 or not np.any(shape[ok] != 0):
                continue
            A = np.c_[shape[ok], np.ones(ok.sum())] * w[ok, None]
            (sc, bg), *_ = np.linalg.lstsq(A, y[ok] * w[ok], rcond=None)
            if not (sc > 0 and math.isfinite(sc)):
                continue
            chi = float(np.mean(((y[ok] - sc * shape[ok] - bg) * w[ok]) ** 2))
            varied = {k for k, v in p.items() if k in params and isinstance(v, float)}
            ranked.append((chi, {**params, "scale": float(sc), "background": float(bg)}, pd, varied))
            ranked.sort(key=lambda r: r[0])
            del ranked[POLISH_TOP:]
        if not ranked:
            return {"model": model, "structure_factor": sf, "params": {}, "pd": {}, "draws": tried,
                    "chisq_n": None, "note": "no prior draw gave a usable curve: starting from sasmodels defaults"}
        chi, params, pd, _ = ranked[0]
        polished = False
        if req.get("polish", True):
            t1 = time.perf_counter()
            for k, (c0, p0, pd0, varied) in enumerate(ranked):
                if k and time.perf_counter() - t1 > POLISH_BUDGET_S:
                    break
                left = POLISH_BUDGET_S - (time.perf_counter() - t1)
                out = self._polish(model, sf, ds, p0, pd0, varied, limits, req.get("window"), max(left, 0.5))
                if out and out[0] < chi:
                    chi, params, pd = out
                    polished = True
        pd = {k: {"width": v["width"], "type": v["type"]} for k, v in pd.items()}
        if polished or pd:   # report χ²/N with the UI's own PD integration
            ev = s.evaluate({"model": model, "structure_factor": sf, "params": params, "pd": pd,
                             "dataset": ds.id, "window": req.get("window")})
            chi = ev.get("chisq", chi)
        return {"model": model, "structure_factor": sf, "params": params, "pd": pd, "draws": tried,
                "chisq_n": chi, "polished": polished, "elapsed_s": time.perf_counter() - t0, "note": ""}

    def _polish(self, model, sf, ds, params, pd, varied, limits, window, budget_s=POLISH_BUDGET_S):
        """Short bounded LM over the parameters the prior varied (plus scale and
        background) from the best draw. Skipped silently without bumps."""
        from . import fit as fitting
        if not fitting.AVAILABLE or ds.di is None:
            return None
        free = {}
        for k in varied | {"scale", "background"}:
            v = params.get(k)
            if v is None or k.startswith("sld") or k.endswith("_mode"):
                continue
            lo, hi = limits.get(k, (None, None))
            if k == "background":
                span = max(abs(v), float(np.median(np.abs(ds.i))) * 1e-3, 1e-6)
                a, b = v - 3 * span, v + 3 * span
            elif v > 0:
                a, b = v / 4, v * 4
            else:
                continue
            a = a if lo is None else max(a, lo)
            b = b if hi is None else min(b, hi)
            if a < v < b:
                free[k] = [a, b]
        if not free:
            return None
        req = {"model": model, "structure_factor": sf, "dataset": ds.id, "params": params, "pd": pd,
               "free": free, "method": "lm", "window": window, "options": {"steps": 40}}
        try:
            job = fitting.FitJob(self.session, req)
            timer = threading.Timer(budget_s, lambda: setattr(job, "cancelled", True))
            timer.start()                   # cut off slow kernels; the best point so far is kept
            try:
                job._run()                  # synchronous: this request is already off the UI thread
            finally:
                timer.cancel()
        except Exception:
            return None
        if job.state not in ("done", "cancelled") or not job.result:
            return None
        new = {**params, **job.result["values"]}
        ev = self.session.evaluate({"model": model, "structure_factor": sf, "params": new, "pd": pd,
                                    "dataset": ds.id, "window": window})
        return (ev["chisq"], new, pd) if ev.get("chisq") is not None else None
