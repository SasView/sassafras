"""Data loading and model evaluation for sassafras.

Everything numerical lives here, separate from the HTTP layer, so it can be
used and tested directly from Python:

    >>> from sassafras.backend import Session
    >>> s = Session()
    >>> ds = s.load_path("sample.xml")
    >>> out = s.evaluate({"model": "sphere", "params": {"radius": 60}})

Model curves come from sasmodels' ``DirectModel`` (the same calculator that
SasView and the sasmodels BUMPS wrapper use), so the instrument resolution
carried by a data file (pinhole dQ or slit dxl/dxw) is applied when a dataset
is active. Without data the model is evaluated on a plain log-spaced Q grid.
No optimiser is used anywhere: the only "fitting" is a closed-form weighted
linear solve for scale and background, triggered explicitly by the user.
"""
from __future__ import annotations

import math
import os
import re
import tempfile
import threading
import time
import warnings
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

# Native C kernels by default. OpenCL on laptops is a frequent source of
# surprises (single precision, driver prompts); set SAS_OPENCL yourself to
# override this before launching.
os.environ.setdefault("SAS_OPENCL", "none")

import numpy as np  # noqa: E402

PD_TYPES = ("gaussian", "lognormal", "schulz", "rectangle", "uniform", "boltzmann")
PD_DEFAULT_N = 35
PD_DEFAULT_NSIGMA = 3.0
MAX_KERNELS = 12  # compiled models kept in memory


def precision(full_name: str) -> str:
    """"single" unless a component is flagged by sasmodels as needing double
    precision (e.g. hayter_msa, which is ~1% off in single)."""
    from sasmodels.core import load_model_info

    def ok(info):
        if info.composition is not None:
            return all(ok(p) for p in info.composition[1])
        return bool(info.single)
    return "single" if ok(load_model_info(full_name)) else "double"


def load_kernel(full_name: str):
    """Compile a model, in single precision where that is safe (1.2-1.6x faster)."""
    from sasmodels import core
    return core.load_model(full_name, dtype=precision(full_name))


class UserError(ValueError):
    """An error whose message is meant for the person at the screen."""


# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------

def _norm_unit(unit: str | None) -> str:
    return re.sub(r"[\s{}()^]", "", (unit or "")).lower().replace("angstrom", "a").replace("ang", "a").replace("å", "a")


def q_factor_to_inv_angstrom(unit: str | None) -> float:
    """Multiplier that converts Q in ``unit`` to 1/Å (sasmodels' unit)."""
    u = _norm_unit(unit)
    if u in ("", "a-1", "1/a", "inva", "a^-1"):
        return 1.0
    if u in ("nm-1", "1/nm", "invnm"):
        return 0.1
    if u in ("m-1", "1/m"):
        return 1e-10
    if u in ("cm-1", "1/cm") :
        return 1e-8
    return 1.0


# --------------------------------------------------------------------------
# Datasets
# --------------------------------------------------------------------------

@dataclass
class Dataset:
    id: str
    label: str
    source: str            # path on disk, or "upload:<name>"
    q: np.ndarray
    i: np.ndarray
    di: np.ndarray | None
    dq: np.ndarray | None
    dxl: np.ndarray | None = None
    dxw: np.ndarray | None = None
    q_unit_in_file: str = ""
    i_unit: str = ""
    notes: list[str] = field(default_factory=list)
    entry: int = 0          # index of this entry in the file, as sasdata's Loader returns it
    altered: bool = False   # True if Q was rescaled or points were dropped on load
    loaded_at: float = field(default_factory=time.time)

    def to_sasmodels_data(self):
        """A sasmodels-compatible Data1D carrying this dataset's resolution."""
        from sasmodels.data import Data1D
        data = Data1D(x=self.q, y=self.i, dx=self.dq, dy=self.di)
        if self.dxl is not None:
            data.dxl = self.dxl
        if self.dxw is not None:
            data.dxw = self.dxw
        data.filename = self.label
        return data

    def background_estimate(self, n: int = 3) -> float:
        """Initial background guess: mean I of the n highest-Q points."""
        return float(np.mean(self.i[-min(n, self.i.size):]))

    def summary(self) -> dict:
        resolution = "none"
        if self.dxl is not None:
            resolution = "slit"
        elif self.dq is not None:
            resolution = "pinhole dQ"
        return {
            "id": self.id, "label": self.label, "source": self.source,
            "reloadable": not self.source.startswith("upload:"),
            "n": int(self.q.size), "qmin": float(self.q.min()), "qmax": float(self.q.max()),
            "has_di": self.di is not None, "resolution": resolution,
            "q_unit_in_file": self.q_unit_in_file, "i_unit": self.i_unit,
            "notes": self.notes,
            "bkg_estimate": self.background_estimate(),
            "q": self.q.tolist(), "i": self.i.tolist(),
            "di": None if self.di is None else self.di.tolist(),
        }


