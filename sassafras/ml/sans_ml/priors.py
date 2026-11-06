"""Physically reasonable parameter priors for every sasmodels form/structure factor.

Sizes are in Angstrom, SLDs in 1e-6/A^2. Each function returns one random draw
for a model. Constraints between parameters (core inside shell, dnn larger than
the particle, ...) are expressed directly in the draw. Absolute intensity scale
is irrelevant because the encoder normalises it away.
"""
from __future__ import annotations

import numpy as np


class R:
    def __init__(self, rng: np.random.Generator):
        self.g = rng

    def u(self, a, b):
        return float(self.g.uniform(a, b))

    def l(self, a, b):
        return float(np.exp(self.g.uniform(np.log(a), np.log(b))))

    def i(self, a, b):
        return int(self.g.integers(a, b + 1))

    def c(self, seq):
        return seq[int(self.g.integers(len(seq)))]

    def p(self, prob):
        return bool(self.g.random() < prob)

    # ---- contrast ----
    def solvent(self):
        return self.c([6.36, 6.36, 6.36, -0.56, -0.56, 5.66, 1.0, 2.0, 3.0, 4.5])

    def sld(self, sv):
        """An SLD with at least 0.3 contrast to the solvent."""
        for _ in range(20):
            v = sv + self.c([-1, 1]) * self.l(0.3, 7.0)
            if -0.6 <= v <= 8.5:
                return v
        return 1.0 if sv > 3 else 5.0


def _s(r, **names):  # helper: solvent + named SLDs
    sv = r.solvent()
    out = {k: r.sld(sv) for k in names}
    return sv, out


def _simple(r):
    sv, o = _s(r, sld=1)
    return {'sld_solvent': sv, **o}


def _box(r):
    a = r.l(10, 300)
    return a, a * r.l(1, 5), a * r.l(1, 30)


def _layered(r):
    sv = r.solvent()
    n = r.i(1, 4)
    return sv, n


def _cs(r, *n):
    sv = r.solvent()
    return {'sld_solvent': sv, **{k: r.sld(sv) for k in n}}


def adsorbed_layer(r):
    sv = r.solvent()
    return dict(second_moment=r.l(10, 60), adsorbed_amount=r.u(0.5, 5), density_shell=r.u(0.7, 1.2),
                radius=r.l(200, 3000), volfraction=r.u(0.01, 0.2), sld_shell=r.sld(sv), sld_solvent=sv)


def barbell(r):
    a = r.l(10, 150)
    return dict(radius=a, radius_bell=a * r.u(1.05, 2.5), length=r.l(30, 1500), **_simple(r))


def _para(r):
    a = r.l(20, 300)
    return dict(radius=a, dnn=2 * a * r.u(1.05, 1.8), d_factor=r.u(0.02, 0.15), **_simple(r))


bcc_paracrystal = fcc_paracrystal = sc_paracrystal = _para


def be_polyelectrolyte(r):
    return dict(contrast_factor=10.0, bjerrum_length=r.u(6, 8), virial_param=r.l(1, 30),
                monomer_length=r.u(3, 15), salt_concentration=r.l(1e-3, 0.3),
                ionization_degree=r.u(0.02, 0.6), polymer_concentration=r.l(0.01, 1))


def binary_hard_sphere(r):
    sv = r.solvent()
    a = r.l(50, 500)
    return dict(radius_lg=a, radius_sm=a * r.u(0.1, 0.6), volfraction_lg=r.u(0.02, 0.3),
                volfraction_sm=r.u(0.02, 0.2), sld_lg=r.sld(sv), sld_sm=r.sld(sv), sld_solvent=sv)


def broad_peak(r):
    return dict(porod_scale=r.l(1e-7, 1e-3), porod_exp=r.u(2, 4), peak_scale=r.l(1, 100),
                correlation_length=r.l(10, 200), peak_pos=r.l(0.01, 0.4), width_exp=r.u(1, 3),
                shape_exp=r.u(0.8, 3))


def capped_cylinder(r):
    a = r.l(10, 200)
    return dict(radius=a, radius_cap=a * r.u(1, 1.6), length=r.l(20, 2000), **_simple(r))


