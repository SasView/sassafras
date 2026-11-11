"""The "Guess" button: rank sasmodels models for the active dataset by matching it
against the curve library (SAS-55M-20k plus an S(Q) sub-library, see
``sassafras.library``) and give the chosen model starting values from its closest
library curves, a short polydispersity scan and a brief LM polish.

The library is downloaded on first start (``sassafras.library.fetch_in_background``)
or with ``python -m sassafras.library fetch``.
"""
from __future__ import annotations

import json
import math
import threading
import time

import numpy as np

from .backend import Session, UserError
from .library import Library

TOP = 6
POLISH_TOP = 3             # closest library curves per model given a short LM polish (when bumps is installed)
POLISH_BUDGET_S = 6.0
SEARCH_PD_N = 15           # PD integration points while searching (the UI then uses 35)
LIBRARY_SCAN_S = 3.0       # polydispersity scan over the library matches
PD_EVAL_MAX_S = 0.4        # skip polydispersity when one evaluation with it is slower than this
SLOW_EVAL_S = 0.25         # a kernel slower than this gets one library match, no PD scan
SLOW_POLISH_S = 3.0


class Guesser:
    def __init__(self, session: Session, library: str | bool | None = None, library_sq: str | bool | None = None):
        self.session = session
        self._library_args = (library, library_sq)
        self.lock = threading.Lock()
        self._find_libraries()
        self._last_match = None          # (key, {model: [(chi, row), ...]}) of the last library lookup

    def _find_libraries(self):
        library, library_sq = self._library_args
        dirs = [Library.find(library)]
        if library is not False:      # the S(Q) sub-library is consulted alongside the main one
            dirs.append(Library.find(library_sq, "library_sq", "SASSAFRAS_LIBRARY_SQ"))
        self.library_dirs = [d for d in dirs if d]
        self.library_dir = self.library_dirs[0] if self.library_dirs else None
        self._libraries = None

    def reload_libraries(self):
        """Pick up a curve library installed while the server runs (the background download)."""
        with self.lock:
            self._find_libraries()
            self._last_match = None

    @staticmethod
    def _download_note() -> str:
        from .library import FETCH
        if FETCH["state"] != "running":
            return ""
        p = FETCH["progress"]
        return f"The curve library is still downloading ({p})" if p and p != "starting" else "The curve library is still downloading"

    # --------------------------------------------------------------- status
    def status(self) -> dict:
        if not self.library_dir and self._library_args[0] is not False:
            self.reload_libraries()      # picks up a library fetched from the command line meanwhile
        if self.library_dir:
            label = " + ".join(f"{d.parent.name}/{d.name}" for d in self.library_dirs)
            return {"available": True, "source": "library", "folder": ", ".join(map(str, self.library_dirs)),
                    "label": label}
        note = self._download_note()
        reason = (note + "; try Guess again in a minute." if note else
                  "the curve library is not installed. Get it with: python -m sassafras.library fetch")
        return {"available": False, "source": "library", "reason": reason, "downloading": bool(note)}

    def libraries(self) -> list[Library]:
        with self.lock:
            if self._libraries is None:
                self._libraries = [Library(d) for d in self.library_dirs]
            return self._libraries

    def _library_label(self):
        return " + ".join(lib.label for lib in self.libraries())

    def _in_library(self, full: str) -> bool:
        return any(full in lib.models for lib in self.libraries())

    # ------------------------------------------------------------- suggest
    def suggest(self, req: dict) -> dict:
        ds = self.session.datasets.get(req.get("dataset") or "")
        if ds is None:
            raise UserError("Select an active dataset to guess a model for.")
        st = self.status()
        if not st["available"]:
            raise UserError("Guess unavailable: " + st["reason"])
        t0 = time.perf_counter()
        mask = self.session._window_mask(ds, req.get("window"))
        return self._suggest_library(req, ds, mask, t0)

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
        """Starting values for one model from its closest library curves. Returns
        params, pd, controls and the score (sasmodels defaults, with a note, for a
        model the library doesn't hold)."""
        if "@" in req.get("model", ""):          # accept "form@sf" as the model name too
            form, _, sf_name = req["model"].partition("@")
            req = {**req, "model": form, "structure_factor": sf_name}
        st = self.status()
        if not st["available"]:
            raise UserError("Guess unavailable: " + st["reason"])
        return self._library_values(req)

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
