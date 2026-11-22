"""Bayesian update of an expert (PERT) parameter from new evidence.

Each prior counts as w0 pseudo-observations at its mode (expert judgment is
worth `PRIOR_WEIGHT` observations). A piece of evidence is a pseudo-observation
of weight w_e at an implied value (current mode × (1 + implied change)), with
w_e set by strength: weak 1, moderate 3, strong 6. The conjugate-style update is

    mode' = (w0 · mode + w_e · implied) / (w0 + w_e)

so one strong ruling moves a parameter by at most 6 / 26 ≈ 23% of the implied
change: evidence updates the prior, it doesn't replace it. min and max move by
the same ratio as the mode (the shape is kept), and every value is clipped to the
guardrail bounds. The prior weight then grows by w_e, so later evidence moves
the parameter less.
"""
from __future__ import annotations

PRIOR_WEIGHT = 20.0
STRENGTH = {"weak": 1.0, "moderate": 3.0, "strong": 6.0}
MAX_IMPLIED_CHANGE = 1.0     # evidence can imply at most a doubling or halving
MIN_IMPLIED_CHANGE = -0.5


def update_pert(spec: dict, implied_change: float, strength: str, bounds: tuple[float, float] | None = None) -> dict:
    """Return the updated PERT spec (min, mode, max, and the evidence weight used)."""
    if spec.get("dist") != "pert":
        raise ValueError("Only PERT parameters are calibrated from evidence")
    implied_change = max(MIN_IMPLIED_CHANGE, min(MAX_IMPLIED_CHANGE, float(implied_change)))
    w0 = float(spec.get("evidence_weight", PRIOR_WEIGHT))
    we = STRENGTH[strength]
    mode = float(spec["mode"])
    implied = mode * (1 + implied_change)
    new_mode = (w0 * mode + we * implied) / (w0 + we)
    ratio = new_mode / mode if mode else 1.0
    new = {"min": float(spec["min"]) * ratio, "mode": new_mode, "max": float(spec["max"]) * ratio}
    if bounds:
        lo, hi = bounds
        new = {k: min(max(v, lo), hi) for k, v in new.items()}
        new["mode"] = min(max(new["mode"], new["min"]), new["max"])
    return {**new, "evidence_weight": w0 + we, "ratio": ratio, "prior_weight": w0, "weight": we,
            "implied_value": implied}
