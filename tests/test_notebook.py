"""IQuana notebook export. Building is always tested; executing the notebook
needs iquana, nbclient and ipykernel and is skipped otherwise."""
import json
import re
from pathlib import Path

import pytest

from sassafras.backend import Session, UserError
from sassafras.notebook import Exporter, build

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture(scope="module")
def session():
    return Session()


def request_for(session, ds, model, sf=None, **over):
    desc = session.describe(model, sf)
    params = {p["name"]: p["default"] for p in desc["parameters"]}
    params.update(over.pop("params", {}))
    return {"model": model, "structure_factor": sf, "params": params, "dataset": ds.id, **over}


def sources(nb):
    return "\n".join(c["source"] for c in nb["cells"] if c["cell_type"] == "code")


def test_translation_of_model_sf_params_pd_window_and_fit(session):
    ds = session.load_path(str(EXAMPLES / "sphere_hardsphere.xml"))[0][0]
    req = request_for(session, ds, "sphere", "hardsphere", method="amoeba", window=[0.004, 0.3],
                      params={"radius": 58.5, "radius_effective_mode": 0, "radius_effective": 61.0},
                      pd={"radius": {"width": 0.1, "type": "lognormal"}},
                      free={"radius": [10, 200], "scale": [1e-4, 1.0], "radius_pd": [0, 1]})
    code = sources(build(session, req)["notebook"])
    assert f"fitter.load_data({str(EXAMPLES / 'sphere_hardsphere.xml')!r})" in code
    assert "fitter.set_structure_factor('hardsphere', radius_effective_mode='unconstrained')" in code
    assert re.search(r"set_param\('radius',\s+value=58\.5, min=10\.0, max=200\.0, vary=True\)", code)
    assert re.search(r"set_param\('radius_effective',\s+value=61\.0, vary=False\)", code)
    assert "set_param('radius_effective_mode'" not in code          # IQuana owns the kernel switch
    assert "fitter.set_pd_param('radius', pd_width=0.1, pd_n=35, pd_nsigma=3.0, pd_type='lognormal', vary=True)" in code
    assert "fitter.set_q_range(qmin=0.004, qmax=0.3)" in code
    assert "fitter.fit(engine='bumps', method='amoeba')" in code


def test_radius_mode_links_and_computed_modes_fix_reff(session):
    ds = session.load_path(str(EXAMPLES / "sphere_hardsphere.xml"))[0][0]
    linked = sources(build(session, request_for(session, ds, "sphere", "hardsphere",
                                                params={"radius_effective_mode": 1}))["notebook"])
    assert "radius_effective_mode='link_radius'" in linked and "set_param('radius_effective'" not in linked
    # cylinder: mode 2 = equivalent volume sphere -> R_eff = (3/4 r² L)^(1/3)
    out = build(session, request_for(session, ds, "cylinder", "hardsphere",
                                     params={"radius": 20.0, "length": 300.0, "radius_effective_mode": 2}))
    r_eff = (0.75 * 20.0 ** 2 * 300.0) ** (1 / 3)
    m = re.search(r"set_param\('radius_effective',\s+value=([0-9.]+), vary=False\)", sources(out["notebook"]))
    assert m and float(m.group(1)) == pytest.approx(r_eff, rel=1e-6)
    assert any("equivalent volume sphere" in n for n in out["notes"])


def test_uploads_become_a_csv_snapshot_and_vectors_are_refused(session, tmp_path):
    raw = (EXAMPLES / "sphere_hardsphere.xml").read_bytes()
    ds = session.load_bytes("upload.xml", raw)[0][0]
    out = Exporter(session, str(tmp_path)).export(dict(request_for(session, ds, "sphere"), launch=False))
    csv = [Path(f) for f in out["files"]]
    assert len(csv) == 1 and csv[0].read_text().splitlines()[0] == "Q_invA,I,dI,dQ"
    assert len(csv[0].read_text().splitlines()) == 1 + ds.q.size
    nb = json.loads(Path(out["path"]).read_text())
    assert f"fitter.load_data({csv[0].name!r})" in sources(nb)
    with pytest.raises(UserError, match="vector"):
        build(session, request_for(session, ds, "core_multi_shell"))


def test_exported_notebook_runs_and_reproduces_chisq(session, tmp_path):
    pytest.importorskip("iquana")
    pytest.importorskip("ipykernel")
    nbclient = pytest.importorskip("nbclient")
    nbformat = pytest.importorskip("nbformat")
    ds = session.load_path(str(EXAMPLES / "flexible_cylinder_elliptical.dat"))[0][0]
    params = {"length": 196.17620245794816, "kuhn_length": 95.80379029291666, "radius": 5.491593635342845,
              "axis_ratio": 3.1314444613729395, "sld": 0.37, "sld_solvent": 6.7,
              "scale": 0.015795258763676597, "background": 0.1622545117353932}
    req = request_for(session, ds, "flexible_cylinder_elliptical", params=params)
    req["chisq_n"] = session.evaluate(req)["chisq"]
    out = Exporter(session, str(tmp_path)).export(dict(req, launch=False))
    nb = nbformat.read(out["path"], as_version=4)
    nbclient.NotebookClient(nb, timeout=300, kernel_name="python3",
                            resources={"metadata": {"path": str(tmp_path)}}).execute()
    text = "".join(o.get("text", "") for c in nb.cells for o in c.get("outputs", []) if o.get("name") == "stdout")
    m = re.search(r"χ²/N = ([0-9.eE+-]+)", text)
    assert m, text[-500:]
    assert float(m.group(1)) == pytest.approx(req["chisq_n"], rel=1e-3)   # single vs double precision
