# Changelog

## 0.5.0 — first release under SasView

- Moved to [github.com/SasView/sassafras](https://github.com/SasView/sassafras) and published on PyPI
  (`pip install sassafras`, extras `fit`, `notebook`, `guess`, `all`).
- The curve library for Guess is downloaded (`python -m sassafras.library fetch`) into a per-user
  data folder instead of the package folder, from Zenodo (doi:10.5281/zenodo.24080759) with GitHub as a mirror; indexes in the
  old in-package location are still found.
- IQuana links point to its SasView home.
- Continuous integration (Linux and macOS) and trusted-publishing release workflows.

## 0.4.x

- **Mix:** sums of up to four form factors (sasmodels mixtures), with the first able to take an S(Q),
  in fitting, Multi and the IQuana notebook.
- **Multi:** simultaneous constrained fitting of several datasets (shared, per-dataset and
  expression parameters) with IQuana `MultiFitter` export.
- Copy to clipboard (renamed from "Copy to notebook"); Zenodo and `CITATION.cff` metadata.

## 0.3.x

- Guess: curve-library lookup against SAS-55M-20k plus an S(Q) sub-library, with the ml-sans
  network as fallback; IQuana notebook export; colour schemes; compact action bar.

## 0.2.x

- Optional BUMPS fitting, Kratky and Guinier plots.
