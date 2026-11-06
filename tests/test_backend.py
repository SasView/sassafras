"""Backend checks. Run with:  python -m pytest tests"""
from pathlib import Path

import numpy as np
import pytest

from sassafras.backend import Session, UserError, q_factor_to_inv_angstrom

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture(scope="module")
def session():
    return Session()


def test_catalogue_lists_form_and_structure_factors(session):
    listing = session.catalogue.listing()
    names = {m["name"] for m in listing["form_factors"]}
    sfs = {m["name"] for m in listing["structure_factors"]}
    assert {"sphere", "cylinder", "core_shell_sphere", "power_law"} <= names
    assert {"hardsphere", "hayter_msa", "squarewell", "stickyhardsphere"} <= sfs
    sphere = next(m for m in listing["form_factors"] if m["name"] == "sphere")
    power = next(m for m in listing["form_factors"] if m["name"] == "power_law")
    assert sphere["accepts_sq"] and not power["accepts_sq"]


@pytest.mark.parametrize("name", sorted(EXAMPLES.glob("*.*")), ids=lambda p: p.name)
def test_every_example_loads(session, name):
    added, _ = session.load_path(str(name))
    assert added and added[0].q.size > 10
    assert np.all(np.diff(added[0].q) > 0)


def test_nm_q_is_converted_to_inverse_angstrom(session):
    added, _ = session.load_path(str(EXAMPLES / "saxs_core_shell_nm.xml"))
    assert added[0].q.min() == pytest.approx(0.005, rel=1e-6)
    assert q_factor_to_inv_angstrom("nm^{-1}") == 0.1
    assert q_factor_to_inv_angstrom("A^{-1}") == 1.0


def test_reproduces_previous_elliptical_cylinder_fit(session):
    """Same model, parameters and dQ as the elliptical_cylinder_gui template:
    the curve must agree with the exported best-fit curve (6 s.f. CSV)."""
    ds = session.load_path(str(EXAMPLES / "flexible_cylinder_elliptical.dat"))[0][0]
    assert ds.dq is not None
    params = {"length": 196.17620245794816, "kuhn_length": 95.80379029291666,
              "radius": 5.491593635342845, "axis_ratio": 3.1314444613729395,
              "sld": 0.37, "sld_solvent": 6.7, "scale": 0.015795258763676597,
              "background": 0.1622545117353932}
    out = session.evaluate({"model": "flexible_cylinder_elliptical", "params": params,
                            "dataset": ds.id})
    first, last = 1.102289e+01, 1.631661e-01   # I_fit at first and last Q in the export
    assert out["theory"][0] == pytest.approx(first, rel=2e-6)
    assert out["theory"][-1] == pytest.approx(last, rel=2e-6)
    # template's native BUMPS chisq was 561.675 with 6 free params over 57 points:
    # BUMPS normalises by (N - p); sassafras reports sum/N.
    assert out["chisq"] * 57 / (57 - 6) == pytest.approx(561.6750944665648, rel=1e-4)


def test_model_without_data_uses_grid(session):
    out = session.evaluate({"model": "sphere", "params": {"radius": 50},
                            "grid": {"qmin": 1e-3, "qmax": 0.5, "n": 200}})
    assert len(out["q"]) == 200 and "chisq" not in out
    assert all(v is not None and v > 0 for v in out["theory"])


def test_product_with_structure_factor_and_pd(session):
    desc = session.describe("sphere", "hardsphere")
    names = [p["name"] for p in desc["parameters"]]
    assert names[:2] == ["scale", "background"]
    assert {"volfraction", "radius_effective", "radius_effective_mode"} <= set(names)
    mode = next(p for p in desc["parameters"] if p["name"] == "radius_effective_mode")
    assert mode["kind"] == "choice" and mode["choices"]
    a = session.evaluate({"model": "sphere", "structure_factor": "hardsphere",
                          "params": {"radius": 50, "volfraction": 0.3}})
    b = session.evaluate({"model": "sphere", "structure_factor": "hardsphere",
                          "params": {"radius": 50, "volfraction": 0.3},
                          "pd": {"radius": {"width": 0.2, "type": "lognormal"}}})
    assert a["theory"] != b["theory"]


def test_vector_parameters_follow_control(session):
    d1 = session.describe("core_multi_shell", None, {"n": 1})
    d3 = session.describe("core_multi_shell", None, {"n": 3})
    n1 = {p["name"] for p in d1["parameters"]}
    n3 = {p["name"] for p in d3["parameters"]}
    assert "thickness1" in n1 and "thickness3" not in n1 and "thickness3" in n3


def test_match_scale_and_background_recovers_truth(session):
    q = np.geomspace(2e-3, 0.4, 150)
    truth = session.evaluate({"model": "sphere", "params": {"radius": 40, "scale": 0.02, "background": 0.05},
                              "grid": {"qmin": q[0], "qmax": q[-1], "n": q.size}})
    rec = dict(label="synthetic", source="upload:synthetic", q=q, i=np.array(truth["theory"]),
               di=0.01 * np.array(truth["theory"]), dq=None)
    ds = session._add_records([rec])[0]
    out = session.match_scale({"model": "sphere", "params": {"radius": 40, "scale": 1, "background": 0},
                               "dataset": ds.id, "background": True})
    assert out["scale"] == pytest.approx(0.02, rel=1e-6)
    assert out["background"] == pytest.approx(0.05, rel=1e-6)


def test_bad_input_is_reported(session):
    with pytest.raises(UserError):
        session.evaluate({"model": "not_a_model", "params": {}})
    with pytest.raises(UserError):
        session.evaluate({"model": "sphere", "params": {"radius": "big"}})
    with pytest.raises(UserError):
        session.describe("sphere", "power_law")


def test_background_estimate_is_mean_of_three_highest_q(session):
    ds = session.load_path(str(EXAMPLES / "flexible_cylinder_elliptical.dat"))[0][0]
    expected = (1.635490e-01 + 1.613380e-01 + 1.581890e-01) / 3
    assert ds.summary()["bkg_estimate"] == pytest.approx(expected, rel=1e-9)
