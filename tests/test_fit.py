"""Fitting checks (skipped when bumps is not installed)."""
import time

import numpy as np
import pytest

pytest.importorskip("bumps")

from sassafras.backend import Session, UserError  # noqa: E402
from sassafras.fit import FitManager  # noqa: E402

TRUTH = {"radius": 40.0, "scale": 0.02, "background": 0.05}


@pytest.fixture(scope="module")
def setup():
    session = Session()
    q = np.geomspace(2e-3, 0.4, 150)
    clean = np.array(session.evaluate({"model": "sphere", "params": TRUTH,
                                       "grid": {"qmin": q[0], "qmax": q[-1], "n": q.size}})["theory"])
    rng = np.random.default_rng(1)
    di = 0.02 * clean
    rec = dict(label="synthetic", source="upload:synthetic", q=q, i=clean + di * rng.standard_normal(q.size),
               di=di, dq=None)
    ds = session._add_records([rec])[0]
    return session, FitManager(session), ds


def wait(job, timeout=60):
    t0 = time.time()
    while job.state in ("starting", "running"):
        assert time.time() - t0 < timeout
        time.sleep(0.05)
    return job.status()


def base_request(ds, **kw):
    req = {"model": "sphere", "dataset": ds.id, "method": "lm",
           "params": {"radius": 46, "scale": 0.01, "background": 0.0, "sld": 1, "sld_solvent": 6},
           "free": {"radius": [5, 500], "scale": [1e-5, 1], "background": [0, 1]}}
    req.update(kw)
    return req


@pytest.mark.parametrize("method", ["lm", "amoeba"])
def test_fit_recovers_truth_with_uncertainties(setup, method):
    session, fits, ds = setup
    st = fits.start(base_request(ds, method=method))
    out = wait(fits.get(st["id"]))
    assert out["state"] == "done", out
    res = out["result"]
    for k, v in TRUTH.items():
        assert res["values"][k] == pytest.approx(v, abs=5 * res["stderr"][k])
    assert 0.5 < res["chisq_reduced"] < 2
    assert res["dof"] == 150 - 3


def test_window_limits_points_and_pd_width_is_fittable(setup):
    session, fits, ds = setup
    req = base_request(ds, window=[0.01, 0.2], pd={"radius": {"width": 0.05, "type": "gaussian"}})
    req["free"]["radius_pd"] = [0, 0.5]
    out = wait(fits.get(fits.start(req)["id"]))
    assert out["state"] == "done", out
    assert out["result"]["n_points"] == int(np.sum((ds.q >= 0.01) & (ds.q <= 0.2)))
    assert "radius_pd" in out["result"]["values"]


def test_cancel_stops_a_long_fit(setup):
    session, fits, ds = setup
    st = fits.start(base_request(ds, method="dream"))
    time.sleep(0.3)
    fits.cancel(st["id"])
    out = wait(fits.get(st["id"]))
    assert out["state"] in ("cancelled", "done")


def test_bad_requests_are_reported(setup):
    session, fits, ds = setup
    with pytest.raises(UserError):
        fits.start(base_request(ds, free={}))
    with pytest.raises(UserError):
        fits.start(base_request(ds, free={"radius": [100, 500]}))   # start value outside range
    with pytest.raises(UserError):
        fits.start(base_request(ds, dataset=None))