def core_multi_shell(r):
    sv, n = _layered(r)
    p = dict(radius=r.l(20, 400), n=n, sld_core=r.sld(sv), sld_solvent=sv)
    for i in range(1, n + 1):
        p[f'sld{i}'] = r.sld(sv)
        p[f'thickness{i}'] = r.l(5, 100)
    return p


def core_shell_bicelle(r):
    return dict(radius=r.l(15, 400), thick_rim=r.l(4, 40), thick_face=r.l(4, 40), length=r.l(10, 100),
                **_cs(r, 'sld_core', 'sld_face', 'sld_rim'))


def core_shell_bicelle_elliptical(r):
    return dict(core_shell_bicelle(r), radius=r.l(15, 300), x_core=r.u(1.1, 5))


def core_shell_bicelle_elliptical_belt_rough(r):
    return dict(core_shell_bicelle_elliptical(r), sigma=r.u(0, 10))


def core_shell_cylinder(r):
    return dict(radius=r.l(8, 300), thickness=r.l(3, 100), length=r.l(30, 5000),
                **_cs(r, 'sld_core', 'sld_shell'))


def core_shell_ellipsoid(r):
    return dict(radius_equat_core=r.l(10, 300), x_core=r.l(0.2, 6), thick_shell=r.l(3, 100),
                x_polar_shell=r.l(0.3, 3), **_cs(r, 'sld_core', 'sld_shell'))


def core_shell_parallelepiped(r):
    a, b, c = _box(r)
    return dict(length_a=a, length_b=b, length_c=c, thick_rim_a=r.l(3, 40), thick_rim_b=r.l(3, 40),
                thick_rim_c=r.l(3, 40), **_cs(r, 'sld_core', 'sld_a', 'sld_b', 'sld_c'))


def core_shell_sphere(r):
    return dict(radius=r.l(10, 800), thickness=r.l(3, 150), **_cs(r, 'sld_core', 'sld_shell'))


def correlation_length(r):
    return dict(lorentz_scale=r.l(1, 100), porod_scale=r.l(1e-7, 1e-3), cor_length=r.l(10, 500),
                porod_exp=r.u(1.5, 4), lorentz_exp=r.u(1.5, 3))


def cylinder(r):
    return dict(radius=r.l(5, 500), length=r.l(20, 8000), **_simple(r))


def dab(r):
    return dict(cor_length=r.l(10, 2000))


def ellipsoid(r):
    a = r.l(8, 500)
    return dict(radius_polar=a, radius_equatorial=max(5.0, a * r.l(0.1, 10)), **_simple(r))


def elliptical_cylinder(r):
    return dict(radius_minor=r.l(8, 300), axis_ratio=r.u(1.1, 6), length=r.l(30, 5000), **_simple(r))


def flexible_cylinder(r):
    k = r.l(40, 600)
    return dict(kuhn_length=k, length=k * r.l(5, 50), radius=r.l(5, 80), **_simple(r))


def flexible_cylinder_elliptical(r):
    return dict(flexible_cylinder(r), axis_ratio=r.u(1.1, 4))


def fractal(r):
    a = r.l(5, 100)
    sv = r.solvent()
    return dict(volfraction=r.u(0.01, 0.2), radius=a, fractal_dim=r.u(1.2, 2.9), cor_length=a * r.l(5, 100),
                sld_block=r.sld(sv), sld_solvent=sv)


def fractal_core_shell(r):
    a = r.l(10, 100)
    return dict(radius=a, thickness=r.l(3, 40), volfraction=r.u(0.01, 0.2), fractal_dim=r.u(1.2, 2.9),
                cor_length=a * r.l(5, 100), **_cs(r, 'sld_core', 'sld_shell'))


def fuzzy_sphere(r):
    a = r.l(20, 800)
    return dict(radius=a, fuzziness=a * r.u(0.05, 0.4), **_simple(r))


def gauss_lorentz_gel(r):
    return dict(gauss_scale=r.l(1, 1000), cor_length_static=r.l(30, 500), lorentz_scale=r.l(1, 1000),
                cor_length_dynamic=r.l(5, 50))


