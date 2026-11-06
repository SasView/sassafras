"""Optional BUMPS fitting for sassafras.

Fits run in a background thread so the GUI stays responsive; the page polls
for progress and can cancel. The model, resolution and Q window are the same
as the live overlay (sasmodels ``bumps_model.Experiment`` on the active
dataset), so a fit starts exactly where the chi-by-eye curve is.

Requires ``bumps``. If it is missing, ``AVAILABLE`` is False and the GUI hides
the Fit toggle; everything else in sassafras works without it.
"""
from __future__ import annotations

import math
import threading
import time
import uuid
import warnings

import numpy as np

from .backend import Session, UserError

try:
    import bumps
    from bumps import fitters
    from bumps.fitproblem import FitProblem
    from bumps.monitor import Monitor
    AVAILABLE = True
    BUMPS_VERSION = getattr(bumps, "__version__", "?")
except ImportError:  # pragma: no cover - exercised only without bumps
    AVAILABLE = False
    BUMPS_VERSION = None
    Monitor = object

# label shown in the GUI -> (bumps fitter class name, extra options)
METHODS = {
    "lm": ("Levenberg–Marquardt", "LevenbergMarquardtFit", {}),
    "amoeba": ("Nelder–Mead simplex", "SimplexFit", {}),
    "de": ("Differential evolution", "DEFit", {}),
    "dream": ("DREAM (MCMC)", "DreamFit", {}),
}
MAX_JOBS = 20


class _Progress(Monitor):
    def __init__(self, job):
        self.job = job

    def config_history(self, history):
        history.requires(step=1, value=1, point=1)

    def __call__(self, history):
        self.job.step = int(history.step[0])
        self.job.best_nllf = float(history.value[0])


