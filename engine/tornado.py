"""Tornado chart data: which uncertain parameter moves the premium most?

Each parameter is set to its low and high percentile while all others stay at
their median. Runs use point pricing with common random numbers (same seed),
so differences come from the parameter, not from simulation noise.
"""
from __future__ import annotations

from . import distributions as D
from .model import get_path, insured_priors, load_params, price, uncertain_paths


def tornado(insured: dict, params: dict | None = None, low_q: float = 0.10,
            high_q: float = 0.90, n_years: int = 200_000) -> dict:
    params = params or load_params()
    priors = insured_priors(params, insured)
    kw = dict(mode="point", n_outer=1, n_inner=n_years)
    base = price(insured, params, **kw)["gross_premium"]

    bars = []
    for path in uncertain_paths(priors, list(insured["perils"])):
        spec = get_path(priors, path)
        lo_v, hi_v = D.quantile(spec, low_q), D.quantile(spec, high_q)
        lo = price(insured, params, overrides={path: lo_v}, **kw)["gross_premium"]
        hi = price(insured, params, overrides={path: hi_v}, **kw)["gross_premium"]
        bars.append({
            "parameter": path,
            "low_value": lo_v, "high_value": hi_v,
            "premium_at_low": lo, "premium_at_high": hi,
            "swing": abs(hi - lo),
            "source": spec.get("source", ""),
        })
    bars.sort(key=lambda b: b["swing"], reverse=True)
    return {"insured_id": insured.get("id"), "base_premium": base,
            "percentiles": [low_q, high_q], "bars": bars}
