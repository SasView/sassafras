# Contributing to sassafras

Sassafras is part of the [SasView](https://www.sasview.org) project and follows the SasView
project's code of conduct (see the [SasView repository](https://github.com/SasView/sasview)).

- Report bugs and ideas as [issues](https://github.com/SasView/sassafras/issues); a data file
  (with metadata removed if needed) and the model string help a lot.
- For changes, open a pull request against `main`. Tests run on Linux and macOS for every PR.

```bash
git clone https://github.com/SasView/sassafras && cd sassafras
pip install -e ".[test]"
python -m pytest -q
```

The tests never download the 290 MB curve library: they build a tiny synthetic one. To try Guess
with the real library, run `python -m sassafras.library fetch` once.

## Releasing (maintainers)

1. Update `CHANGELOG.md` and bump the version in `pyproject.toml`, `sassafras/__init__.py` and
   `CITATION.cff`.
2. Push a tag `vX.Y.Zrc1` to try the package on TestPyPI.
3. Publish a GitHub release `vX.Y.Z`. After approval in the `pypi` environment it goes to PyPI,
   and Zenodo archives it under the concept DOI 10.5281/zenodo.24041504.

A rebuilt curve library is released separately: `python -m sassafras.library pack`, a new version
of its Zenodo record (plus a `library-vN` GitHub release as a mirror), then update `RELEASE_TAG`,
`RELEASE_FILE`, `RELEASE_SHA256` and `ZENODO_URL` in `sassafras/library.py`.
