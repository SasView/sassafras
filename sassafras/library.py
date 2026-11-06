"""Curve-library lookup for the Guess button (SAS-55M-20k).

SAS-55M-20k (the Sasformer training set) holds 1.1 million noise-free sasmodels
curves: 55 form factors x 20,000 random parameter sets, on 512 log-spaced Q
points from 0.001 to 1 A^-1, each stored with its structural parameters.

``build`` turns the two parquet files into a compact index (log10 I on 128 of
the 512 Q points as float16, plus the parameters, ~400 MB) that is memory-mapped
at run time. ``Library.match`` then scores every library curve against a
dataset: each curve is interpolated onto the data's Q points (in log-log) and
gets its own weighted least-squares scale and background, so only the shape
counts. The best-matching curves give both the model ranking and the starting
parameters. SLDs and polydispersity are not stored in the dataset; sassafras
keeps the sasmodels default SLDs (scale absorbs the contrast) and scans a few
polydispersity widths instead.

Build once (needs pyarrow, which pandas users usually have)::

    python -m sassafras.library build ~/Downloads/sas-55m-20k

which writes ``sassafras/ml/library`` (git-ignored). ``--out`` writes elsewhere;
point sassafras at it with ``--library`` or ``$SASSAFRAS_LIBRARY``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

DEFAULT_DIR = Path(__file__).with_name("ml") / "library"
N_Q = 128
MAX_DATA_POINTS = 160      # data points used for matching (evenly thinned beyond this)
CHUNK = 32768              # library rows scored at a time
FILES = ("train.parquet", "test.parquet")


# ------------------------------------------------------------------ build
def _pd_params(yaml_path: Path) -> dict:
    """Parameters that the generator made polydisperse, per model."""
    if not yaml_path.is_file():
        return {}
    try:
        import yaml
        cfg = yaml.safe_load(yaml_path.read_text()) or {}
        return {m: [p for p, v in spec.items() if isinstance(v, dict) and "polydispersity" in v]
                for m, spec in cfg.items() if isinstance(spec, dict)}
    except ImportError:     # plain line scan of the same layout
        out, model, param = defaultdict(list), None, None
        for line in yaml_path.read_text().splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            indent = len(line) - len(line.lstrip())
            key = line.strip().split(":")[0]
            if indent == 0:
                model = key
            elif indent == 4:
                param = key
            elif indent == 8 and key == "polydispersity" and model and param:
                out[model].append(param)
        return dict(out)


def build(src: str | Path, out: str | Path = DEFAULT_DIR, n_q: int = N_Q, log=print) -> Path:
    import pyarrow.parquet as pq
    src, out = Path(src).expanduser(), Path(out).expanduser()
    files = [src / f for f in FILES if (src / f).is_file()]
    if not files:
        raise SystemExit(f"No {' or '.join(FILES)} in {src}")
    schema = pq.ParquetFile(files[0]).schema_arrow.names
    icols = [c for c in schema if c.startswith("I(q=")]
    q_all = np.array([float(c[4:-1]) for c in icols])
    pick = np.unique(np.linspace(0, len(icols) - 1, n_q).round().astype(int))
    icols, q = [icols[k] for k in pick], q_all[pick]
    regs = defaultdict(list)
    for c in schema:
        if c.startswith("reg-model="):
            m, p = c[len("reg-model="):].split("-param=")
            regs[m].append(p)
    rows = [pq.ParquetFile(f).metadata.num_rows for f in files]
    n, kmax = sum(rows), max(len(v) for v in regs.values())
    out.mkdir(parents=True, exist_ok=True)
    logI = np.lib.format.open_memmap(out / "logI.npy", "w+", np.float16, (n, len(icols)))
    params = np.full((n, kmax), np.nan, np.float32)
    model_id = np.zeros(n, np.uint8)
    models: list[str] = []
    r0, t0 = 0, time.time()
    for f, nr in zip(files, rows):
        pf = pq.ParquetFile(f)
        names = pf.read(columns=["model"]).column(0).to_pylist()
        for name in dict.fromkeys(names):
            if name not in models:
                models.append(name)
        ids = np.array([models.index(x) for x in names], np.uint8)
        model_id[r0:r0 + nr] = ids
        part = np.empty((nr, len(icols)), np.float32)
        for k in range(0, len(icols), 16):
            block = pf.read(columns=icols[k:k + 16]).to_pandas().to_numpy(np.float64)
            part[:, k:k + 16] = np.log10(np.clip(block, 1e-30, None))
            log(f"  {f.name}: intensities {min(k + 16, len(icols))}/{len(icols)}  ({time.time() - t0:.0f} s)")
        # each curve relative to its own maximum: the match fits a free scale anyway,
        # and float16 is most precise near zero
        part -= part.max(axis=1, keepdims=True)
        logI[r0:r0 + nr] = part.astype(np.float16)
        del part
        for m, plist in regs.items():
            if m not in models:
                continue
            sel = np.flatnonzero(ids == models.index(m))
            cols = [f"reg-model={m}-param={p}" for p in plist]
            vals = pf.read(columns=cols).to_pandas().to_numpy(np.float64)[sel]
            params[r0 + sel, :len(plist)] = vals
        log(f"  {f.name}: parameters done  ({time.time() - t0:.0f} s)")
        r0 += nr
    logI.flush()
    bad = ~np.isfinite(np.asarray(logI, np.float32)).all(axis=1)
    np.save(out / "model.npy", np.where(bad, 255, model_id).astype(np.uint8))
    np.save(out / "params.npy", params)
    meta = {"source": src.name, "n": n, "q": q.tolist(), "models": models,
            "params": {m: regs.get(m, []) for m in models},
            "pd": {m: v for m, v in _pd_params(src / "ml_train_55m.yaml").items() if m in models},
            "built": time.strftime("%Y-%m-%d"), "skipped_rows": int(bad.sum())}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    log(f"Wrote {n} curves ({len(models)} models, {len(q)} Q points) to {out} in {time.time() - t0:.0f} s")
    return out


# ----------------------------------------------------------------- lookup
class Library:
    def __init__(self, folder: str | Path):
        self.folder = Path(folder)
        self.meta = json.loads((self.folder / "meta.json").read_text())
        self.q = np.asarray(self.meta["q"], float)
        self.logI = np.load(self.folder / "logI.npy", mmap_mode="r")
        self.model_id = np.load(self.folder / "model.npy")
        self.params = np.load(self.folder / "params.npy", mmap_mode="r")
        self.models = self.meta["models"]
        self.label = f"{self.meta.get('source', 'library')} ({self.meta['n'] / 1e6:.1f}M curves)"

    @staticmethod
    def find(explicit: str | bool | None = None) -> Path | None:
        """The index folder: ``explicit``, ``$SASSAFRAS_LIBRARY``, or the default; False disables."""
        if explicit is False:
            return None
        for p in [explicit, os.environ.get("SASSAFRAS_LIBRARY"), DEFAULT_DIR]:
            if p and (Path(p).expanduser() / "meta.json").is_file():
                return Path(p).expanduser().resolve()
        return None

    def curve_params(self, row: int) -> dict:
        names = self.meta["params"][self.models[self.model_id[row]]]
        vals = self.params[row, :len(names)]
        return {k: float(v) for k, v in zip(names, vals) if np.isfinite(v)}

    def match(self, q, i, di, model: str | None = None, per_model: int = 3):
        """Score library curves against (q, i, di). Returns {model: [(chi2/N, row), ...]}
        with the per_model best rows of each model, best model first."""
        q, i = np.asarray(q, float), np.asarray(i, float)
        w = 1.0 / np.asarray(di, float) if di is not None else 1.0 / np.maximum(np.abs(i), 1e-30)
        ok = (q >= self.q[0]) & (q <= self.q[-1]) & np.isfinite(i) & np.isfinite(w) & (w > 0)
        q, i, w = q[ok], i[ok], w[ok]
        if len(q) < 8:
            raise ValueError(f"only {len(q)} points inside the library's Q range "
                             f"{self.q[0]:g}-{self.q[-1]:g} A^-1 (need 8)")
        if len(q) > MAX_DATA_POINTS:
            keep = np.unique(np.linspace(0, len(q) - 1, MAX_DATA_POINTS).round().astype(int))
            q, i, w = q[keep], i[keep], w[keep]
        X = np.log(self.q)
        j = np.clip(np.searchsorted(X, np.log(q)) - 1, 0, len(X) - 2)
        a = (np.log(q) - X[j]) / (X[j + 1] - X[j])
        M = np.zeros((len(X), len(q)), np.float32)        # log-log interpolation as one matrix product
        M[j, np.arange(len(q))] = 1 - a
        M[j + 1, np.arange(len(q))] += a
        w2 = w * w
        Sw, Sd, Sdd = w2.sum(), (w2 * i).sum(), (w2 * i * i).sum()
        if model is None:
            rows = np.flatnonzero(self.model_id != 255)
        else:
            if model not in self.models:
                return {}
            rows = np.flatnonzero(self.model_id == self.models.index(model))
        chi = np.empty(len(rows))
        for c0 in range(0, len(rows), CHUNK):
            r = rows[c0:c0 + CHUNK]
            G = self.logI[r[0]:r[-1] + 1] if model is None and r[-1] - r[0] == len(r) - 1 else self.logI[r]
            G = np.asarray(G, np.float32)
            L = np.exp((G @ M).astype(np.float64) * np.log(10.0))
            Sl, Sll, Sld = L @ w2, (L * L) @ w2, L @ (w2 * i)
            det = Sll * Sw - Sl * Sl
            with np.errstate(divide="ignore", invalid="ignore"):
                s = (Sld * Sw - Sl * Sd) / det
                b = (Sll * Sd - Sl * Sld) / det
                x = Sdd - 2 * s * Sld - 2 * b * Sd + s * s * Sll + 2 * s * b * Sl + b * b * Sw
            x[~(s > 0) | ~np.isfinite(x)] = np.inf
            chi[c0:c0 + len(r)] = np.maximum(x, 0) / len(q)
        order = np.argsort(chi, kind="stable")
        best: dict[str, list] = {}
        for k in order:
            if not np.isfinite(chi[k]):
                break
            name = self.models[self.model_id[rows[k]]]
            lst = best.setdefault(name, [])
            if len(lst) < per_model:
                lst.append((float(chi[k]), int(rows[k])))
        return best


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m sassafras.library",
                                 description="Build the curve-library index used by Guess.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="index a SAS-55M-20k folder (train/test parquet files)")
    b.add_argument("src")
    b.add_argument("--out", default=str(DEFAULT_DIR))
    b.add_argument("--nq", type=int, default=N_Q)
    args = ap.parse_args(argv)
    try:
        build(args.src, args.out, args.nq)
    except ImportError as exc:
        sys.exit(f"{exc}. Install pyarrow: {sys.executable} -m pip install pyarrow")


if __name__ == "__main__":
    main()
