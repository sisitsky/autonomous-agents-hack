"""What would lower this insured's price? Re-prices the insured under changes it
could make, using the same seed and simulation size so differences come from the
change, not from simulation noise (common random numbers).

Two kinds of option:
  - rating-factor controls: factors marked `controllable: true` in
    params/rating_factors.yaml, moved to each level with a lower relativity;
  - red-team remediation: the same suite re-run with every failure fixed.
"""
from __future__ import annotations

import copy

from .model import load_params, price


def _applies(spec: dict, insured: dict) -> bool:
    return any(p in insured["perils"] for p in spec["applies_to"])


def control_options(insured: dict, params: dict | None = None, base: dict | None = None,
                    **price_kw) -> dict:
    params = params or load_params()
    base = base or price(insured, params, **price_kw)
    base_premium = base["gross_premium"]
    options = []

    for name, spec in params["rating_factors"]["factors"].items():
        if not spec.get("controllable") or not _applies(spec, insured):
            continue
        current = insured.get("factors", {}).get(name)
        current_rel = spec["levels"][current] if current else 1.0
        for level, rel in spec["levels"].items():
            if rel >= current_rel or level == current:
                continue
            alt = copy.deepcopy(insured)
            alt.setdefault("factors", {})[name] = level
            premium = price(alt, params, **price_kw)["gross_premium"]
            options.append({
                "kind": "rating_factor", "factor": name,
                "from_level": current or "not rated (neutral)", "to_level": level,
                "from_relativity": current_rel, "to_relativity": rel,
                "premium": premium, "saving": base_premium - premium,
                "saving_pct": 1 - premium / base_premium,
                "rationale": spec["source"],
            })

    rt = insured.get("red_team")
    fails = sum((rt or {}).get("failures_by_type", {}).values()) if rt else 0
    if rt and fails:
        alt = copy.deepcopy(insured)
        alt["red_team"]["failures_by_type"] = {k: 0 for k in rt["failures_by_type"]}
        premium = price(alt, params, **price_kw)["gross_premium"]
        options.append({
            "kind": "red_team_remediation", "factor": "red_team",
            "from_level": f"{fails} of {rt['tested']} cases failed",
            "to_level": "all failures fixed and re-tested",
            "premium": premium, "saving": base_premium - premium,
            "saving_pct": 1 - premium / base_premium,
            "rationale": "Fix the failed cases and re-run the same suite.",
        })

    options.sort(key=lambda o: o["saving"], reverse=True)
    return {"insured_id": insured.get("id"), "base_premium": base_premium, "options": options}
