"""Label space for the model-suggestion problem.

Everything is derived from the installed sasmodels so the vocabulary tracks the
library; the only hand-written lists are the two physics decisions below.
"""
from __future__ import annotations

import warnings
from functools import lru_cache

from sasmodels.core import list_models, load_model_info

# Forms that do not receive an S(Q): they already contain inter-particle
# interference (paracrystals, stacks, binary_hard_sphere, RPA, peak/correlation
# functions) or describe no finite particle (power laws, line). A hard-sphere
# S(Q) on these is computable but is not physically meaningful, so it would only
# add noise to the labels. Empty this set to reproduce "literally every P@S".
NO_STRUCTURE_FACTOR = {
    'bcc_paracrystal', 'fcc_paracrystal', 'sc_paracrystal',
    'lamellar_stack_caille', 'lamellar_hg_stack_caille', 'lamellar_stack_paracrystal',
    'binary_hard_sphere', 'rpa', 'be_polyelectrolyte', 'teubner_strey', 'spinodal',
    'gaussian_peak', 'peak_lorentz', 'broad_peak', 'porod', 'power_law', 'line',
}

# Components used for double-form-factor data: the things that plausibly coexist
# (micelles + worms, vesicles + micelles, particles + free polymer, particles +
# aggregates or a low-Q upturn).
MIXTURE_POOL = [
    'sphere', 'core_shell_sphere', 'fuzzy_sphere', 'vesicle', 'ellipsoid',
    'core_shell_ellipsoid', 'cylinder', 'core_shell_cylinder', 'hollow_cylinder',
    'flexible_cylinder', 'lamellar', 'lamellar_hg', 'core_shell_bicelle',
    'parallelepiped', 'polymer_micelle', 'mono_gauss_coil', 'poly_gauss_coil',
    'polymer_excl_volume', 'power_law', 'lorentz', 'dab', 'mass_fractal',
    'surface_fractal', 'fractal', 'guinier_porod',
]


# Kernels whose nested orientation integrals cost >0.1 s per mono curve. They are
# computed on a coarser Q grid with lighter polydispersity integration.
SLOW = {
    'barbell', 'capped_cylinder', 'core_shell_bicelle_elliptical',
    'core_shell_bicelle_elliptical_belt_rough', 'core_shell_parallelepiped',
    'elliptical_cylinder', 'hollow_rectangular_prism',
    'hollow_rectangular_prism_thin_walls', 'micromagnetic_FF_3D', 'parallelepiped',
    'pringle', 'prism', 'rectangular_prism', 'superball', 'tetrahedron',
    'triaxial_ellipsoid', 'truncated_octahedron', 'truncated_tetrahedron',
    'bcc_paracrystal', 'fcc_paracrystal', 'sc_paracrystal',
}

# Lowest Q at which a kernel is numerically usable: the paracrystal lattice sums
# diverge to inf at very low Q in single precision.
Q_FLOOR = {'bcc_paracrystal': 2e-3, 'fcc_paracrystal': 2e-3, 'sc_paracrystal': 2e-3}


@lru_cache(maxsize=1)
def _infos():
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        return {n: load_model_info(n) for n in list_models('all')}


def draw_weight(form: str) -> float:
    """Relative draw rate. Slow kernels are drawn less and up-weighted in the loss;
    pass --uniform-forms to the generator on a cluster to remove this."""
    if form in ('superball', 'pringle'):
        return 0.1
    if form in Q_FLOOR:
        return 0.2
    return 0.4 if form in SLOW else 1.0


def structure_factors() -> list[str]:
    return sorted(n for n, i in _infos().items() if i.structure_factor)


def form_factors() -> list[str]:
    return sorted(n for n, i in _infos().items() if not i.structure_factor)


def category(name: str) -> str:
    """Coarse family used for 'near miss' scoring, e.g. shape:cylinder."""
    return _infos()[name].category or 'other'


def forms_with_sq() -> list[str]:
    return [f for f in form_factors() if f not in NO_STRUCTURE_FACTOR]


def info(name: str):
    return _infos()[name]


def effective_radius_mode(form: str) -> int:
    """Index of the 'equivalent volume sphere' mode (else mode 1, else 0=none)."""
    modes = info(form).radius_effective_modes
    if not modes:
        return 0
    return modes.index('equivalent volume sphere') + 1 if 'equivalent volume sphere' in modes else 1


def size_polydisperse_params(form: str) -> list[str]:
    """Length parameters a polydispersity width can be attached to."""
    out = []
    for p in info(form).parameters.kernel_parameters:
        if p.polydisperse and p.type == 'volume' and p.units == 'Ang' and p.length == 1:
            out.append(p.name)
    return out