def gaussian_peak(r):
    q = r.l(0.005, 0.5)
    return dict(peak_pos=q, sigma=q * r.u(0.03, 0.4))


def gel_fit(r):
    return dict(guinier_scale=r.l(0.1, 100), lorentz_scale=r.l(0.1, 100), rg=r.l(20, 500),
                fractal_dim=r.u(1.5, 3), cor_length=r.l(5, 60))


def guinier(r):
    return dict(rg=r.l(10, 1000))


def guinier_porod(r):
    s = r.u(0, 2.5)
    return dict(rg=r.l(10, 800), s=s, porod_exp=r.u(s + 1, 4.5))


def _hollow_cyl(r):
    return dict(radius=r.l(15, 400), thickness=r.l(3, 60), length=r.l(30, 5000), **_simple(r))


hollow_cylinder = _hollow_cyl


def rectangular_prism(r):
    return dict(length_a=r.l(10, 300), b2a_ratio=r.l(1, 5), c2a_ratio=r.l(1, 30), **_simple(r))


def hollow_rectangular_prism(r):
    p = rectangular_prism(r)
    return dict(p, thickness=r.l(2, 0.4 * p['length_a']))


hollow_rectangular_prism_thin_walls = rectangular_prism


def lamellar(r):
    return dict(thickness=r.l(10, 200), **_simple(r))


def _hg(r):
    t, h = r.u(8, 30), r.u(4, 15)
    return t, h, _cs(r, 'sld', 'sld_head')


def lamellar_hg(r):
    t, h, c = _hg(r)
    return dict(length_tail=t, length_head=h, **c)


def lamellar_hg_stack_caille(r):
    t, h, c = _hg(r)
    return dict(length_tail=t, length_head=h, Nlayers=r.i(5, 100), d_spacing=2 * (t + h) * r.u(1.1, 4),
                Caille_parameter=r.u(0.02, 0.5), **c)


def lamellar_stack_caille(r):
    t = r.l(15, 100)
    return dict(thickness=t, Nlayers=r.i(5, 100), d_spacing=t * r.u(1.3, 8), Caille_parameter=r.u(0.02, 0.5),
                **_simple(r))


def lamellar_stack_paracrystal(r):
    t = r.l(15, 100)
    d = t * r.u(1.3, 8)
    return dict(thickness=t, Nlayers=r.i(5, 100), d_spacing=d, sigma_d=d * r.u(0.02, 0.2), **_simple(r))


def line(r):
    a = r.l(0.1, 10)
    return dict(intercept=a, slope=a * r.u(1, 20))


def linear_pearls(r):
    return dict(radius=r.l(20, 200), edge_sep=r.l(20, 500), num_pearls=r.i(2, 8), **_simple(r))


def lorentz(r):
    return dict(cor_length=r.l(5, 1000))


def mass_fractal(r):
    a = r.l(5, 100)
    return dict(radius=a, fractal_dim_mass=r.u(1.2, 2.9), cutoff_length=a * r.l(10, 100))


def mass_surface_fractal(r):
    return dict(fractal_dim_mass=r.u(1.5, 2.9), fractal_dim_surf=r.u(2, 2.9), rg_cluster=r.l(500, 8000),
                rg_primary=r.l(10, 200))


def micromagnetic_FF_3D(r):
    sv = r.solvent()
    return dict(radius=r.l(30, 300), thickness=r.l(5, 80), nuc_sld_core=r.sld(sv), nuc_sld_shell=r.sld(sv),
                nuc_sld_solvent=sv, mag_sld_core=r.u(0.5, 6), mag_sld_shell=r.u(0.5, 6), mag_sld_solvent=0.0,
                hk_sld_core=r.u(0.1, 2), Hi=r.l(0.01, 5), Ms=r.u(0.2, 2), A=r.u(1, 30), D=r.u(0, 2))


def mono_gauss_coil(r):
    return dict(i_zero=r.l(1, 100), rg=r.l(15, 800))


def multilayer_vesicle(r):
    sv, o = _s(r, sld=1)
    return dict(volfraction=r.u(0.01, 0.2), radius=r.l(50, 800), thick_shell=r.l(15, 60),
                thick_solvent=r.l(10, 120), n_shells=r.i(2, 10), sld_solvent=sv, **o)


