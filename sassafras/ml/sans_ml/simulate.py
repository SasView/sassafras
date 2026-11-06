"""One curated training example: draw settings, call iquana.examples.simulate, QC.

simulate() is called with noise=0 so the smeared curve is kept for on-the-fly
augmentation; the realised noise then comes from IQuana's own _apply_noise, so it
is identical to calling simulate(noise=...) directly.
"""
from __future__ import annotations

import warnings

import numpy as np
from iquana import examples
from scipy.interpolate import PchipInterpolator

from . import catalogue as cat
from . import priors

N_Q = 200
Q_GRID = np.geomspace(5e-4, 1.0, N_Q)

NOISE_RANGE = (0.005, 0.2)      # IQuana noise (relative error at median I)
DQ_RANGE = (0.01, 0.2)          # sigma_q/q
QMIN_RANGE = (0.001, 0.03)
QMAX_RANGE = (0.08, 1.0)
BG_RANGE = (1e-3, 1.0)          # flat background / median model intensity
MODE_WEIGHTS = {'P': 0.35, 'P@S': 0.40, 'P+P': 0.20, 'P@S+P': 0.05}
DETECT_CHI2 = 3.0               # mean chi^2 a feature must add to count as measurable
MAX_TRIES = 6


def _pd(form, params, rng):
    """Attach size polydispersity to up to two parameters (one for slow kernels)."""
    names = cat.size_polydisperse_params(form)
    if not names or rng.random() < 0.25:
        return {}
    r = priors.R(rng)
    slow = form in cat.SLOW
    k = 1 if slow or len(names) == 1 or rng.random() < 0.7 else 2
    chosen = list(rng.choice(names, size=k, replace=False))
    out = {}
    for n in chosen:
        if n not in params:
            continue
        out[f'{n}_pd'] = r.u(0.02, 0.35)
        out[f'{n}_pd_type'] = r.c(['gaussian', 'lognormal', 'schulz'])
        out[f'{n}_pd_n'] = 7 if slow else (25 if k == 1 else 11)
        out[f'{n}_pd_nsigma'] = 3.0
    return out


def _size_proxy(params):
    keys = ('radius', 'radius_core', 'radius_polar', 'radius_lg', 'radius_minor', 'rg', 'length_a',
            'radius_average', 'radius_a', 'cor_length', 'thickness')
    for k in keys:
        if k in params:
            return float(params[k])
    return None


def _draw_sq(form, fparams, s, rng):
    p = priors.draw(s, rng)
    mode = cat.effective_radius_mode(form)
    if mode:
        p['radius_effective_mode'] = mode
    else:
        size = _size_proxy(fparams)
        p['radius_effective'] = size if size else priors.R(rng).l(20, 300)
    return p


def _sim(model, dq, params, w, coarse=1):
    """Noise-free smeared I(Q) on grid points `w`; NaN elsewhere.

    background=0 is explicit because sasmodels otherwise adds its 0.001 default.
    Expensive kernels (coarse>1) are computed on every coarse-th point and
    interpolated: the curves are smooth after orientation averaging and smearing.
    """
    idx = np.flatnonzero(w)
    use = idx[::coarse]
    if use[-1] != idx[-1]:
        use = np.append(use, idx[-1])
    d = examples.simulate(model, q=Q_GRID[use], noise=0.0, seed=None, dq=dq, **{'background': 0.0, **params})
    y = np.full(N_Q, np.nan)
    v = np.asarray(d.y, dtype=float)
    if coarse > 1:
        if not (np.isfinite(v).all() and (v > 0).all()):
            return y
        v = np.exp(PchipInterpolator(np.log(Q_GRID[use]), np.log(v))(np.log(Q_GRID[idx])))
    y[idx] = v
    return y


def _lstsq_chi2(y, dy, basis):
    """Mean chi^2 of the best non-trivial linear combination of `basis` + constant."""
    A = np.column_stack(basis + [np.ones_like(y)]) / dy[:, None]
    scale = np.linalg.norm(A, axis=0)
    scale[scale == 0] = 1.0
    coef, *_ = np.linalg.lstsq(A / scale, y / dy, rcond=None)
    return float(np.mean((y / dy - (A / scale) @ coef) ** 2))


def _qc(comps, w, bg, noise):
    """comps: dict with total and alternatives. Returns (reason|None, margin)."""
    total = comps['total']
    I = total + bg
    med = np.median(np.abs(I[w]))
    dI = noise * np.sqrt(np.abs(I) * med)
    if not (dI[w] > 0).all():
        return 'zero_error', 0.0
    chis = [_lstsq_chi2(I[w], dI[w], [])]
    for alt in comps['alts']:
        chis.append(_lstsq_chi2(I[w], dI[w], [a[w] for a in alt]))
    margin = min(chis) / DETECT_CHI2
    if chis[0] < DETECT_CHI2:
        return 'flat', margin
    if min(chis) < DETECT_CHI2:
        return 'undetectable_component', margin
    return None, margin


def sane(y, w):
    v = y[w]
    if not np.isfinite(v).all():
        return 'nonfinite'
    if (v < 0).any() or v.max() <= 0 or v.max() > 1e30:
        return 'nonpositive' if v.max() <= 0 or (v < 0).any() else 'range'
    # Gaussian tails legitimately underflow, so only judge points that can matter.
    # A smeared minimum cannot change log-slope by e^6 between neighbouring points,
    # but the isolated single-precision glitches in some kernels do.
    sig = v[v > 1e-12 * v.max()]
    if len(sig) > 2 and np.abs(np.diff(np.log(sig), 2)).max() > 6:
        return 'spike'
    return None