def _clean_array(values, n):
    if values is None:
        return None
    arr = np.asarray(values, dtype=float).ravel()
    if arr.size != n or not np.any(arr > 0):
        return None
    return arr


def datasets_from_loader_output(items, source: str, label_hint: str) -> tuple[list[dict], list[str]]:
    """Convert sasdata loader objects into plain dicts of 1/Å arrays.

    Returns (records, messages). 2D data are skipped with a message.
    """
    records, messages = [], []
    one_d = [d for d in items if hasattr(d, "x") and not hasattr(d, "qx_data")]
    skipped = len(items) - len(one_d)
    if skipped:
        messages.append(f"{label_hint}: skipped {skipped} 2D data set(s); reduce to 1D (I(Q)) first.")
    for k, d in enumerate(one_d):
        raw_index = next(j for j, item in enumerate(items) if item is d)
        q = np.asarray(d.x, dtype=float).ravel()
        y = np.asarray(d.y, dtype=float).ravel()
        n = q.size
        di = _clean_array(getattr(d, "dy", None), n)
        dq = _clean_array(getattr(d, "dx", None), n)
        dxl = _clean_array(getattr(d, "dxl", None), n)
        dxw = _clean_array(getattr(d, "dxw", None), n)
        notes = []
        q_unit = getattr(d, "_xunit", "") or ""
        factor = q_factor_to_inv_angstrom(q_unit)
        if factor != 1.0:
            q = q * factor
            dq = None if dq is None else dq * factor
            dxl = None if dxl is None else dxl * factor
            dxw = None if dxw is None else dxw * factor
            notes.append(f"Q converted from {q_unit} to 1/Å")
        keep = np.isfinite(q) & np.isfinite(y) & (q > 0)
        if di is not None:
            keep &= np.isfinite(di) & (di > 0)
        if not np.all(keep):
            notes.append(f"dropped {int((~keep).sum())} point(s) with Q≤0, non-finite values or dI≤0")
        if keep.sum() < 2:
            messages.append(f"{label_hint}: entry {k + 1} has fewer than two usable points; skipped.")
            continue
        order = np.argsort(q[keep])
        pick = lambda a: None if a is None else a[keep][order]  # noqa: E731
        title = (getattr(d, "title", "") or "").strip()
        label = label_hint if len(one_d) == 1 else f"{label_hint} [{title or k + 1}]"
        if di is None:
            notes.append("no dI in file; χ² is not available for this dataset")
        records.append(dict(
            label=label, source=source, q=pick(q), i=pick(y), di=pick(di), dq=pick(dq),
            dxl=pick(dxl), dxw=pick(dxw), q_unit_in_file=q_unit,
            i_unit=getattr(d, "_yunit", "") or "", notes=notes,
            entry=raw_index, altered=(factor != 1.0) or not bool(np.all(keep))))
    return records, messages


def load_file(path: str, label: str | None = None) -> tuple[list[dict], list[str]]:
    """Load any file sasdata can read (canSAS XML, NXcanSAS HDF5, ASCII/CSV,
    IGOR .abs/.cor, .pdh, ...). Returns (records, messages)."""
    from sasdata.dataloader.loader import Loader
    p = Path(path).expanduser().resolve()   # absolute, so exports work from any folder
    if not p.is_file():
        raise UserError(f"File not found: {p}")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            items = Loader().load(str(p))
        except Exception as exc:  # sasdata raises a variety of types
            raise UserError(f"sasdata could not read {p.name}: {exc}") from None
    if not isinstance(items, list):
        items = [items]
    if not items:
        raise UserError(f"sasdata found no data in {p.name}.")
    return datasets_from_loader_output(items, str(p), label or p.name)


