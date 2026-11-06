"""Curve-library Guess, on a tiny synthetic index (no SAS-55M-20k download needed)."""
import json

import numpy as np
import pytest

from sassafras.backend import Session
from sassafras.guess import Guesser
from sassafras.library import Library

Q = np.geomspace(1e-3, 1.0, 128)


def _curves(model, rng, n, draw):
    from sasmodels.core import load_model
    from sasmodels.data import empty_data1D
    from sasmodels.direct_model import DirectModel
    calc = DirectModel(empty_data1D(Q), load_model(model))
    pars = [draw(rng) for _ in range(n)]
    return [np.log10(calc(**p, background=0.001)) for p in pars], pars


@pytest.fixture(scope="module")
def index(tmp_path_factory):
    rng = np.random.default_rng(1)
    out = tmp_path_factory.mktemp("lib")
    sph, sp = _curves("sphere", rng, 60, lambda r: {"radius": float(10 ** r.uniform(1, 2.5))})
    cyl, cp = _curves("cylinder", rng, 60, lambda r: {"radius": float(10 ** r.uniform(1, 2)),
                                                      "length": float(10 ** r.uniform(2, 3))})
    logI = np.array(sph + cyl, np.float32)
    np.save(out / "logI.npy", (logI - logI.max(axis=1, keepdims=True)).astype(np.float16))
    np.save(out / "model.npy", np.array([0] * 60 + [1] * 60, np.uint8))
    params = np.full((120, 2), np.nan, np.float32)
    params[:60, 0] = [p["radius"] for p in sp]
    params[60:, 0] = [p["radius"] for p in cp]
    params[60:, 1] = [p["length"] for p in cp]
    np.save(out / "params.npy", params)
    (out / "meta.json").write_text(json.dumps({
        "source": "test", "n": 120, "q": Q.tolist(), "models": ["sphere", "cylinder"],
        "params": {"sphere": ["radius"], "cylinder": ["radius", "length"]},
        "pd": {"sphere": ["radius"], "cylinder": ["radius"]}}))
    return out


@pytest.fixture()
def cylinder_data():
    from sasmodels.core import load_model
    from sasmodels.data import empty_data1D
    from sasmodels.direct_model import DirectModel
    q = np.geomspace(0.004, 0.4, 90)
    i = DirectModel(empty_data1D(q), load_model("cylinder"))(radius=35.0, length=400.0, scale=0.02, background=0.05)
    di = 0.03 * i
    csv = "\n".join(f"{a:.8g},{b:.8g},{c:.8g}" for a, b, c in zip(q, i, di))
    s = Session()
    ds = s.load_bytes("cyl.csv", csv.encode())[0][0]
    return s, ds


def test_match_ranks_the_right_model(index, cylinder_data):
    s, ds = cylinder_data
    best = Library(index).match(ds.q, ds.i, ds.di)
    assert next(iter(best)) == "cylinder"


def test_guess_uses_library_and_loads_parameters(index, cylinder_data):
    s, ds = cylinder_data
    g = Guesser(s, library=str(index))
    assert g.status()["source"] == "library"
    r = g.suggest({"dataset": ds.id})
    assert r["source"] == "library" and r["suggestions"][0]["model"] == "cylinder"
    v = r["values"]
    assert v["source"] == "library" and v["chisq_n"] is not None
    assert v["params"]["radius"] == pytest.approx(35, rel=0.25)
    other = g.starting_values({"dataset": ds.id, "model": "sphere"})
    assert other["source"] == "library" and "radius" in other["params"]


def test_library_can_be_disabled(index):
    assert Guesser(Session(), library=False).library_dir is None
    assert Library.find(str(index)) == index.resolve()
