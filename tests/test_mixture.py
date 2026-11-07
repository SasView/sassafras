"""Mix: sums of up to four form factors (the first may carry an S(Q))."""
import contextlib
import importlib.util
import io

import numpy as np
import pytest

from sassafras.backend import Session, UserError
from sassafras.notebook import build


@pytest.fixture(scope="module")
def session_ds(tmp_path_factory):
    from sasmodels.core import load_model
    from sasmodels.data import empty_data1D
    from sasmodels.direct_model import DirectModel
    q = np.geomspace(0.005, 0.4, 100)
    i = DirectModel(empty_data1D(q, resolution=0.05), load_model("sphere+power_law"))(
        A_radius=40, A_sld=1, A_sld_solvent=-0.56, scale=0.01, background=0.03, B_scale=2e-7, B_power=3.5)
    f = tmp_path_factory.mktemp("mix") / "mix.dat"
    np.savetxt(f, np.c_[q, i, 0.03 * i, 0.05 * q])
    s = Session()
    return s, s.load_path(str(f))[0][0]


def test_full_name_rules():
    cat = Session().catalogue
    assert cat.full_name("sphere", "hardsphere", ["cylinder", "power_law"]) == "sphere@hardsphere+cylinder+power_law"
    with pytest.raises(UserError, match="At most 4"):
        cat.full_name("sphere", None, ["cylinder", "dab", "power_law", "guinier"])
    with pytest.raises(UserError):
        cat.full_name("sphere", None, ["hardsphere"])          # an S(Q) is not a form factor


def test_describe_and_fit_mixture(session_ds):
    from sassafras.fit import FitJob
    s, ds = session_ds
    d = s.describe("sphere", None, {}, ["power_law"])
    names = [p["name"] for p in d["parameters"]]
    assert names[:2] == ["scale", "background"] and "A_radius" in names and "B_power" in names
    p = {x["name"]: x["default"] for x in d["parameters"]}
    p.update(A_radius=35, A_sld=1, A_sld_solvent=-0.56, scale=0.01, background=0.03, B_scale=1e-7, B_power=3.5)
    job = FitJob(s, {"model": "sphere", "structure_factor": None, "components": ["power_law"], "params": p, "pd": {},
                     "dataset": ds.id, "method": "lm", "window": [None, None],
                     "free": {"A_radius": [10, 100], "B_scale": [0, 1e-5], "background": [0, 0.1]}})
    job.start()
    job.thread.join()
    assert job.state == "done", job.error
    assert job.result["values"]["A_radius"] == pytest.approx(40, rel=0.01)
    assert job.result["values"]["B_scale"] == pytest.approx(2e-7, rel=0.05)


def _req(s, ds):
    d = s.describe("sphere", "hardsphere", {}, ["power_law"])
    p = {x["name"]: x["default"] for x in d["parameters"]}
    p.update(A_radius=40, A_sld=1, A_sld_solvent=-0.56, A_volfraction=0.1, A_radius_effective_mode=1,
             scale=0.01, background=0.03, B_scale=2e-7, B_power=3.5)
    return {"model": "sphere", "structure_factor": "hardsphere", "components": ["power_law"], "params": p,
            "pd": {"A_radius": {"width": 0.05, "type": "schulz", "n": 35}}, "dataset": ds.id,
            "free": {"A_radius": [10, 100]}, "method": "lm", "window": [None, None]}


def test_notebook_uses_the_raw_mixture(session_ds):
    s, ds = session_ds
    src = "\n".join(c["source"] for c in build(s, _req(s, ds))["notebook"]["cells"])
    assert "fitter.set_model('sphere@hardsphere+power_law')" in src
    assert "set_structure_factor" not in src and "fitter.set_param('A_radius_effective_mode'" in src
    assert "fitter.set_pd_param('A_radius'" in src


@pytest.mark.skipif(importlib.util.find_spec("iquana") is None, reason="iquana not installed")
def test_mixture_notebook_reproduces_chisq(session_ds, tmp_path, monkeypatch):
    s, ds = session_ds
    req = _req(s, ds)
    chi = s.evaluate(req)["chisq"]
    nb = build(s, {**req, "chisq_n": chi})["notebook"]
    monkeypatch.chdir(tmp_path)
    g, out = {}, io.StringIO()
    for c in nb["cells"]:
        if c["cell_type"] != "code" or "fitter.fit(" in c["source"] or "plot_results" in c["source"] or "report" in c["source"]:
            continue
        code = c["source"].replace("fitter.plot_model(show_residuals=True, log_scale=True)", "pass")
        with contextlib.redirect_stdout(out):
            exec(compile(code, "cell", "exec"), g)
    printed = float(out.getvalue().split("χ²/N = ")[1].split()[0])
    assert printed == pytest.approx(chi, rel=1e-3)
