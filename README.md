# Sassafras

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.24041504.svg)](https://doi.org/10.5281/zenodo.24041504)
[![PyPI](https://img.shields.io/pypi/v/sassafras)](https://pypi.org/project/sassafras/)

**Small Angle Scattering Structure- And Form-factor Rapid ASsessment**

A quick-look SANS/SAXS tool for checking samples at the beamline. Load reduced
1D data, pick any sasmodels form factor (optionally × a structure factor) and
drag sliders to overlay the model, with live χ² and residuals. From there you
can fit with BUMPS, fit several datasets together, let **Guess** suggest a
model, and export the whole state as an IQuana notebook.

Sassafras runs locally as a browser GUI (no external JavaScript) and is part of
the [SasView](https://www.sasview.org) ecosystem, alongside sasmodels, sasdata
and [IQuana](https://github.com/SasView/IQuana).

## Install and run

```bash
pip install sassafras
sassafras                                   # or: python -m sassafras
sassafras run_1234.xml run_1235.h5 -m core_shell_sphere -s hardsphere
sassafras /data/reduced/                    # every readable file in a folder (≤ 50)
```

A browser tab opens on `http://127.0.0.1:…`; stop with Ctrl+C or **Stop
server**. Nothing leaves your machine except the one-off curve-library
download for Guess (~290 MB, in the background on first start; `--no-fetch`
skips it).

* Options: `--port 8765` · `--no-browser` · `-m MODEL` · `-s STRUCTURE_FACTOR` ·
  `--notebook-dir DIR` · `--no-fetch` · `--check` (load, compile, evaluate
  once, print a JSON summary, exit).
* sasmodels needs a C compiler (Xcode command-line tools on macOS). Each model
  compiles once (1–2 s) and is cached.
* Models run in single precision (1.2–1.6× faster, ~1e-5 relative difference),
  except those sasmodels marks as needing double, such as most S(Q).

## Data

* Drop files on the page, browse, or type a file or folder path (path-loaded
  files can be re-read with ↻ while reduction is still writing them).
* Any format sasdata reads: canSAS XML, NXcanSAS, ASCII/CSV, IGOR `.abs/.cor`,
  Anton Paar `.pdh`, … Q is shown in Å⁻¹ (nm⁻¹ files are converted); points
  with Q ≤ 0, non-finite values or dI ≤ 0 are dropped with a note; 2D data are
  skipped.
* The **active** dataset (radio) supplies the Q points and resolution (pinhole
  dQ or slit, as in SasView) and is the one scored. Other ticked datasets are
  drawn for comparison. With no dataset the model is drawn on a log-spaced Q
  grid.

## Model and parameters

* All sasmodels 1D form factors, grouped and filterable; any S(Q)
  (hardsphere, hayter_msa, …) via sasmodels' `model@sf`, including
  `radius_effective_mode` and the β approximation.
* Each parameter has a value box, a slider with editable min/max and a **log**
  toggle; **PD** opens its polydispersity (the button lights up once the width
  is above 0).
* Values carry over between models by name; each model remembers its own
  settings, and the last state is restored on the next launch.
* **Match**: **Scale** or **Scale+bg** solve scale (and background) by weighted
  least squares in the χ² window; **bg** sets background from the 3
  highest-Q points (done once automatically for each new dataset).
* **Curve**: **Pin** keeps the current curve as a dashed reference (with its
  χ²); **CSV ↓** exports Q, I, dI, model and residuals.
* **Parameters**: **Save** / **Load** a JSON file with the model, values, PD,
  slider ranges, Q window, fit setup, plot type and package versions.

## Fitting

Switch on **Fit** (Parameters panel), tick the parameters to vary (PD widths
too), and press **Fit**. The slider min/max are the bounds. Methods:
Levenberg–Marquardt (default, ± from the covariance), Nelder–Mead simplex,
differential evolution, and DREAM (± from the posterior).

The fit runs in the background (**Cancel** keeps the best point so far). When
it finishes the sliders move, each fitted value shows ±1σ (⚑ if it hit a
bound), the starting curve is pinned and **Undo fit** restores it. **Fit χ²ᵣ**
is the reduced χ², Σ((I − model)/dI)²/(N − p); it and the ± values are struck
through once you change anything by hand.

## Mix

Tick **Mix** (Model panel) to sum up to four form factors, e.g. particles plus
a low-Q power law. Only the first can carry an S(Q). This is a sasmodels
mixture (`sphere@hardsphere+power_law`): one global scale and background, with
the other parameters shown as **radius ₂**, **power ₂**, … (sasmodels names
`A_`, `B_`, … in fits, files and notebooks). Fit, Match, Multi and the
notebook export all work with mixtures.

## Multi

Tick **Multi** to work on all ticked datasets at once, e.g. a contrast or
concentration series, or SANS + SAXS.

* Each parameter is **all** (one shared value) or **each** (per dataset;
  scale, background and solvent SLDs start as each). Click a dataset's radio to
  edit its values.
* **ƒ** sets an *each* parameter by an expression, e.g. `0.8 * h2o.scale` or
  `(h2o.background + d2o.background) / 2`, using the dataset names shown in
  the panel. Expressions are parsed, never executed; cycles are refused.
* **Fit** runs one joint fit; χ²/N, pinned curves and residuals cover every
  dataset, each in its own colour.
* **Save**/**Load**, **Copy** and the notebook (an IQuana `MultiFitter`)
  include every dataset.

## Guess

**Guess** (Model panel) matches the active dataset, inside the χ² window,
against about 1.3 million simulated curves, ranks the models by their closest
curve (shape only: each curve gets its own scale and background), and loads
the best one with starting values. Click another row to load that model
instead. Treat the result as a starting point for the sliders or Fit.

* **Starting values** come from the model's three closest curves, then a
  short polydispersity scan and LM polish. SLDs aren't in the library, so the
  defaults are kept and scale absorbs the contrast.
* **The library** is [SAS-55M-20k](https://doi.org/10.6084/m9.figshare.21716618)
  (55 form factors × 20,000 curves, Q 0.001–1 Å⁻¹) plus an S(Q) sub-library of
  20 particle form factors × `hardsphere` and `hayter_msa` at weak and strong
  interaction (~205,000 curves). Two-population mixtures aren't covered.
* **Download**: automatic on first start, checksum-verified, from
  [Zenodo](https://doi.org/10.5281/zenodo.24080759) (GitHub as a mirror) into
  a per-user folder that survives upgrades (`~/Library/Application
  Support/sassafras` on macOS, `~/.local/share/sassafras` on Linux,
  `%LOCALAPPDATA%\sassafras` on Windows; override with `SASSAFRAS_DATA`). To
  get it by hand, e.g. on an offline machine: `python -m sassafras.library fetch`.
* SAS-55M-20k is by B. Yildirim, J. Doutch and J. M. Cole (*Digital
  Discovery* **3**, 694, 2024), MIT-licensed.

## IQuana notebook

**IQuana notebook ↗** writes a Jupyter notebook with
[IQuana](https://sasview.github.io/IQuana/) code that reproduces the screen
(data, model, values, PD, Q window, resolution and, with Fit on, the same fit
setup) and opens it in JupyterLab. Notebooks go to `~/sassafras_notebooks`.
A check cell compares IQuana's χ²/N with sassafras's.

* Uploaded or rescaled data are saved next to the notebook as CSV.
* `radius_effective_mode` maps to IQuana where it can; other computed modes
  are fixed at the current value, and the notebook says so.
* Models with per-shell vector parameters (e.g. `core_multi_shell`) can't be
  exported yet.

## Copy and plot

* **Copy to clipboard**: **All** (figure + parameter table, for OneNote, an
  ELN or Word), **Figure**, **Params**, **Data** (columns for Excel), or
  **PNG ↓**.
* **Plot type**: I(Q), Kratky (Q²I vs Q) or Guinier (ln I vs Q²). Drag to zoom
  (double-click to reset), hover for values; the χ² window greys out points
  outside it.
* χ²/N = Σ((I − model)/dI)² / N in the χ² window, a number for comparing
  settings by eye. Multiply by N/(N − p) to compare with a reduced χ².
* **Theme** (auto/light/dark) and **Colour** (chopped, cooked, washed, chalked,
  girlypop) are remembered by the browser.

## Citing

Please cite [doi:10.5281/zenodo.24041504](https://doi.org/10.5281/zenodo.24041504),
which always resolves to the latest version (each release has its own DOI
there), or use GitHub's "Cite this repository".

## Contributing and licence

Issues and pull requests are welcome; see
[CONTRIBUTING.md](https://github.com/SasView/sassafras/blob/main/CONTRIBUTING.md)
and the [changelog](https://github.com/SasView/sassafras/blob/main/CHANGELOG.md).
BSD 3-Clause ([LICENSE](https://github.com/SasView/sassafras/blob/main/LICENSE)).