class FitJob:
    def __init__(self, session: Session, request: dict):
        if not AVAILABLE:
            raise UserError("Fitting needs the bumps package: pip install bumps")
        self.id = uuid.uuid4().hex[:12]
        self.request = request
        self.method = request.get("method", "lm")
        if self.method not in METHODS:
            raise UserError(f"Unknown fit method {self.method!r}.")
        self.state = "starting"
        self.step = 0
        self.best_nllf = None
        self.error = None
        self.result = None
        self.cancelled = False
        self.started = time.time()
        self.finished = None
        self.problem, self.labels, self.n_points = self._build(session, request)
        self.thread = threading.Thread(target=self._run, daemon=True)

    # ------------------------------------------------------------------
    def _build(self, session: Session, request: dict):
        from sasmodels.bumps_model import Experiment, Model
        from sasmodels.core import load_model

        full = session.catalogue.full_name(request.get("model", ""), request.get("structure_factor") or None)
        ds = session.datasets.get(request.get("dataset") or "")
        if ds is None:
            raise UserError("Select an active dataset to fit.")
        if ds.di is None:
            raise UserError("This dataset has no dI, so it cannot be fitted.")
        pars = session._parameters(request.get("params"), request.get("pd"))
        free = request.get("free") or {}
        if not free:
            raise UserError("Tick at least one parameter to fit.")
        # a fitted PD width must exist in the model even if currently zero
        for name in free:
            if name.endswith("_pd") and name not in pars:
                base = name[:-3]
                pd = (request.get("pd") or {}).get(base, {})
                pars[name] = 0.0
                pars[f"{base}_pd_type"] = pd.get("type", "gaussian")
                pars[f"{base}_pd_n"] = int(pd.get("n", 35))
                pars[f"{base}_pd_nsigma"] = float(pd.get("nsigma", 3.0))
        kernel = load_model(full, dtype="double")
        model = Model(kernel, **{k: v for k, v in pars.items()})
        for name, bounds in free.items():
            par = getattr(model, name, None)
            if par is None:
                raise UserError(f"{name} is not a fittable parameter of {full}.")
            lo, hi = (float(b) for b in bounds)
            if not (math.isfinite(lo) and math.isfinite(hi) and lo < hi):
                raise UserError(f"{name}: fit range needs finite min < max (set the slider ends).")
            value = float(par.value)
            if not lo <= value <= hi:
                raise UserError(f"{name}: current value {value:g} is outside its fit range [{lo:g}, {hi:g}].")
            par.range(lo, hi)
        data = ds.to_sasmodels_data()
        window = request.get("window") or [None, None]
        data.qmin = float(window[0]) if window[0] is not None else 0.0
        data.qmax = float(window[1]) if window[1] is not None else np.inf
        experiment = Experiment(data=data, model=model)
        problem = FitProblem(experiment)
        n = int(np.sum((ds.q >= data.qmin) & (ds.q <= data.qmax)))
        if n <= len(free):
            raise UserError(f"Only {n} points in the Q window for {len(free)} free parameters.")
        return problem, problem.labels(), n

    def start(self):
        self.thread.start()
        return self

    def _run(self):
        self.state = "running"
        _, cls_name, options = METHODS[self.method]
        fitclass = getattr(fitters, cls_name)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                driver = fitters.FitDriver(fitclass, problem=self.problem, monitors=[_Progress(self)],
                                           abort_test=lambda: self.cancelled, **options)
                x, _ = driver.fit()
                if x is None:
                    raise RuntimeError("the fitter returned no result")
                self.problem.setp(x)
                try:
                    err = np.asarray(driver.stderr(), dtype=float)
                except Exception:  # singular covariance etc.
                    err = np.full(len(x), np.nan)
            chisq = float(self.problem.chisq())
            values = {k: float(v) for k, v in zip(self.labels, x)}
            stderr = {k: (float(e) if np.isfinite(e) else None) for k, e in zip(self.labels, err)}
            lo, hi = np.asarray(self.problem.bounds(), dtype=float)
            span = np.where(np.isfinite(hi - lo), hi - lo, np.maximum(np.abs(x), 1.0))
            near = (np.abs(x - lo) <= 1e-6 * span) | (np.abs(hi - x) <= 1e-6 * span)
            at_bound = [k for k, b in zip(self.labels, near) if b]
            self.result = {"values": values, "stderr": stderr, "chisq_reduced": chisq,
                           "dof": int(self.problem.dof), "n_points": self.n_points,
                           "method": self.method, "method_label": METHODS[self.method][0],
                           "at_bound": at_bound, "cancelled": self.cancelled,
                           "bumps": BUMPS_VERSION}
            self.state = "cancelled" if self.cancelled else "done"
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.state = "error"
        finally:
            self.finished = time.time()

    def status(self) -> dict:
        out = {"id": self.id, "state": self.state, "step": self.step, "method": self.method,
               "elapsed_s": (self.finished or time.time()) - self.started}
        if self.best_nllf is not None and self.problem.dof > 0:
            out["chisq_reduced"] = 2 * self.best_nllf / self.problem.dof
        if self.error:
            out["error"] = self.error
        if self.result:
            out["result"] = self.result
        return out


class FitManager:
    def __init__(self, session: Session):
        self.session = session
        self.jobs: dict[str, FitJob] = {}
        self.lock = threading.Lock()

    def start(self, request: dict) -> dict:
        with self.lock:
            if any(j.state in ("starting", "running") for j in self.jobs.values()):
                raise UserError("A fit is already running; cancel it first.")
            job = FitJob(self.session, request)
            self.jobs[job.id] = job
            while len(self.jobs) > MAX_JOBS:
                self.jobs.pop(next(iter(self.jobs)))
        job.start()
        return job.status()

    def get(self, job_id: str) -> FitJob:
        job = self.jobs.get(job_id or "")
        if job is None:
            raise UserError("Unknown fit job.")
        return job

    def cancel(self, job_id: str) -> dict:
        job = self.get(job_id)
        job.cancelled = True
        return job.status()


def capabilities() -> dict:
    return {"available": AVAILABLE, "bumps": BUMPS_VERSION,
            "methods": [{"id": k, "label": v[0]} for k, v in METHODS.items()]}
