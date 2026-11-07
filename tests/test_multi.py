"""Multi: expression checks, joint BUMPS fit and the IQuana MultiFitter export."""
import importlib.util

import numpy as np
import pytest

from sassafras.backend import Session, UserError
from sassafras import multi

bumps = pytest.importorskip("bumps")


@pytest.fixture(scope="module")
def contrast(tmp_path_factory):
    """Three sphere contrasts: shared R = 45 Å, PD 0.1; own scale, background and solvent."""
    from sasmodels.core import load_model
    from sasmodels.data import empty_data1D
    from sasmodels.direct_model import DirectModel
    out = tmp_path_factory.mktemp("contrast")
    q = np.geomspace(0.005, 0.4, 110)
    rng = np.random.default_rng(3)
    calc = DirectModel(empty_data1D(q, resolution=0.05), load_model("sphere"))
    s, ids = Session(), {}
    for name, sld_s, scale, bkg in [("h2o", -0.56, 0.01, 0.05), ("d2o", 6.36, 0.012, 0.02), ("mix", 3.0, 0.011, 0.035)]:
        i = calc(radius=45, radius_pd=0.1, radius_pd_type="schulz", radius_pd_n=35, sld=1.0, sld_solvent=sld_s,
                 scale=scale, background=bkg)
        di = 0.03 * i + 0.002
        f = out / f"{name}.dat"
        np.savetxt(f, np.c_[q, i + rng.normal(0, di), di, 0.05 * q])
        ids[name] = s.load_path(str(f))[0][0].id
    return s, ids


def request(s, ids, **over):
    d = s.describe("sphere", None)
    base = {p["name"]: p["default"] for p in d["parameters"]}
    sld = {"h2o": -0.56, "d2o": 6.36, "mix": 3.0}
    members = [{"id": ids[n], "alias": n, "params": {**base, "radius": 40, "sld": 1.0, "sld_solvent": sld[n],
                                                      "scale": 0.01, "background": 0.03},
                "pd": {"radius": {"width": 0.05, "type": "schulz"}},
                "free": {"radius": [10, 100], "radius_pd": [0, 1], "scale": [1e-4, 1], "background": [0, 0.2]},
                "window": [None, None]} for n in ("h2o", "d2o", "mix")]
    req = {"model": "sphere", "structure_factor": None, "method": "lm", "members": members,
           "shared": ["radius", "radius_pd", "sld"],
           "constraints": {"mix.background": "(h2o.background + d2o.background) / 2"}}
    req.update(over)
    return req


def test_expressions_are_checked():
    known = {"h2o": {"scale", "background"}, "d2o": {"scale", "background"}}
    assert multi.canonical("0.8*scale", "d2o", known) == "0.8 * d2o.scale"
    assert multi.canonical("h2o.scale ^ 2", "d2o", known) == "h2o.scale ** 2"
    for bad in ["__import__('os')", "h2o.scale ** 0.5", "x.scale", "h2o.radius", "abs(h2o.scale)", "h2o.scale if 1 else 2"]:
        with pytest.raises(UserError):
            multi.parse(bad, "d2o", known)


def test_cycles_and_shared_targets_are_refused(contrast):
    s, ids = contrast
    with pytest.raises(UserError, match="Circular"):
        multi.check_request(request(s, ids, constraints={"h2o.scale": "d2o.scale", "d2o.scale": "2 * h2o.scale"}))
    with pytest.raises(UserError, match="shared"):
        multi.check_request(request(s, ids, constraints={"mix.radius": "h2o.radius"}))


def test_joint_fit_recovers_shared_and_per_dataset_values(contrast):
    from sassafras.fit import FitJob
    s, ids = contrast
    job = FitJob(s, request(s, ids))
    assert "mix.background" not in job.labels and "radius" in job.labels
    job.start()
    job.thread.join()
    assert job.state == "done", job.error
    r = job.result
    assert r["multi"] and r["values"]["radius"] == pytest.approx(45, rel=0.02)
    assert r["values"]["radius_pd"] == pytest.approx(0.1, abs=0.02)
    assert r["values"]["d2o.scale"] == pytest.approx(0.012, rel=0.03)
    assert r["chisq_reduced"] == pytest.approx(1, abs=0.3)
    assert r["derived"] == {"mix.background": "(h2o.background + d2o.background) / 2"}


def test_multifitter_notebook(contrast):
    from sassafras.notebook import build
    s, ids = contrast
    out = build(s, {**request(s, ids), "chisq_n": {"h2o": 1.0}})
    src = "\n".join(c["source"] for c in out["notebook"]["cells"])
    assert "fit = MultiFitter()" in src and "fit.add('h2o'" in src
    assert "fit.share('radius', 'radius_pd', 'sld')" in src
    assert "fit.constrain('mix.background', '(h2o.background + d2o.background) / 2')" in src
    assert "result = fit.fit(method='lm')" in src and "fit.save_results(" in src


@pytest.mark.skipif(importlib.util.find_spec("iquana") is None, reason="iquana not installed")
def test_multifitter_notebook_runs_and_matches(contrast, tmp_path, monkeypatch):
    import contextlib
    import io
    from sassafras.notebook import build
    s, ids = contrast
    nb = build(s, request(s, ids))["notebook"]
    monkeypatch.chdir(tmp_path)
    g = {}
    for c in nb["cells"]:
        if c["cell_type"] == "code":
            code = c["source"].replace("fit.plot_model()", "pass").replace("fit.plot_results()", "pass")
            with contextlib.redirect_stdout(io.StringIO()):
                exec(compile(code, "cell", "exec"), g)
    report = str(g["fit"].get_fit_report())
    assert g["result"] is not None and "radius" in report and "derived" in report
    assert (tmp_path / "multi_sphere_h2o_d2o_mix_results").is_dir()
