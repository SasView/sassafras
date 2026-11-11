"""Guess without a curve library: it says what to do instead of failing."""
import pytest

from sassafras.backend import Session, UserError
from sassafras.guess import Guesser


def test_without_a_library_guess_explains_how_to_get_one():
    s = Session()
    g = Guesser(s, library=False)
    st = g.status()
    assert not st["available"] and "python -m sassafras.library fetch" in st["reason"]
    ds = s.load_path(str(__import__("pathlib").Path(__file__).resolve().parents[1] / "examples" / "sphere_hardsphere.xml"))[0][0]
    with pytest.raises(UserError, match="fetch"):
        g.suggest({"dataset": ds.id})
    with pytest.raises(UserError, match="fetch"):
        g.starting_values({"dataset": ds.id, "model": "sphere"})


def test_needs_a_dataset():
    with pytest.raises(UserError):
        Guesser(Session(), library=False).suggest({"dataset": None})
