"""Guess button. Needs the ml-sans folder (found next to sassafras, via
$SASSAFRAS_ML_SANS, or ./ml-sans); the network tests also need torch."""
import importlib.util
from pathlib import Path

import pytest

from sassafras.backend import Session, UserError
from sassafras.guess import Guesser

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture(scope="module")
def setup():
    s = Session()
    g = Guesser(s)
    if not g.folder:
        pytest.skip("ml-sans folder not found")
    ds = s.load_path(str(EXAMPLES / "sphere_hardsphere.xml"))[0][0]
    return s, g, ds


def test_missing_folder_is_reported():
    g = Guesser(Session(), "/nonexistent/ml-sans")
    if g.folder is None:
        assert not g.status()["available"] and "ml-sans" in g.status()["reason"]


def test_prior_search_finds_the_sphere(setup):
    s, g, ds = setup
    v = g.starting_values({"model": "sphere", "structure_factor": "hardsphere", "dataset": ds.id})
    assert v["draws"] > 50 and v["chisq_n"] is not None
    if importlib.util.find_spec("bumps"):          # with the LM polish the truth (R = 60 Å, φ = 0.2) is recovered
        assert v["params"]["radius"] == pytest.approx(60, rel=0.05)
        assert v["params"]["volfraction"] == pytest.approx(0.2, rel=0.15)
        assert v["chisq_n"] < 10
    # the result must be accepted by the evaluator as-is
    out = s.evaluate({"model": "sphere", "structure_factor": "hardsphere", "params": v["params"],
                      "pd": v["pd"], "dataset": ds.id})
    assert out["chisq"] == pytest.approx(v["chisq_n"], rel=1e-6)


def test_network_ranks_and_flags_unloadable(setup):
    s, g, ds = setup
    if importlib.util.find_spec("torch") is None:
        pytest.skip("torch not installed")
    r = g.suggest({"dataset": ds.id, "with_values": False})
    sug = r["suggestions"]
    assert len(sug) >= 3 and abs(sum(x["probability"] for x in sug) - 1) < 0.5
    assert any(x["model"].startswith("sphere") for x in sug[:3])
    for x in sug:
        assert x["loadable"] == ("+" not in x["model"]) or not x["loadable"]


def test_needs_a_dataset(setup):
    s, g, ds = setup
    with pytest.raises(UserError):
        g.suggest({"dataset": None})
