# Sassafras

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.24041504.svg)](https://doi.org/10.5281/zenodo.24041504)

**Small Angle Scattering Structure- And Form-factor Rapid ASsessment**

A lightweight SANS/SAXS analysis tool. Load reduced 1D data
in any format sasdata reads, pick a sasmodels form factor (optionally × a
structure factor), and drag sliders to overlay the model on the data —
chi-by-eye, with live χ² and residuals. With the **Fit** toggle off there is
no optimiser; the only automatic step is a one-click linear solve for scale
and background. Switch **Fit** on to refine the chi-by-eye starting point
with BUMPS (needs the optional `bumps` package).

It is a generalised local browser GUI with no external JavaScript, working with 
any model and any (including no) data file.

Sassafras is part of the [SasView](https://www.sasview.org) ecosystem,
alongside sasmodels, sasdata and [IQuana](https://github.com/SasView/IQuana).

## Install

```bash
pip install sassafras                     # viewer: sasmodels, sasdata, numpy
pip install "sassafras[all]"              # + fitting (bumps), IQuana notebooks, Guess (torch)
python -m sassafras.library fetch         # optional: the ~290 MB curve library for Guess
```

Extras on their own: `fit` (bumps), `notebook` (IQuana, JupyterLab), `guess`
(torch, scipy, IQuana). From a clone, `pip install -e ".[all,test]"`.

## Run

```bash
sassafras                                 # or: python -m sassafras
sassafras run_1234.xml run_1235.h5 -m core_shell_sphere -s hardsphere
sassafras /data/reduced/                  # load every readable file in a folder (≤ 50)
```

A browser tab opens automatically. If it doesn't, open the printed
`http://127.0.0.1:…` URL. Keep the terminal running, and stop with Ctrl+C or the
**Stop server** button. Everything runs on your machine, and the server only
listens on 127.0.0.1.

Options: `--port 8765` · `--no-browser` · `-m MODEL` · `-s STRUCTURE_FACTOR` ·
`--check` (load, compile, evaluate once, print a JSON summary, exit).

sasmodels needs a C compiler to build model kernels (Xcode command-line tools
on macOS). The first use of each model compiles it, which takes about 1–2 s,
and sasmodels caches the result after that. OpenCL is off by default
(`SAS_OPENCL=none`); set that variable yourself to change it.

## Data

* **Drop files** on the page, click to browse, or type a **file or folder
  path** (path loads can later be re-read with ↻, which is useful while
  reduction is still rewriting a file).
* Formats: whatever sasdata's `Loader` supports — canSAS XML, NXcanSAS
  (`.h5 .hdf5 .nxs`), ASCII/CSV (`.txt .dat .csv`), IGOR `.abs/.cor`, Anton
  Paar `.pdh`, and others. Multi-entry files give one dataset per entry. 2D data
  are skipped with a message.
* Q is shown in Å⁻¹. sasdata converts nm⁻¹ files on load.
* Points with Q ≤ 0, non-finite values or dI ≤ 0 are dropped, and the dataset's
  tooltip says so.
* **Active** dataset (radio button): its Q points and resolution are used for
  the model, and it is the one scored. Pinhole dQ or slit (dxl/dxw) smearing
  is applied by sasmodels exactly as in SasView.
* **Shown** datasets (colour swatch): drawn for comparison, e.g. sample vs
  buffer, or successive runs.
* **Model only**: evaluates on a log-spaced grid (Q min, Q max, points in the
  toolbar). You can still show datasets underneath.

## Model and parameters

* Form factor list = all sasmodels 1D models, grouped by category, with a
  filter box (type, then Enter).
* S(Q) = hardsphere, hayter_msa, squarewell, stickyhardsphere, …, combined as
  `model@sf` by sasmodels, so you get `radius_effective`, `volfraction`,
  `radius_effective_mode` and, where supported, `structure_factor_mode` (β
  approximation). `radius_effective` is greyed out unless its mode is
  "unconstrained".
* Every float parameter has an exact-value box, a slider, editable slider
  min/max boxes and a **log** toggle. Initial ranges come from the sasmodels
  defaults and limits. If you type a value outside the slider range, the range
  widens to include it. Values outside sasmodels' hard limits are refused.
* Choice parameters get a dropdown. Shell counts such as `n` in
  `core_multi_shell` rebuild the parameter list.
* **PD** opens polydispersity (σ/mean, distribution type; 35 points, 3σ) for
  any polydisperse parameter.
* Switching model keeps values for parameters with the same name (scale,
  background, sld_solvent, radius, …) but not polydispersity. Each model also
  remembers its own last settings for the session, and the last model and
  values are restored on the next launch in the same browser.
* **Background guess on load:** when a newly loaded dataset becomes active,
  the background box is set to the mean I of its 3 highest-Q points
  (once per file, so reloading the page keeps any value you've typed since).
  **bg** (Match) re-applies it.
* **Scale / Scale+bg** (Match): a weighted linear least-squares solve
  over the χ² window, with the model shape fixed. It's for getting the curve
  onto the data before you start adjusting by eye.
* **Pin** (Curve) keeps the current curve, residuals and χ² as a dashed
  reference to compare against.
* **Save / Load** (Parameters; JSON: model, values, PD, slider ranges, Q window)
  and **CSV ↓** (Curve; Q, I, dI, model, residual, in-window flag).
* These buttons sit in a compact bar at the top right of the plot panel, next
  to the χ²/N readout; hover any of them for the full description.

## Fitting (optional)

Install `bumps` (`pip install "sassafras[fit]"`) and restart
sassafras. A **Fit** toggle next to **Defaults** in the Parameters panel then shows the fitting controls. With
it off, or without bumps installed, the page is the plain chi-by-eye viewer.

1. Get close by eye: sliders, **Bkg from high Q**, **Match scale + bkg**.
2. Tick the parameters to vary. A **fit** tick also appears in each open PD
   panel. Ticks are remembered per model.
3. **The slider's min/max boxes are the fit bounds** (0–1 for PD widths), so
   narrow them to keep the fit physical.
4. Pick a method and press **Fit**:
   * **Levenberg–Marquardt**: fast, local, with ± from the covariance. The
     default.
   * **Nelder–Mead simplex**: local and robust to rough χ² surfaces.
   * **Differential evolution**: global search within the bounds. Slower.
   * **DREAM**: MCMC, so the ± values come from the posterior. The slowest
     (seconds to minutes).
5. The fit runs in the background with live step and χ²ᵣ readouts.
   **Cancel** keeps the best point found so far.

When it finishes:

* the sliders move to the fitted values;
* each fitted parameter shows its ± 1σ uncertainty (flagged ⚑ if it ended on
  a bound: widen that range and refit);
* the starting curve is pinned as "before fit";
* **Undo fit** restores the previous values.

The fit uses the same model, resolution (dQ or slit) and χ² window as the
overlay, through `sasmodels.bumps_model`.

Two χ² values are shown side by side. **Fit χ²ᵣ (BUMPS)** is
Σ((I − model)/dI)² / (N − p), the usual reduced χ². **χ²/N** is the
fit-agnostic number used everywhere else. Change any value by hand and the
± values disappear and the fit χ²ᵣ is struck through, because they no longer
describe the parameters on screen.

**Copy** and **Save parameters** include the method, χ²ᵣ, degrees of freedom,
uncertainties and any parameters at a bound while the fit is current.

## Mix (several form factors)

Tick **Mix** (Model panel) to sum up to four form factors, e.g. particles plus
a low-Q power law, or two populations. The form factor and S(Q) selected above
stay as form factor 1 (the only one that can carry a structure factor); **+
form factor** adds the next, × removes one, and unticking Mix goes back to a
single model, keeping form factor 1's values and polydispersity.

* This is sasmodels' mixture (`sphere@hardsphere+power_law+cylinder`): one
  global **scale** and **background**, and per-form parameters prefixed `A_`,
  `B_`, `C_`, `D_`. The panel shows form factor 1's parameters by their plain
  names (its own scale as **scale ₁**) and the others with their index
  (**radius ₂**, **power ₂**, …), listed after form factor 1; hover a label
  for the sasmodels name, which is also what fits, reports and JSON use.
* A newly added form factor's scale is set to about 10% of the rest of the
  model at every Q, so it doesn't swamp the curve; adjust from there.
* Fitting, Match, Multi (shared/per-dataset/expressions on any of these
  parameters) and the IQuana notebook all work with mixtures. The notebook
  uses IQuana's raw `set_model('sphere@hardsphere+power_law')`, which keeps
  the same `A_`/`B_` names, and `MultiFitter.add(..., model=...)` with the same
  string; IQuana's Bayesian sampler doesn't take mixtures, so a DREAM setting
  exports as simplex.
* **Guess** suggests single models: applying a suggestion turns Mix off.

## Multi (several datasets at once)

Tick **Multi** (next to **Fit** in the Parameters panel) to work on every
ticked dataset together — a contrast series, a concentration series, SANS +
SAXS — on the same page:

* **Members** are the ticked (drawn) datasets. The active (radio) one is the
  one the parameter panel edits; click another radio to edit that dataset.
  Every member's model curve is drawn in its dataset colour, the residual plot
  shows them all, and the χ²/N readout is the combined value (hover it for
  each dataset's).
* **all / each** next to each parameter: *all* means one shared value, slider
  range and polydispersity for every member; *each* means every dataset has its
  own (scale, background and solvent SLDs start as *each*, the rest as *all*).
* **ƒ** (on an *each* parameter) sets this dataset's value by an expression of
  other parameters, e.g. `0.8 * h2o.scale` or
  `(h2o.background + d2o.background) / 2`. Datasets are referred to by the
  short names shown in the Multi line (from the file names); a bare name such
  as `scale` means the same dataset. Numbers, `+ - * /`, parentheses and
  integer powers are allowed, as in IQuana; expressions are parsed, never
  executed, and cycles are refused.
* **Fit** (with the Fit toggle on) runs one joint BUMPS fit: shared parameters
  are one fitted quantity, expression parameters are derived, and the fit
  ticks apply to every dataset (bounds: the slider range — per dataset for
  *each* parameters). **Undo fit** restores every dataset.
* **IQuana notebook ↗** writes a `MultiFitter` notebook: one `fit.add(...)`
  per dataset with its own values, bounds, PD, Q range and resolution, then
  `fit.share(...)` and `fit.constrain(...)`, a check that prints each
  dataset's χ²/N next to sassafras's, the joint fit, `fit.get_fit_report()`
  and `fit.save_results(...)` (parameters, datasets, covariance and curves as
  CSV). IQuana has no joint DREAM yet, so a DREAM setting exports as simplex.
* **Copy to clipboard → Params**, **Save** and **Load** cover all members (a table with one
  column per dataset; the JSON restores the datasets it can find by path).
* All members share the model and the χ² window. **Guess** still looks at the
  active dataset only and says so when Multi is on.

## Guess (ML model suggestion)

**Guess** (Model panel) passes the active dataset, inside the χ² window, to the
trained network from the `ml-sans` project. It shows the ranked sasmodels
suggestions with their probabilities, and loads the top one sassafras can
handle. Click any other row to load that model instead.

The network ranks models only; it doesn't predict parameters. Starting values
come from a quick prior search:

1. Up to 300 parameter sets for the suggested model (about 4 s) are drawn from
   the same physical priors the network was trained on (`sans_ml.priors`,
   including PD and the S(Q) effective-radius handling).
2. Each set is scored against the data with sassafras's resolution-smeared
   evaluation and the closed-form scale/background solve.
3. If `bumps` is installed, the best three draws get a short, time-limited
   Levenberg–Marquardt polish of the parameters the prior varies.

The result is a starting point, not a final fit; refine it with the sliders or
the Fit toggle.

* Two-component suggestions (`A+B`) are listed but can't be loaded yet.
* As the ml-sans README notes, the network does well on simulated-like curves
  and less well on measured data, so treat the top three as the answer.
* Setup: the trained model is bundled, so only the Python packages are
  needed: `pip install "sassafras[guess]"` (`torch`, `scipy`, `iquana`) in the same
  environment as sassafras. Without them the Guess button is dimmed; clicking it shows what is missing,
  and the terminal prints a `Guess: ready` or `Guess: unavailable` line at
  start-up.
* The network runs in a short-lived child process, so PyTorch is never loaded
  into the sassafras server. (On macOS, PyTorch and numpy/sasmodels can bring
  conflicting OpenMP runtimes, which aborts the process that loads both.)
* `sassafras/ml` holds `model.pt` and the inference subset of ml-sans's
  `sans_ml` package (`__init__`, `catalogue`, `encode`, `net`, `predict`,
  `priors`, `simulate`); training and data-generation code stays in ml-sans.
  After retraining, refresh the copy from the ml-sans folder:

  ```bash
  cp ../ml-sans/model.pt sassafras/ml/
  for f in __init__ catalogue encode net predict priors simulate; do
    cp ../ml-sans/sans_ml/$f.py sassafras/ml/sans_ml/; done
  ```

  To try a new model without copying, start with `--ml-sans /path/to/ml-sans`
  (or set `SASSAFRAS_ML_SANS`).
* S(Q) can now be added to any form factor except those that already include
  inter-particle structure or describe no finite particle. This is the same
  rule ml-sans uses, so its P@S suggestions load.

### Curve library (SAS-55M-20k)

When a curve-library index is installed, **Guess** uses it instead of the
network. SAS-55M-20k (the Sasformer training set) holds 1.1 million noise-free
sasmodels curves: 55 form factors × 20,000 random parameter sets on 512 Q
points from 0.001 to 1 Å⁻¹, each stored with its structural parameters.

* **Ranking:** every library curve is interpolated onto the active dataset's Q
  points inside the χ² window and given its own weighted least-squares scale
  and background, so only the shape counts. The list shows each model's best
  χ²/N (unsmeared, so it is a ranking rather than a fit quality). The match
  takes 1–3 s.
* **Starting values:** the three closest curves of the chosen model supply
  their stored parameters. SLDs and polydispersity are not stored, so
  sassafras keeps the default SLDs (scale absorbs the contrast), scans Schulz
  polydispersity widths 0–0.3 on the parameters the generator made
  polydisperse (fewer of them for slow kernels), then runs the short LM
  polish. For models whose shape depends on contrast ratios (two or more
  particle SLDs, e.g. core–shell), the polish also moves those SLDs.
* **S(Q) sub-library:** SAS-55M-20k itself has no structure factors (apart
  from `binary_hard_sphere`), so a second index adds P×S curves. It takes 3,000
  random curves of each of the 20 particle form factors that have a defined
  effective radius (spheres, core–shells, vesicles, cylinders, ellipsoids,
  prisms, …; not the shape-independent, lamellar or flexible-chain models),
  and multiplies each by four structure factors:

  | S(Q) | weak | strong |
  |---|---|---|
  | `hardsphere` | φ 0.02–0.10 | φ 0.20–0.40 |
  | `hayter_msa` (water, 298 K) | φ 0.005–0.05, charge 5–20 e, salt 0.01–0.1 M | φ 0.02–0.15, charge 20–60 e, salt 0–0.005 M |

  S(Q) is evaluated at the form's effective radius (equivalent-volume sphere
  where defined) × 1.0–1.1, on the same Q points, as
  I = (I_stored − 0.001)·S(Q) + 0.001, so each curve keeps the generator's
  polydispersity and SLDs. Settings where Hayter–MSA has no physical solution
  (S ≤ 0, mostly large particles at weak charge) are dropped, leaving about
  205,000 curves (~90 MB). The lookup searches both indexes at once, and a
  `form@sf` match loads with `radius_effective_mode` "unconstrained" at the
  stored radius_effective; temperature and dielectric constant stay fixed in
  the polish.
* **Limits:** P×S is only covered for the S(Q) settings above, and
  two-population mixtures aren't covered at all.
  Where the generator coupled parameters or depended on the unknown SLDs, the
  stored values are only approximate starting points.
* **Setup (quick):** download the prebuilt indexes (~290 MB, ~480 MB
  unpacked), checksum-verified, into your per-user data folder:

  ```bash
  python -m sassafras.library fetch
  ```

  The folder is `~/Library/Application Support/sassafras` on macOS,
  `~/.local/share/sassafras` on Linux and `%LOCALAPPDATA%\sassafras` on
  Windows; set `SASSAFRAS_DATA` to use another. It survives upgrades and is
  shared by every Python environment. Indexes built or fetched into
  `sassafras/ml/` by versions before 0.5 are still found.

* **Setup (from the dataset):** or download SAS-55M-20k
  ([Figshare](https://doi.org/10.6084/m9.figshare.21716618), ~4.5 GB) and build
  the index once (about 20 s; it needs `pyarrow`, `pip install -e ".[library]"`):

  ```bash
  python -m sassafras.library build ~/Downloads/sas-55m-20k
  ```

  This writes about 400 MB to `library` in the data folder. Then build the
  S(Q) sub-library (about 30 s, ~90 MB in `library_sq`; `--per-model` sets
  the size):

  ```bash
  python -m sassafras.library build-sq ~/Downloads/sas-55m-20k
  ```

  Use `--out` to put either elsewhere and start sassafras with `--library DIR`
  / `--library-sq DIR` (or set `SASSAFRAS_LIBRARY` / `SASSAFRAS_LIBRARY_SQ`). Without an index, Guess uses the ml-sans network as
  before.

* **Data credit and licence:** SAS-55M-20k is by B. Yildirim, J. Doutch and
  J. M. Cole (*Digital Discovery* **3**, 694–704, 2024) and is MIT-licensed;
  the licence is in `sassafras/ml/SAS-55M-20k_LICENSE.txt` and is shipped as
  `DATA_LICENSE.txt` inside the release archive. To publish a rebuilt index:
  `python -m sassafras.library pack sassafras-library-vN.tar.gz`, upload it as
  a new version of the library's Zenodo record (and attach it to a
  `library-vN` GitHub release as a mirror), then update `RELEASE_TAG`,
  `RELEASE_FILE`, `RELEASE_SHA256` and `ZENODO_URL` in `sassafras/library.py`.

## IQuana notebook

**IQuana notebook ↗** (top right of the plot panel) writes a Jupyter notebook with
[IQuana](https://sasview.github.io/IQuana/) code that reproduces what is on
screen, then opens it in JupyterLab. It covers the data, model and structure
factor, every parameter value, polydispersity, the Q window and the
resolution. When the Fit toggle is on it also sets up the same fit: the ticked
parameters with the slider ranges as bounds, and the same method (LM, simplex
and DE → `fit(engine='bumps', method=…)`, DREAM → `fit_bayesian()`).

* Notebooks are saved in `~/sassafras_notebooks` (change it with
  `--notebook-dir`). One JupyterLab server rooted there is started on first
  use and reused afterwards; it keeps running after sassafras stops, so open
  notebooks aren't lost.
* A check cell prints IQuana's χ²/N next to the sassafras value. IQuana builds
  kernels in single precision, so they agree to about 1 part in 10⁴.
* Path-loaded files are read from their original location. Uploaded files,
  and files that sassafras rescaled or trimmed on load, are saved next to the
  notebook as a CSV snapshot (Q, I, dI, dQ).
* Effective radius: sasmodels' `radius_effective_mode` "unconstrained" maps
  to IQuana's `'unconstrained'`, and "radius" to `'link_radius'`. Other
  computed modes (equivalent volume sphere, excluded volume, …) have no IQuana
  equivalent, so `radius_effective` is fixed at the value sasmodels computes
  for the current geometry, and the notebook says so.
* Models with per-shell vector parameters (e.g. `core_multi_shell`) can't be
  addressed shell by shell in IQuana 0.5, so they are refused with a message.
* Needs `iquana` and `jupyterlab` in the same Python environment:
  `pip install "sassafras[notebook]"`.

## Copy to clipboard

Buttons above the plot put content on the clipboard, ready to paste into
OneNote, an ELN, Word, Excel or a plain-text cell:

* **All**: title line, figure and parameter table as one rich paste. Plain-text
  editors get the text summary instead.
* **Figure**: PNG of the data, model, pinned curve, residuals and legend, with
  the dataset, model and χ²/N as a title. It is always drawn on a white
  background, whatever the screen theme.
* **Parameters**: model, χ²/N with N and the Q window, and every parameter with
  units and polydispersity. It pastes as a table, or as aligned text where
  tables aren't supported.
* **Data**: Q, I, dI, model, residual and in-window flag at full precision. It
  pastes as columns into Excel and as a table into notebooks.
* **PNG ↓**: downloads the figure instead.

## Plot

**Plot type** (next to log Q / log I): **I(Q)**; **Kratky**, Q²·I(Q) vs Q
(linear axes by default), a quick check of folding, compactness or chain-like
scattering; or **Guinier**, ln I vs Q² (log axes off), where a straight
low-Q line gives Rg from its slope (−Rg²/3). Guinier opens on the low-Q region
where ln I has fallen by about 0.8, a little past the usual Q·Rg ≈ 1.3 limit;
drag to zoom as usual. Data, error bars, the model and any pinned curve are
all transformed; the residual panel keeps (I − model)/dI on the same Q axis.

Light and dark themes follow the system setting. The **Theme** button in the header switches between auto, light and dark, and the browser remembers your choice. The **Colour** button next to it cycles the colour scheme (accents, first data series and logo): chopped (green, default), cooked (orange), washed (blue), chalked (grey) and girlypop (pink); it is remembered too. Log/linear axes, drag a box to zoom (a horizontal drag zooms Q only),
double-click or **Reset zoom** to undo, and hover for point values. The χ² window
greys out points outside it and restricts both χ² and Match.

χ²/N = Σ((I − model)/dI)² ÷ (points in window). No parameters are fitted, so
this is a number for comparing settings, not a goodness-of-fit statistic. To
compare with a BUMPS/SasView fit's reduced χ², multiply by N/(N − p).

## Checks

`python -m pytest tests` covers loading every example format, nm⁻¹
conversion, product and polydisperse models, vector parameters, the
scale/background solve, error reporting and every HTTP endpoint. One
regression test reproduces the `elliptical_cylinder_gui` starting curve to
within 2 × 10⁻⁶ relative, and its BUMPS χ² (561.675) after the N/(N − p)
rescale. With bumps installed, `tests/test_fit.py` checks that LM and
simplex fits recover known parameters within their uncertainties, that the Q
window and PD widths are respected, that cancellation works and that bad
requests are rejected. Those tests are skipped when bumps is absent.

`examples/` contains measured flexible elliptical cylinder SANS data
(`flexible_cylinder_elliptical.dat`; four columns Q (Å⁻¹), I, dI (cm⁻¹), dQ (Å⁻¹), with
metadata removed), synthetic sphere × hard-sphere data (canSAS XML and
NXcanSAS), and a synthetic SAXS core–shell dataset written in nm⁻¹.

## Layout

```
sassafras/backend.py   data loading (sasdata), model catalogue, evaluation (sasmodels)
sassafras/server.py    stdlib HTTP server + CLI
sassafras/fit.py       optional BUMPS fitting (background jobs, progress, cancel; joint multi-dataset fits)
sassafras/multi.py     Multi: request checks and safe expressions shared by the joint fit and the export
sassafras/notebook.py  IQuana notebook export and JupyterLab launcher
sassafras/guess.py     Guess: ml-sans model suggestion + prior-search starting values
sassafras/_guess_worker.py  child process that runs the ml-sans network (torch)
sassafras/library.py   curve-library index (build) and shape matching for Guess
sassafras/ml/          bundled model.pt + sans_ml inference code (copied from ml-sans)
sassafras/static/      index.html, app.js (SVG plotting, controls), copy.js (clipboard),
                       fitting.js (Fit toggle and controls), multi.js (Multi), notebook.js, guess.js, app.css, logos + favicon
```

The backend can be used without the GUI:

```python
from sassafras.backend import Session
s = Session()
ds, _ = s.load_path("run.xml")
out = s.evaluate({"model": "sphere", "structure_factor": "hardsphere",
                  "params": {"radius": 60, "volfraction": 0.2}, "dataset": ds[0].id})
out["chisq"]
```

Tested with Python 3.13, sasmodels 1.1.0, sasdata 0.12.2, bumps 1.0.3 and numpy 2.5.3, in
headless Chromium (light and dark themes, desktop and phone widths).

## Contributing

Issues and pull requests are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).

## Citing

If sassafras helps your work, please cite it:
[doi:10.5281/zenodo.24041504](https://doi.org/10.5281/zenodo.24041504). That DOI
always resolves to the latest version; each release also has its own DOI on
that page. GitHub's "Cite this repository" button gives the same reference
from `CITATION.cff`.

## Licence

BSD 3-Clause; see [LICENSE](LICENSE). Sassafras uses
[sasmodels](https://github.com/SasView/sasmodels) and
[sasdata](https://github.com/SasView/sasdata), which are also BSD-licensed.
