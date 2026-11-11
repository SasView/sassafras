# Contributing to sassafras

Sassafras is part of [SasView](https://www.sasview.org) and follows its code of conduct.

- Report bugs and ideas as [issues](https://github.com/SasView/sassafras/issues); a data file
  (metadata removed if needed) and the model string help.
- Open pull requests against `dev`. `main` only changes when a release is merged.

```bash
git clone https://github.com/SasView/sassafras && cd sassafras
git switch dev
pip install -e ".[test]"
python -m pytest -q
```

The tests (Linux and macOS, Python 3.10–3.13) build a tiny synthetic curve library and never
download the real one. `examples/` has measured flexible elliptical cylinder SANS data (metadata
removed), synthetic sphere × hard-sphere data (canSAS XML, NXcanSAS) and a SAXS set in nm⁻¹.

## Layout

```
sassafras/backend.py   data loading (sasdata), model catalogue, evaluation (sasmodels)
sassafras/server.py    stdlib HTTP server and CLI
sassafras/fit.py       BUMPS fitting jobs, including joint Multi fits
sassafras/multi.py     Multi request checks and safe expressions
sassafras/notebook.py  IQuana notebook export and JupyterLab launcher
sassafras/guess.py     Guess: library ranking and starting values
sassafras/library.py   curve library: build, pack, fetch, matching
sassafras/static/      the browser GUI (plain JavaScript, SVG plots)
```

The backend works without the GUI:

```python
from sassafras.backend import Session
s = Session()
ds, _ = s.load_path("run.xml")
s.evaluate({"model": "sphere", "structure_factor": "hardsphere",
            "params": {"radius": 60, "volfraction": 0.2}, "dataset": ds[0].id})["chisq"]
```

## Releasing (maintainers)

1. On `dev`: update `CHANGELOG.md` and the version in `pyproject.toml`, `sassafras/__init__.py`
   and `CITATION.cff`. Optionally push a tag `vX.Y.Zrc1` to try the package on TestPyPI.
2. Open a pull request `dev` → `main` and merge it when the tests pass.
3. Publish a GitHub release `vX.Y.Z` on `main`. After approval in the `pypi` environment it goes
   to PyPI, and Zenodo archives it under the concept DOI 10.5281/zenodo.24041504.
4. `git switch dev && git merge main && git push`.

## Rebuilding the curve library

Download [SAS-55M-20k](https://doi.org/10.6084/m9.figshare.21716618) (~4.5 GB), then:

```bash
pip install -e ".[library]"                                  # pyarrow
python -m sassafras.library build ~/Downloads/sas-55m-20k     # ~20 s, ~400 MB
python -m sassafras.library build-sq ~/Downloads/sas-55m-20k  # S(Q) sub-library, ~30 s, ~90 MB
python -m sassafras.library pack sassafras-library-vN.tar.gz
```

Upload the archive as a new version of the [library's Zenodo record](https://doi.org/10.5281/zenodo.24080759)
(and a `library-vN` GitHub release as a mirror), then update `RELEASE_TAG`, `RELEASE_FILE`,
`RELEASE_SHA256` and `ZENODO_URL` in `sassafras/library.py`. How the S(Q) sub-library is made (which form
factors; φ, charge and salt ranges) is set by `SQ_CONFIGS` and `build_sq` in that file.