def onion(r):
    sv, n = _layered(r)
    p = dict(radius_core=r.l(20, 400), n_shells=n, sld_core=r.sld(sv), sld_solvent=sv)
    for i in range(1, n + 1):
        p[f'sld_in{i}'] = r.sld(sv)
        p[f'sld_out{i}'] = r.sld(sv)
        p[f'thickness{i}'] = r.l(5, 100)
        p[f'A{i}'] = r.u(-3, 3)
    return p


def parallelepiped(r):
    a, b, c = _box(r)
    return dict(length_a=a, length_b=b, length_c=c, **_simple(r))


def peak_lorentz(r):
    q = r.l(0.005, 0.5)
    return dict(peak_pos=q, peak_hwhm=q * r.u(0.03, 0.4))


def pearl_necklace(r):
    sv = r.solvent()
    return dict(radius=r.l(20, 150), edge_sep=r.l(30, 500), thick_string=r.l(1, 10), num_pearls=r.i(2, 10),
                sld=r.sld(sv), sld_string=r.sld(sv), sld_solvent=sv)


def poly_gauss_coil(r):
    return dict(i_zero=r.l(1, 100), rg=r.l(15, 800), polydispersity=r.u(1.05, 4))


def polymer_excl_volume(r):
    return dict(rg=r.l(15, 800), porod_exp=r.u(1.6, 3))


def polymer_micelle(r):
    sv = max(r.solvent(), 0.1)
    return dict(ndensity=r.l(1, 50), v_core=r.l(1e3, 1e5), v_corona=r.l(1e3, 1e5), sld_solvent=sv,
                sld_core=abs(r.sld(sv)), sld_corona=abs(r.sld(sv)), radius_core=r.l(20, 150), rg=r.l(8, 60),
                d_penetration=r.u(0.8, 1.5), n_aggreg=r.l(5, 200))


def porod(r):
    return {}


def power_law(r):
    return dict(power=r.u(1, 4.5))


def pringle(r):
    return dict(radius=r.l(30, 500), thickness=r.l(5, 60), alpha=r.u(0, 0.01), beta=r.u(0, 0.03), **_simple(r))


def prism(r):
    return dict(n_sides=r.i(3, 12), radius_average=r.l(30, 800), length=r.l(20, 3000), **_simple(r))


def raspberry(r):
    sv = r.solvent()
    return dict(sld_lg=r.sld(sv), sld_sm=r.sld(sv), sld_solvent=sv, volfraction_lg=r.u(0.01, 0.1),
                volfraction_sm=r.u(0.002, 0.02), surface_fraction=r.u(0.1, 0.7), radius_lg=r.l(500, 5000),
                radius_sm=r.l(30, 200), penetration=r.u(-0.5, 0.5))


def rpa(r):
    # multiblock cases 1,4-9 need correlated K_ij to be stable; keep binary-blend style
    p = dict(case_num=r.c([0, 2, 3]))
    phi = r.g.dirichlet([2, 2, 2, 2])
    for i in range(4):
        p[f'N{i + 1}'] = r.l(100, 5000)
        p[f'Phi{i + 1}'] = float(phi[i])
        p[f'v{i + 1}'] = r.u(50, 150)
        p[f'L{i + 1}'] = r.u(-5, 70)
        p[f'b{i + 1}'] = r.u(3, 10)
    for k in ('K12', 'K13', 'K14', 'K23', 'K24', 'K34'):
        p[k] = -r.l(1e-5, 1e-3)
    return p


def sphere(r):
    return dict(radius=r.l(10, 1500), **_simple(r))


def spherical_sld(r):
    sv, n = _layered(r)
    p = dict(n_shells=n, sld_solvent=sv)
    for i in range(1, n + 1):
        p[f'sld{i}'] = r.sld(sv)
        p[f'thickness{i}'] = r.l(10, 150)
        p[f'interface{i}'] = r.l(3, 60)
        p[f'shape{i}'] = r.i(0, 3)
        p[f'nu{i}'] = r.u(1.5, 4)
    return p