def supported_extensions() -> list[str]:
    try:
        from sasdata.dataloader.loader import Loader
        exts = set()
        for wc in Loader().get_wildcards():
            exts.update(re.findall(r"\*(\.[A-Za-z0-9]+)", wc))
        return sorted(exts)
    except Exception:
        return [".xml", ".h5", ".hdf5", ".nxs", ".txt", ".dat", ".csv", ".abs", ".cor", ".pdh", ".ses"]


# --------------------------------------------------------------------------
# Model catalogue
# --------------------------------------------------------------------------

CATEGORY_LABELS = {
    "shape:sphere": "Spheres", "shape:cylinder": "Cylinders",
    "shape:ellipsoid": "Ellipsoids", "shape:parallelepiped": "Parallelepipeds",
    "shape:lamellae": "Lamellae", "shape:polyhedron": "Polyhedra",
    "shape:paracrystal": "Paracrystals", "shape-independent": "Shape-independent",
    "structure-factor": "Structure factor only",
}


# Form factors that get no S(Q): they already contain inter-particle interference
# (paracrystals, lamellar stacks, binary_hard_sphere, RPA, peaks/correlation
# functions) or describe no finite particle (power laws, line). sasmodels can
# build "x@hardsphere" for them, but it is not physically meaningful. The S(Q)
# sub-library of the Guess curve library follows the same list.
NO_STRUCTURE_FACTOR = {
    "bcc_paracrystal", "fcc_paracrystal", "sc_paracrystal",
    "lamellar_stack_caille", "lamellar_hg_stack_caille", "lamellar_stack_paracrystal",
    "binary_hard_sphere", "rpa", "be_polyelectrolyte", "teubner_strey", "spinodal",
    "gaussian_peak", "peak_lorentz", "broad_peak", "porod", "power_law", "line",
}


def _first_sentence(text: str, limit: int = 220) -> str:
    text = " ".join((text or "").split())
    m = re.match(r"(.{20,}?[.!?])(\s|$)", text)
    out = m.group(1) if m else text
    return out if len(out) <= limit else out[: limit - 1] + "…"


MAX_COMPONENTS = 4     # form factors in one mixture (the first may carry an S(Q))


