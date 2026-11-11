# Changelog

## 0.6.0

- **Faster models:** kernels run in single precision (1.2–1.6× faster, ~1e-5 relative difference)
  wherever sasmodels allows it; models it marks as needing double (most S(Q), e.g. `hardsphere`,
  `hayter_msa`) stay in double.
- **Parameter files** also save the fit ticks and method, plot type, model Q grid, dataset details
  and package versions; loading one restores the ticks so Fit works straight away.
- **Multi:** loading a saved multi-fit uses one dataset per entry (the latest copy if a file was
  loaded twice) and unticks the rest, fixing wrong per-dataset values when files were loaded twice.
- **Multi:** each dataset gets its own pinned curve, drawn faded in its own colour, with a combined
  pinned χ².
- Compact score readouts; pinning or fitting no longer moves the action buttons.

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
