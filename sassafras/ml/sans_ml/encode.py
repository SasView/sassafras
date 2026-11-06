"""Turn any I(Q) into the fixed network input. Used identically in training and
on real data, so there is one code path for 'what the model sees'.

Channels on the canonical log-Q grid:
  0  arcsinh(I/s), median-centred, where s is the typical error bar. This is
     log-like for strong signal, linear through zero, so noise floors and
     negative background-subtracted points are handled, and absolute scale is gone
  1  log10(dI/s): how the error bar varies with Q
  2  measured-range mask
  3  Q coordinate, so the network knows the absolute length scale
"""
from __future__ import annotations

import numpy as np

from .simulate import N_Q, Q_GRID

LOGQ = np.log(Q_GRID)
COORD = ((LOGQ - LOGQ.mean()) / LOGQ.std()).astype('f4')
MIN_POINTS = 8


def estimate_errors(I):
    """Fallback for files without dI: same form as IQuana's counting model at 2%."""
    I = np.asarray(I, float)
    return 0.02 * np.sqrt(np.abs(I) * np.median(np.abs(I)) + 1e-30)


def encode(q, I, dI=None) -> np.ndarray:
    q, I = np.asarray(q, float), np.asarray(I, float)
    if dI is None or not np.any(np.asarray(dI, float) > 0):
        dI = estimate_errors(I)
    dI = np.asarray(dI, float)
    good = np.isfinite(q) & np.isfinite(I) & np.isfinite(dI) & (dI > 0) & (q > 0)
    good &= (q >= Q_GRID[0]) & (q <= Q_GRID[-1])
    if good.sum() < MIN_POINTS:
        raise ValueError(f'need at least {MIN_POINTS} usable points inside '
                         f'Q={Q_GRID[0]:g}-{Q_GRID[-1]:g} 1/A, got {good.sum()}')
    q, I, dI = q[good], I[good], dI[good]
    order = np.argsort(q)
    q, I, dI = q[order], I[order], dI[order]
    s = float(np.median(dI))
    lq = np.log(q)
    mask = (LOGQ >= lq[0]) & (LOGQ <= lq[-1])
    x = np.zeros((4, N_Q), 'f4')
    y = np.interp(LOGQ[mask], lq, np.arcsinh(I / s))
    x[0, mask] = (y - np.median(y)) / 5.0
    x[1, mask] = np.interp(LOGQ[mask], lq, np.log10(dI / s))
    x[2, mask] = 1.0
    x[3] = COORD
    return x