class Catalogue:
    def __init__(self):
        from sasmodels import core
        self._core = core
        self.infos = {}
        for name in core.list_models("all"):
            try:
                self.infos[name] = core.load_model_info(name)
            except Exception:
                continue

    def listing(self) -> dict:
        form_factors, structure_factors = [], []
        for name, info in sorted(self.infos.items()):
            entry = {
                "name": name,
                "title": info.title or name,
                "category": CATEGORY_LABELS.get(info.category, info.category or "Other"),
                "accepts_sq": (not info.structure_factor) and name not in NO_STRUCTURE_FACTOR,
            }
            if info.structure_factor:
                structure_factors.append(entry)
            if "magnetic" in name.lower() or name.startswith("micromagnetic"):
                continue
            form_factors.append(entry)
        return {"form_factors": form_factors, "structure_factors": structure_factors}

    def full_name(self, model: str, structure_factor: str | None, components=None) -> str:
        """sasmodels name: ``model[@sf][+form2[+form3[+form4]]]``. The structure factor
        applies to the first form only (``@`` binds tighter than ``+``); extra forms
        get sasmodels' B_, C_, D_ parameter prefixes and the first one A_."""
        if model not in self.infos:
            raise UserError(f"Unknown sasmodels model: {model!r}")
        name = model
        if structure_factor:
            sf = self.infos.get(structure_factor)
            if sf is None or not sf.structure_factor:
                raise UserError(f"Unknown structure factor: {structure_factor!r}")
            if self.infos[model].structure_factor:
                raise UserError("Choose a form factor, not a structure factor, before adding S(Q).")
            name = f"{model}@{structure_factor}"
        extra = [c for c in (components or []) if c]
        if len(extra) > MAX_COMPONENTS - 1:
            raise UserError(f"At most {MAX_COMPONENTS} form factors can be combined.")
        for c in extra:
            if c not in self.infos or self.infos[c].structure_factor:
                raise UserError(f"Unknown form factor for a mixture: {c!r}")
        return "+".join([name] + extra)

    def model_info(self, full_name: str):
        try:
            return self._core.load_model_info(full_name)
        except Exception as exc:
            raise UserError(f"sasmodels cannot build {full_name}: {exc}") from None

    def describe(self, full_name: str, controls: dict | None = None) -> dict:
        """Parameter list for the UI, honouring control parameters such as
        the shell count ``n`` of multi-shell models."""
        info = self.model_info(full_name)
        table = info.parameters
        ctrl = {}
        control_names = set()
        for p in table.kernel_parameters:
            if p.length_control:
                control_names.add(p.length_control)
        for name in control_names:
            default = next((p.default for p in table.kernel_parameters if p.name == name), 1)
            value = (controls or {}).get(name, default)
            try:
                ctrl[name] = int(round(float(value)))
            except (TypeError, ValueError):
                ctrl[name] = int(default)
        user = table.user_parameters(ctrl, is2d=False)
        params = []
        for p in user:
            if p.name in ("up_frac_i", "up_frac_f", "up_theta", "up_phi") or p.name.endswith(("_M0", "_mtheta", "_mphi")):
                continue
            lo, hi = p.limits
            choices = list(getattr(p, "choices", None) or [])
            kind = "choice" if choices else ("control" if p.name in control_names else "float")
            units = "" if p.units in (None, "None") else p.units
            params.append({
                "name": p.name,
                "default": float(ctrl.get(p.name, p.default)) if kind != "float" else float(p.default),
                "min": None if not math.isfinite(lo) else float(lo),
                "max": None if not math.isfinite(hi) else float(hi),
                "units": units,
                "description": p.description or "",
                "type": p.type or "",
                "kind": kind,
                "choices": choices,
                "polydisperse": bool(p.polydisperse) and kind == "float",
            })
        # scale and background first, then the rest in sasmodels order
        params.sort(key=lambda d: {"scale": 0, "background": 1}.get(d["name"], 2))
        return {
            "model": full_name,
            "title": info.title or full_name,
            "summary": _first_sentence(info.description or ""),
            "parameters": params,
            "controls": ctrl,
            "pd_types": list(PD_TYPES),
        }


# --------------------------------------------------------------------------
# Evaluation session
# --------------------------------------------------------------------------