def spinodal(r):
    return dict(gamma=r.u(2, 6), q_0=r.l(0.005, 0.3))


def stacked_disks(r):
    return dict(thick_core=r.l(8, 60), thick_layer=r.l(5, 40), radius=r.l(20, 400), n_stacking=r.i(2, 20),
                sigma_d=r.u(0, 10), **_cs(r, 'sld_core', 'sld_layer'))


def star_polymer(r):
    return dict(rg_squared=r.l(100, 2.5e5), arms=r.u(2, 6))


def superball(r):
    return dict(length_a=r.l(20, 500), exponent_p=r.l(0.6, 8), **_simple(r))


def surface_fractal(r):
    return dict(radius=r.l(10, 200), fractal_dim_surf=r.u(2.05, 2.95), cutoff_length=r.l(300, 5000))


def tetrahedron(r):
    return dict(radius=r.l(30, 500), **_simple(r))


def teubner_strey(r):
    d = r.l(30, 1000)
    return dict(volfraction_a=r.u(0.2, 0.8), sld_a=r.u(-0.5, 2), sld_b=r.u(3, 6.4), d=d, xi=d * r.l(0.2, 2))


def triaxial_ellipsoid(r):
    a = r.l(10, 200)
    return dict(radius_equat_minor=a, radius_equat_major=a * r.l(1, 6), radius_polar=a * r.l(0.3, 10), **_simple(r))


def truncated_octahedron(r):
    return dict(radius_a=r.l(30, 500), b2a_ratio=r.l(0.5, 2), c2a_ratio=r.l(0.5, 2), truncation=r.u(0, 0.45),
                **_simple(r))


def truncated_tetrahedron(r):
    return dict(radius=r.l(30, 500), truncation=r.u(0.05, 0.45), **_simple(r))


def two_lorentzian(r):
    return dict(lorentz_scale_1=r.l(1, 100), lorentz_length_1=r.l(50, 1000), lorentz_exp_1=r.u(1.5, 4),
                lorentz_scale_2=r.l(0.1, 10), lorentz_length_2=r.l(3, 50), lorentz_exp_2=r.u(1.5, 4))


def two_power_law(r):
    a = r.u(0.5, 3)
    return dict(coefficent_1=1.0, crossover=r.l(0.005, 0.2), power_1=a, power_2=r.u(a + 0.7, 4.5))


def unified_power_Rg(r):
    n = r.i(1, 3)
    p = dict(level=n)
    rg = r.l(200, 2000)
    g = r.l(10, 1e4)
    for i in range(1, n + 1):
        d = r.u(2, 4.2)
        p[f'rg{i}'] = rg
        p[f'power{i}'] = d
        p[f'G{i}'] = g
        p[f'B{i}'] = g * d / rg**d * r.l(0.3, 3)
        rg /= r.l(4, 15)
        g /= r.l(10, 1000)
    return p


def vesicle(r):
    return dict(volfraction=r.u(0.01, 0.2), radius=r.l(30, 1500), thickness=r.l(10, 80), **_simple(r))


# ----- structure factors ---------------------------------------------------
def hardsphere(r):
    return dict(volfraction=r.u(0.05, 0.45))


def hayter_msa(r):
    return dict(volfraction=r.u(0.01, 0.3), charge=r.l(5, 100), temperature=r.u(280, 350),
                concentration_salt=r.c([0.0, r.l(1e-4, 0.3)]), dielectconst=r.u(70, 85))


def squarewell(r):
    return dict(volfraction=r.u(0.02, 0.08), welldepth=r.u(0.3, 1.5), wellwidth=r.u(1.05, 1.8))


def stickyhardsphere(r):
    return dict(volfraction=r.u(0.05, 0.35), perturb=r.u(0.02, 0.1), stickiness=r.l(0.1, 5))


def two_yukawa(r):
    return dict(volfraction=r.u(0.03, 0.3), k1=r.u(2, 10), k2=-r.u(0.5, 6), z1=r.u(5, 15), z2=r.u(1, 4))


def draw(name: str, rng: np.random.Generator) -> dict:
    return globals()[name](R(rng))