def _ref_level(total, w):
    """Median of the points carrying signal; robust to underflowed tails."""
    v = total[w]
    return float(np.median(v[v > 1e-8 * v.max()]))


def _window(form, rng, r):
    lo = max(QMIN_RANGE[0], cat.Q_FLOOR.get(form, 0.0))
    return r.l(lo, QMIN_RANGE[1]), r.l(*QMAX_RANGE)


def attempt(mode, forms, s, rng):
    r = priors.R(rng)
    A = forms[0]
    qmin, qmax = _window(A, rng, r)
    w = (Q_GRID >= qmin) & (Q_GRID <= qmax)
    dq, noise = r.l(*DQ_RANGE), r.l(*NOISE_RANGE)
    bg_ratio = r.l(*BG_RANGE)
    coarse = 3 if any(f in cat.SLOW for f in forms) else 1
    if coarse > 1:
        dq = max(dq, 0.03)

    pa = priors.draw(A, rng)
    pa_all = {**pa, **_pd(A, pa, rng)}
    meta = dict(mode=mode, dq=dq, noise=noise, qmin=qmin, qmax=qmax, bg_ratio=bg_ratio,
                params={'A': pa_all})
    alts = []
    ps = _draw_sq(A, pa, s, rng) if 'S' in mode else None
    if ps and 'volfraction' in pa_all:     # shared by P and S(Q): S(Q) wins, as in sasmodels
        pa_all = {**pa_all, 'volfraction': ps['volfraction']}
    ya = _sim(A, dq, pa_all, w, coarse)
    bad = sane(ya, w)
    if bad:
        return None, bad
    if ps:
        yas = _sim(f'{A}@{s}', dq, {**pa_all, **ps}, w, coarse)
        bad = sane(yas, w)
        if bad:
            return None, 'sq_' + bad
        meta['params']['S'] = ps
    else:
        yas = ya

    if mode.endswith('+P'):
        B = forms[1]
        pb = priors.draw(B, rng)
        pb_all = {**pb, **_pd(B, pb, rng)}
        yb = _sim(B, dq, pb_all, w, coarse)
        bad = sane(yb, w)
        if bad:
            return None, bad
        # choose the mixing ratio so the curves are comparable somewhere in the window
        ratio = _ref_level(yas, w) / _ref_level(yb, w) * r.l(0.1, 10)
        yb = yb * ratio
        total = yas + yb
        meta['params']['B'] = pb_all
        meta['mix_ratio'] = ratio
        alts += [[yas], [yb]]
        if ps:
            alts.append([ya, yb])
    else:
        total = yas
        if ps:
            alts.append([ya])

    bg = bg_ratio * _ref_level(total, w)
    if not bg > 0:                # mostly exactly zero in the window, so no usable noise scale
        return None, 'flat'
    reason, margin = _qc(dict(total=total, alts=alts), w, bg, noise)
    if reason:
        return None, reason
    meta.update(margin=margin, bg=bg)
    return (total, meta), None


def _pick_form(names, rng, uniform):
    if uniform:
        return names[rng.integers(len(names))]
    w = np.array([cat.draw_weight(n) for n in names])
    return names[rng.choice(len(names), p=w / w.sum())]


def recipe(rng, uniform=False):
    modes = list(MODE_WEIGHTS)
    mode = modes[rng.choice(len(modes), p=list(MODE_WEIGHTS.values()))]
    if mode == 'P':
        return mode, [_pick_form(cat.form_factors(), rng, uniform)], None
    sfs = cat.structure_factors()
    if mode == 'P@S':
        return mode, [_pick_form(cat.forms_with_sq(), rng, uniform)], sfs[rng.integers(len(sfs))]
    pool = cat.MIXTURE_POOL
    a, b = rng.choice(len(pool), 2, replace=False)
    forms = [pool[a], pool[b]]
    if mode == 'P+P':
        return mode, forms, None
    if forms[0] in cat.NO_STRUCTURE_FACTOR:
        forms = forms[::-1]
    if forms[0] in cat.NO_STRUCTURE_FACTOR:
        return 'P+P', forms, None
    return mode, forms, sfs[rng.integers(len(sfs))]


def label(mode, forms, s):
    if mode == 'P':
        return forms[0]
    if mode == 'P@S':
        return f'{forms[0]}@{s}'
    if mode == 'P+P':
        return '+'.join(forms)
    return f'{forms[0]}@{s}+{forms[1]}'


def one(rng, stats=None, uniform=False):
    """Return (clean, meta) or None if a recipe failed QC MAX_TRIES times."""
    mode, forms, s = recipe(rng, uniform)
    for _ in range(MAX_TRIES):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            with np.errstate(all='ignore'):
                try:
                    res, why = attempt(mode, forms, s, rng)
                except Exception as e:  # sasmodels parameter/compile failure
                    res, why = None, 'error:' + type(e).__name__
        key = label(mode, forms, s)
        if stats is not None:
            d = stats.setdefault(forms[0], {})
            if res is None:
                d[why] = d.get(why, 0) + 1
            else:
                d['ok'] = d.get('ok', 0) + 1
        if res is not None:
            clean, meta = res
            meta.update(model=key, forms=forms, s=s)
            return clean, meta
    return None