class Session:
    """Holds loaded datasets and compiled kernels for one GUI instance."""

    def __init__(self):
        self.lock = threading.RLock()
        self.catalogue = Catalogue()
        self.datasets: "OrderedDict[str, Dataset]" = OrderedDict()
        self._kernels: "OrderedDict[str, object]" = OrderedDict()
        self._calculators: dict = {}
        self._counter = 0

    # ---- datasets -------------------------------------------------------
    def _add_records(self, records):
        added = []
        with self.lock:
            for rec in records:
                self._counter += 1
                ds = Dataset(id=f"d{self._counter}", **rec)
                self.datasets[ds.id] = ds
                added.append(ds)
        return added

    def load_path(self, path: str):
        p = Path(path).expanduser()
        if p.is_dir():
            exts = set(supported_extensions())
            files = sorted(f for f in p.iterdir() if f.is_file() and f.suffix.lower() in exts)
            if not files:
                raise UserError(f"No files with a sasdata extension in {p}")
            if len(files) > 50:
                raise UserError(f"{p} holds {len(files)} candidate files; load a narrower selection.")
            added, messages = [], []
            for f in files:
                try:
                    recs, msgs = load_file(str(f))
                    added += self._add_records(recs)
                    messages += msgs
                except UserError as exc:
                    messages.append(str(exc))
            return added, messages
        recs, msgs = load_file(str(p))
        return self._add_records(recs), msgs

    def load_bytes(self, filename: str, payload: bytes):
        name = Path(filename or "upload.dat").name
        suffix = Path(name).suffix or ".dat"
        with tempfile.TemporaryDirectory(prefix="sassafras_") as tmp:
            tmp_path = Path(tmp) / f"upload{suffix}"
            tmp_path.write_bytes(payload)
            recs, msgs = load_file(str(tmp_path), label=name)
        for r in recs:
            r["source"] = f"upload:{name}"
        return self._add_records(recs), msgs

    def reload(self, ds_id: str):
        with self.lock:
            ds = self._get(ds_id)
            if ds.source.startswith("upload:"):
                raise UserError("Uploaded files cannot be re-read from disk; load by path to enable reload.")
            recs, msgs = load_file(ds.source)
            if not recs:
                raise UserError("Reloaded file contained no 1D data.")
            rec = recs[0]
            rec["label"] = ds.label
            new = Dataset(id=ds.id, **rec)
            self.datasets[ds.id] = new
            self._calculators = {k: v for k, v in self._calculators.items() if k[0] != ds.id}
            return new, msgs

    def remove(self, ds_id: str):
        with self.lock:
            self._get(ds_id)
            del self.datasets[ds_id]
            self._calculators = {k: v for k, v in self._calculators.items() if k[0] != ds_id}

    def _get(self, ds_id) -> Dataset:
        ds = self.datasets.get(ds_id)
        if ds is None:
            raise UserError("That dataset is no longer loaded.")
        return ds

    # ---- models ---------------------------------------------------------
    def kernel(self, full_name: str):
        with self.lock:
            if full_name in self._kernels:
                self._kernels.move_to_end(full_name)
                return self._kernels[full_name]
            try:
                kernel = load_kernel(full_name)
            except Exception as exc:
                raise UserError(
                    f"Could not compile {full_name}: {exc}. sasmodels needs a working C "
                    "compiler (or OpenCL) to build model kernels.") from None
            self._kernels[full_name] = kernel
            while len(self._kernels) > MAX_KERNELS:
                old, _ = self._kernels.popitem(last=False)
                self._calculators = {k: v for k, v in self._calculators.items() if k[1] != old}
            return kernel

    def describe(self, model: str, structure_factor: str | None, controls=None, components=None):
        full = self.catalogue.full_name(model, structure_factor, components)
        desc = self.catalogue.describe(full, controls)
        t0 = time.perf_counter()
        self.kernel(full)  # compile now so slider moves are fast
        desc["compile_ms"] = (time.perf_counter() - t0) * 1000
        return desc

    def _calculator(self, full_name: str, ds: Dataset | None, grid: tuple | None):
        from sasmodels.direct_model import DirectModel
        from sasmodels.data import empty_data1D
        key = (ds.id if ds else None, full_name, grid if ds is None else None)
        calc = self._calculators.get(key)
        if calc is None:
            kernel = self.kernel(full_name)
            if ds is not None:
                data = ds.to_sasmodels_data()
            else:
                qmin, qmax, n = grid
                data = empty_data1D(np.geomspace(qmin, qmax, n))
            calc = DirectModel(data, kernel)
            self._calculators[key] = calc
        return calc

    @staticmethod
    def _parameters(params: dict, pd: dict | None) -> dict:
        out = {}
        for name, value in (params or {}).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise UserError(f"{name}: expected a number.")
            if not math.isfinite(float(value)):
                raise UserError(f"{name}: expected a finite number.")
            out[str(name)] = float(value)
        for name, spec in (pd or {}).items():
            width = float(spec.get("width", 0) or 0)
            if not math.isfinite(width) or width < 0:
                raise UserError(f"{name}: polydispersity must be ≥ 0.")
            if width <= 0:
                continue
            kind = spec.get("type", "gaussian")
            if kind not in PD_TYPES:
                raise UserError(f"{name}: unknown distribution {kind!r}.")
            out[f"{name}_pd"] = width
            out[f"{name}_pd_type"] = kind
            out[f"{name}_pd_n"] = int(spec.get("n", PD_DEFAULT_N))
            out[f"{name}_pd_nsigma"] = float(spec.get("nsigma", PD_DEFAULT_NSIGMA))
        return out

    @staticmethod
    def _grid(request) -> tuple:
        g = request.get("grid") or {}
        qmin = float(g.get("qmin", 1e-3))
        qmax = float(g.get("qmax", 0.5))
        n = int(g.get("n", 300))
        if not (0 < qmin < qmax and math.isfinite(qmax)):
            raise UserError("Q grid: need 0 < Qmin < Qmax.")
        if not 10 <= n <= 5000:
            raise UserError("Q grid: use 10 to 5000 points.")
        return (qmin, qmax, n)

    def evaluate(self, request: dict) -> dict:
        """Evaluate the model.

        request keys: model, structure_factor, params, pd, dataset (id or None),
        grid {qmin,qmax,n} (used when no dataset), window [qmin,qmax] for χ².
        """
        full = self.catalogue.full_name(request.get("model", ""), request.get("structure_factor") or None,
                                         request.get("components"))
        ds = self._get(request["dataset"]) if request.get("dataset") else None
        grid = None if ds else self._grid(request)
        pars = self._parameters(request.get("params"), request.get("pd"))
        with self.lock:
            calc = self._calculator(full, ds, grid)
            t0 = time.perf_counter()
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                try:
                    theory = np.asarray(calc(**pars), dtype=float)
                except Exception as exc:
                    raise UserError(f"Model evaluation failed: {type(exc).__name__}: {exc}") from None
            elapsed = (time.perf_counter() - t0) * 1000
        q = ds.q if ds else np.geomspace(*grid[:2], grid[2])
        result = {
            "model": full, "q": q.tolist(),
            "theory": [v if math.isfinite(v) else None for v in theory.tolist()],
            "elapsed_ms": elapsed,
            "warnings": sorted({str(w.message) for w in caught}),
            "dataset": ds.id if ds else None,
        }
        if not np.all(np.isfinite(theory)):
            result["warnings"].append("The model returned non-finite values at some Q; those points are not drawn.")
        if ds is not None:
            result.update(self._statistics(ds, theory, request.get("window")))
        return result

    @staticmethod
    def _window_mask(ds: Dataset, window) -> np.ndarray:
        mask = np.ones(ds.q.size, dtype=bool)
        if window:
            lo, hi = window
            if lo is not None:
                mask &= ds.q >= float(lo)
            if hi is not None:
                mask &= ds.q <= float(hi)
        return mask

    def _statistics(self, ds: Dataset, theory: np.ndarray, window) -> dict:
        if ds.di is None:
            return {"residuals": None, "chisq": None, "chisq_n": 0}
        resid = (ds.i - theory) / ds.di
        mask = self._window_mask(ds, window) & np.isfinite(resid)
        n = int(mask.sum())
        chisq = float(np.sum(resid[mask] ** 2) / n) if n else None
        return {
            "residuals": [float(r) if math.isfinite(r) else None for r in resid],
            "chisq": chisq, "chisq_n": n,
        }

    def match_scale(self, request: dict) -> dict:
        """Closed-form weighted least squares for scale (and optionally
        background) with the model shape held fixed. Not an iterative fit."""
        ds = self._get(request.get("dataset")) if request.get("dataset") else None
        if ds is None:
            raise UserError("Load and select a dataset first.")
        params = dict(request.get("params") or {})
        scale0 = float(params.get("scale", 1.0))
        bkg0 = float(params.get("background", 0.0))
        if scale0 == 0:
            params["scale"] = scale0 = 1.0
        shape_req = dict(request, params={**params, "background": 0.0})
        shape = np.asarray([np.nan if v is None else v for v in self.evaluate(shape_req)["theory"]]) / scale0
        mask = self._window_mask(ds, request.get("window")) & np.isfinite(shape)
        w = 1.0 / ds.di[mask] if ds.di is not None else 1.0 / np.maximum(np.abs(ds.i[mask]), 1e-30)
        y, p = ds.i[mask], shape[mask]
        if mask.sum() < 2 or not np.any(p != 0):
            raise UserError("Not enough usable points in the Q window to match.")
        fit_bkg = bool(request.get("background", True))
        if fit_bkg:
            A = np.c_[p, np.ones_like(p)] * w[:, None]
            (s, b), *_ = np.linalg.lstsq(A, y * w, rcond=None)
            if s < 0:  # negative scale is unphysical: fall back to bkg-only
                s, b = 0.0, float(np.sum(w * w * y) / np.sum(w * w))
        else:
            yb = y - bkg0
            s = float(np.sum(w * w * p * yb) / np.sum(w * w * p * p))
            s, b = max(s, 0.0), bkg0
        return {"scale": float(s), "background": float(b)}
