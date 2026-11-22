"""Parameter distributions.

A parameter spec is either a plain number (known value) or a dict with a
`dist` key. Every distribution supports sampling and quantiles so the engine
can run full Monte Carlo (sample) and tornado analysis (quantiles).
"""
from __future__ import annotations

import numpy as np
from scipy import stats

Z90 = stats.norm.ppf(0.90)  # ~1.2816, standard deviations to the 90th percentile


def is_dist(spec) -> bool:
    return isinstance(spec, dict) and "dist" in spec


def _frozen(spec):
    d = spec["dist"]
    if d == "beta":
        return stats.beta(spec["alpha"], spec["beta"])
    if d == "pert":
        lo, mode, hi = spec["min"], spec["mode"], spec["max"]
        if not lo <= mode <= hi or lo == hi:
            raise ValueError(f"PERT needs min <= mode <= max and min < max: {spec}")
        a = 1 + 4 * (mode - lo) / (hi - lo)
        b = 1 + 4 * (hi - mode) / (hi - lo)
        return stats.beta(a, b, loc=lo, scale=hi - lo)
    if d == "lognormal":
        mu = np.log(spec["median"])
        sigma = (np.log(spec["p90"]) - mu) / Z90
        return stats.lognorm(s=sigma, scale=np.exp(mu))
    if d == "dirichlet_component":
        # Marginal of a Dirichlet component: Beta(α_k, α_0 − α_k). Used for medians
        # and tornado quantiles; joint draws are made in model.price().
        return stats.beta(spec["alpha"], spec["alpha0"] - spec["alpha"])
    if d == "uniform":
        return stats.uniform(loc=spec["min"], scale=spec["max"] - spec["min"])
    raise ValueError(f"Unknown distribution: {d}")


def sample(spec, rng: np.random.Generator, n: int) -> np.ndarray:
    """Draw n values. Plain numbers return a constant array.

    Uses inverse-transform sampling (uniforms through the quantile function) so
    every parameter consumes exactly n random numbers. Changing one parameter's
    distribution then leaves every other draw unchanged (common random numbers),
    and benchmark differences reflect the change, not simulation noise.
    """
    u = rng.random(n)
    if not is_dist(spec):
        return np.full(n, float(spec))
    return _frozen(spec).ppf(u)


def gamma_ppf(u: np.ndarray, a: float) -> np.ndarray:
    """Inverse-transform Gamma(a, 1) draws (for the Dirichlet gamma construction)."""
    if a <= 0:
        return np.zeros_like(u)
    return stats.gamma(a).ppf(np.clip(u, 1e-12, 1 - 1e-12))


def quantile(spec, q: float) -> float:
    if not is_dist(spec):
        return float(spec)
    return float(_frozen(spec).ppf(q))


def central(spec) -> float:
    """Median: the 'base case' value used for point pricing and tornado charts."""
    return quantile(spec, 0.5)


def lognormal_params(median, p90):
    """Convert median and 90th percentile into lognormal mu and sigma (arrays ok)."""
    mu = np.log(median)
    sigma = (np.log(p90) - mu) / Z90
    if np.any(sigma <= 0):
        raise ValueError("Severity p90 must exceed median")
    return mu, sigma
