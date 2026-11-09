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
from .library import Library

PRIOR_DRAWS = 300          # upper limit on parameter sets tried per model
PRIOR_BUDGET_S = 4.0       # ... and on time spent
TOP = 6
POLISH_TOP = 3             # best prior draws given a short LM polish (when bumps is installed)
POLISH_BUDGET_S = 6.0
SEARCH_PD_N = 15
LIBRARY_SCAN_S = 3.0       # polydispersity scan over the library matches
PD_EVAL_MAX_S = 0.4        # skip polydispersity when one evaluation with it is slower than this
SLOW_EVAL_S = 0.25         # a kernel slower than this gets one library match, no PD scan
SLOW_POLISH_S = 3.0
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
    def __init__(self, session: Session, folder: str | None = None, library: str | None = None,
                 library_sq: str | None = None):
        self.session = session
        dirs = [Library.find(library)]
        if library is not False:      # the S(Q) sub-library is consulted alongside the main one
            dirs.append(Library.find(library_sq, "library_sq", "SASSAFRAS_LIBRARY_SQ"))
        self.library_dirs = [d for d in dirs if d]
        self.library_dir = self.library_dirs[0] if self.library_dirs else None
        self._libraries = None
        self._last_match = None          # (key, {model: [(chi, row), ...]}) of the last library lookup
        self.folder = next((p.resolve() for p in _candidates(folder) if (p / "sans_ml").is_dir()), None)
        self.model_path = None
        if self.folder:
            pts = [self.folder / "model.pt"] + sorted(self.folder.glob("runs/*/model.pt"))
            self.model_path = next((p for p in pts if p.is_file()), None)
        self._mods = None
        self.lock = threading.Lock()

    # --------------------------------------------------------------- status
    def status(self) -> dict:
        net = self._network_status()
        if self.library_dir:
            label = " + ".join(f"{d.parent.name}/{d.name}" for d in self.library_dirs)
            return {"available": True, "source": "library", "folder": ", ".join(map(str, self.library_dirs)),
                    "label": label, "network": net["available"]}
        return {**net, "source": "network", "network": net["available"]}

    def libraries(self) -> list[Library]:
        with self.lock:
            if self._libraries is None:
                self._libraries = [Library(d) for d in self.library_dirs]
            return self._libraries

    def _library_label(self):
        return " + ".join(lib.label for lib in self.libraries())

    def _in_library(self, full: str) -> bool:
        return any(full in lib.models for lib in self.libraries())

    def _network_status(self) -> dict:
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
        st = self._network_status()
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
        if self.library_dir and req.get("source", "library") == "library":
            return self._suggest_library(req, ds, mask, t0)
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

    # ------------------------------------------------------ library lookup
    def _match(self, ds, mask, window, model=None):
        key = (ds.id, hash(ds.q[mask].tobytes() + ds.i[mask].tobytes()), json.dumps(window))
        if self._last_match and self._last_match[0] == key and (model is None or model in self._last_match[1]):
            return self._last_match[1]
        merged = {}
        try:
            for li, lib in enumerate(self.libraries()):
                if model is not None and model not in lib.models:
                    continue
                found = lib.match(ds.q[mask], ds.i[mask], None if ds.di is None else ds.di[mask],
                                  model=model, per_model=POLISH_TOP)
                for name, hits in found.items():
                    merged[name] = [(chi, row, li) for chi, row in hits]
        except ValueError as exc:
            raise UserError(f"The curve library can't use this curve: {exc}") from None
        best = dict(sorted(merged.items(), key=lambda kv: kv[1][0][0]))
        if model is None:
            self._last_match = (key, best)
        return best

    def _suggest_library(self, req, ds, mask, t0):
        best = self._match(ds, mask, req.get("window"))
        if not best:
            raise UserError("No library curve could be matched to this dataset.")
        listing = {m["name"]: m for m in self.session.catalogue.listing()["form_factors"]}
        top = list(best.items())[: int(req.get("top", TOP))]
        chi0 = top[0][1][0][0]
        out = []
        for name, hits in top:
            form, _, sf = name.partition("@")
            entry = {"model": name, "form": form, "structure_factor": sf or None, "chisq_n": hits[0][0],
                     "weight": (chi0 / hits[0][0]) if hits[0][0] > 0 else 1.0,
                     "source": "library", "loadable": form in listing, "reason": ""}
            if not entry["loadable"]:
                entry["reason"] = "not in this sasmodels version"
            out.append(entry)
        result = {"suggestions": out, "source": "library", "library": self._library_label(),
                  "elapsed_s": time.perf_counter() - t0, "used_points": int(mask.sum()), "window": req.get("window")}
        first = next((x for x in out if x["loadable"]), None)
        if first and req.get("with_values", True):
            result["values"] = self._library_values({**req, "model": first["form"],
                                                     "structure_factor": first["structure_factor"]})
        return result

    def _library_values(self, req: dict) -> dict:
        """Start from the best-matching library curves of one model: their stored
        structural parameters, default SLDs, a short polydispersity scan, the
        linear scale/background solve and the usual LM polish."""
        s, t0 = self.session, time.perf_counter()
        model, sf = req.get("model", ""), req.get("structure_factor") or None
        full = f"{model}@{sf}" if sf else model
        ds = s.datasets.get(req.get("dataset") or "")
        if ds is None:
            raise UserError("Select an active dataset first.")
        mask = s._window_mask(ds, req.get("window"))
        hits = self._match(ds, mask, req.get("window"), model=full).get(full)
        if not hits:
            return {"model": model, "structure_factor": sf, "params": {}, "pd": {}, "draws": 0,
                    "chisq_n": None, "note": f"{full} is not in the curve library: starting from sasmodels defaults"}
        lib = self.libraries()[hits[0][2]]
        w = 1.0 / ds.di[mask] if ds.di is not None else 1.0 / np.maximum(np.abs(ds.i[mask]), 1e-30)
        y = ds.i[mask]
        ranked, slow = [], False
        for _, row, _li in hits:
            if slow or (ranked and time.perf_counter() - t0 > LIBRARY_SCAN_S):
                break
            stored = lib.curve_params(row)
            base = s.describe(model, sf)
            ctrl = {}
            for p in base["parameters"]:
                if p["kind"] == "control" and p["name"] in stored:
                    v = int(round(stored[p["name"]]))
                    lo, hi = p["min"], p["max"]
                    ctrl[p["name"]] = int(min(max(v, lo if lo is not None else v), hi if hi is not None else v))
            desc = s.describe(model, sf, ctrl) if ctrl else base
            params, varied, limits = {}, set(), {}
            for p in desc["parameters"]:
                name = p["name"]
                limits[name] = (p["min"], p["max"])
                if p["kind"] == "control":
                    params[name] = float(ctrl.get(name, p["default"]))
                    continue
                if name in ("radius_effective_mode", "structure_factor_mode"):
                    params[name] = 0.0      # the library used P*S at its stored radius_effective
                    continue
                if p["kind"] != "float":
                    continue
                params[name] = p["default"]
                v = stored.get(name)
                if v is not None and math.isfinite(v):
                    lo, hi = p["min"], p["max"]
                    if (lo is None or v >= lo) and (hi is None or v <= hi):
                        params[name] = float(v)
                        if name not in ("temperature", "dielectconst"):   # fixed solvent conditions
                            varied.add(name)
            params["scale"], params["background"] = 1.0, 0.0
            pd_names = [p["name"] for p in desc["parameters"] if p.get("polydisperse")
                        and p["name"] in set(lib.meta.get("pd", {}).get(full, []))]
            best = None
            for width in ((0.0, 0.1, 0.2, 0.3) if pd_names else (0.0,)):
                if width and time.perf_counter() - t0 > LIBRARY_SCAN_S:
                    break
                pd = {k: {"width": width, "type": "schulz", "n": SEARCH_PD_N} for k in pd_names} if width else {}
                scored = self._score(model, sf, ds, mask, w, y, params, pd)
                if not width:
                    t1 = time.perf_counter()                   # time a second, compiled call
                    self._score(model, sf, ds, mask, w, y, params, {})
                    took = time.perf_counter() - t1
                    if took > SLOW_EVAL_S:
                        slow = True     # a slow kernel: keep this one match, without polydispersity
                    # PD cost grows as n^k: keep only as many PD parameters as stay fast
                    while pd_names and took * SEARCH_PD_N ** len(pd_names) > PD_EVAL_MAX_S:
                        pd_names = pd_names[:-1]
                if slow:
                    if scored:
                        best = (scored[0], {**params, "scale": scored[1], "background": scored[2]}, {})
                    break
                if width and not pd_names:
                    break
                if scored and (best is None or scored[0] < best[0]):
                    best = (scored[0], {**params, "scale": scored[1], "background": scored[2]}, pd)
            if best:
                ranked.append((*best, varied))
        if not ranked:
            return {"model": model, "structure_factor": sf, "params": {}, "pd": {}, "draws": len(hits),
                    "chisq_n": None, "note": "the library matches did not evaluate: starting from sasmodels defaults"}
        ranked.sort(key=lambda r: r[0])
        # SLDs are not stored in the library; when contrast ratios shape the curve
        # (two or more particle SLDs) let the polish move them within the generator's range
        slds = [p["name"] for p in desc["parameters"] if p["kind"] == "float"
                and p["name"].startswith("sld") and "solvent" not in p["name"]]
        bounds = {k: (-0.56, 8.0) for k in slds} if len(slds) >= 2 else None
        out = self._finish(model, sf, ds, ranked, req, t0, limits, None if slow else bounds,
                           polish_s=SLOW_POLISH_S if slow else POLISH_BUDGET_S)
        out.update(draws=len(hits), source="library", library=lib.label,
                   controls={k: int(v) for k, v in out["params"].items() if k in ctrl})
        return out

    def _score(self, model, sf, ds, mask, w, y, params, pd):
        """χ²/N after the weighted linear scale/background solve, or None."""
        try:
            out = self.session.evaluate({"model": model, "structure_factor": sf, "params": params, "pd": pd,
                                         "dataset": ds.id})
        except UserError:
            return None
        shape = np.array([np.nan if v is None else v for v in out["theory"]])[mask]
        ok = np.isfinite(shape)
        if ok.sum() < 5 or not np.any(shape[ok] != 0):
            return None
        A = np.c_[shape[ok], np.ones(ok.sum())] * w[ok, None]
        (sc, bg), *_ = np.linalg.lstsq(A, y[ok] * w[ok], rcond=None)
        if not (sc > 0 and math.isfinite(sc)):
            return None
        return float(np.mean(((y[ok] - sc * shape[ok] - bg) * w[ok]) ** 2)), float(sc), float(bg)

    # ------------------------------------------------------ starting values
    def starting_values(self, req: dict) -> dict:
        """Starting values for one model: from the curve library when it is in use
        (``source`` "library", the default when one is installed), else the best of
        up to PRIOR_DRAWS ml-sans prior draws, scored by χ²/N after the linear
        scale/background solve. Returns params, pd, controls and the score."""
        if "@" in req.get("model", ""):          # accept "form@sf" as the model name too
            form, _, sf_name = req["model"].partition("@")
            req = {**req, "model": form, "structure_factor": sf_name}
        sf = req.get("structure_factor")
        full = f"{req.get('model', '')}@{sf}" if sf else req.get("model", "")
        if self.library_dir and req.get("source", "library") == "library" and self._in_library(full):
            return self._library_values(req)
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
            scored = self._score(model, sf, ds, mask, w, y, params, pd)
            if scored is None:
                continue
            chi, sc, bg = scored
            varied = {k for k, v in p.items() if k in params and isinstance(v, float)}
            ranked.append((chi, {**params, "scale": float(sc), "background": float(bg)}, pd, varied))
            ranked.sort(key=lambda r: r[0])
            del ranked[POLISH_TOP:]
        if not ranked:
            return {"model": model, "structure_factor": sf, "params": {}, "pd": {}, "draws": tried,
                    "chisq_n": None, "note": "no prior draw gave a usable curve: starting from sasmodels defaults"}
        out = self._finish(model, sf, ds, ranked, req, t0, limits)
        out["draws"] = tried
        return out

    def _finish(self, model, sf, ds, ranked, req, t0, limits, bounds=None, polish_s=POLISH_BUDGET_S):
        """LM-polish the best few candidates and report the winner with the UI's PD integration."""
        s = self.session
        chi, params, pd, _ = ranked[0]
        polished = False
        if req.get("polish", True):
            t1 = time.perf_counter()
            for k, (c0, p0, pd0, varied) in enumerate(ranked):
                if k and time.perf_counter() - t1 > polish_s:
                    break
                left = polish_s - (time.perf_counter() - t1)
                out = self._polish(model, sf, ds, p0, pd0, varied, limits, req.get("window"), max(left, 0.5),
                                   bounds=bounds)
                if out and out[0] < chi:
                    chi, params, pd = out
                    polished = True
        pd = {k: {"width": v["width"], "type": v["type"]} for k, v in pd.items()}
        if polished or pd:   # report χ²/N with the UI's own PD integration
            ev = s.evaluate({"model": model, "structure_factor": sf, "params": params, "pd": pd,
                             "dataset": ds.id, "window": req.get("window")})
            chi = ev.get("chisq", chi)
        return {"model": model, "structure_factor": sf, "params": params, "pd": pd,
                "chisq_n": chi, "polished": polished, "elapsed_s": time.perf_counter() - t0, "note": ""}

    def _polish(self, model, sf, ds, params, pd, varied, limits, window, budget_s=POLISH_BUDGET_S, bounds=None):
        """Short bounded LM over the parameters the prior varied (plus scale and
        background, and any explicit ``bounds``) from the best draw. Skipped
        silently without bumps."""
        from . import fit as fitting
        if not fitting.AVAILABLE or ds.di is None:
            return None
        free = {k: list(b) for k, b in (bounds or {}).items() if k in params and b[0] < params[k] < b[1]}
        for k in varied | {"scale", "background"}:
            v = params.get(k)
            if v is None or k in free or k.startswith("sld") or k.endswith("_mode"):
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
