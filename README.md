# Sassafras

**Small Angle Scattering Structure- And Form-factor Rapid ASsessment**

A lightweight SANS/SAXS quick-look tool for the beamline. Load reduced 1D data
in any format sasdata reads, pick a sasmodels form factor (optionally × a
structure factor), and drag sliders to overlay the model on the data —
chi-by-eye, with live χ² and residuals. With the **Fit** toggle off there is
no optimiser; the only automatic step is a one-click linear solve for scale
and background. Switch **Fit** on to refine the chi-by-eye starting point
with BUMPS (needs the optional `bumps` package).

It is a generalised local browser GUI with no external JavaScript, working with 
any model and any (including no) data file.

## Run

```bash
pip install -r requirements.txt          # sasmodels, sasdata, numpy (+ bumps for fitting)
python -m sassafras                       # from this folder, no install needed
python -m sassafras run_1234.xml run_1235.h5 -m core_shell_sphere -s hardsphere
python -m sassafras /data/reduced/        # load every readable file in a folder (≤ 50)
```

Or install it so `sassafras` works from any folder: `pip install -e .`

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
  **Bkg from high Q** re-applies it.
* **Match scale / Match scale + bkg**: a weighted linear least-squares solve
  over the χ² window, with the model shape fixed. It's for getting the curve
  onto the data before you start adjusting by eye.
* **Pin curve** keeps the current curve, residuals and χ² as a dashed
  reference to compare against.
* **Save / Load parameters** (JSON: model, values, PD, slider ranges, Q window)
  and **Export curve CSV** (Q, I, dI, model, residual, in-window flag).

## Fitting (optional)

Install `bumps` (`pip install bumps`, or `pip install -e ".[fit]"`) and restart
sassafras. A **Fit** toggle in the header then shows the fitting controls. With
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

## Copy to notebook

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

Light and dark themes follow the system setting. The **Theme** button in the header switches between auto, light and dark, and the browser remembers your choice. Log/linear axes, drag a box to zoom (a horizontal drag zooms Q only),
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
sassafras/fit.py       optional BUMPS fitting (background jobs, progress, cancel)
sassafras/static/      index.html, app.js (SVG plotting, controls), copy.js (clipboard),
                       fitting.js (Fit toggle and controls), app.css, logos + favicon
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

## Licence

BSD 3-Clause; see [LICENSE](LICENSE). Sassafras uses
[sasmodels](https://github.com/SasView/sasmodels) and
[sasdata](https://github.com/SasView/sasdata), which are also BSD-licensed.
