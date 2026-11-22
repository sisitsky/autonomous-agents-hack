"""Actual vs. expected (A/E) experience monitoring (Loop 2, deterministic).

Insurance's own learning loop: compare the claims the model expected with the
claims that actually happened, per peril, and recalibrate when the gap is
credible.

    expected claims E_k = Σ over policy-years of the model's claim frequency
    actual claims   A_k = observed claim count
    credibility-weighted frequency multiplier (Poisson–gamma / Bühlmann):
        m_k = (K + A_k) / (K + E_k),     Z_k = E_k / (E_k + K)

With K = 30 expected claims of prior credibility. When m_k moves outside ±5% and its 90% interval excludes 1,
the peril's claim-propensity parameter is proposed for a Bayesian update
(engine/bayes.py), with evidence strength set by how many claims were expected.

There is no real claims history yet, so `simulate()` writes a SIMULATED claims
book (clearly labeled) from a hidden "true world" that differs from the priors in
known ways. That lets the demo show the loop detecting a real drift. Replace
data/claims/claims.jsonl with actual claims and nothing else changes.

    python -m agents.experience --simulate      # write the simulated book
    python -m agents.experience                 # A/E report
"""
from __future__ import annotations

import argparse
import copy
import json
from datetime import date, timedelta

import numpy as np

from engine.model import load_params, price

from .config import ROOT
from .validator import portfolio_insureds

CLAIMS = ROOT / "data" / "claims" / "claims.jsonl"
EXPOSURE = ROOT / "data" / "claims" / "exposure.json"
K = 30.0
THRESHOLD = 0.05

# The hidden "true world" for the simulated book: frequency relative to the priors.
TRUE_WORLD = {
    "unauthorized_commitment": 1.6,   # courts and customers enforce bot promises more often than assumed
    "data_leakage": 0.8,              # fewer exposures turn into claims than assumed
    "prompt_injection": 1.25,         # agentic attacks are rising
    "wrong_information": 1.0,
}
COPIES = 60          # each portfolio insured stands for 60 similar policies
YEARS = 2


def _frequency_param(params: dict, peril: str) -> str:
    mode = params["priors"]["perils"][peril]["frequency"]["mode"]
    return f"perils.{peril}.frequency." + ("p_claim_given_harm" if mode == "red_team" else "rate")


def expected_by_peril(params: dict | None = None) -> dict:
    """Model-expected claims and losses per policy-year, by insured and peril."""
    params = params or load_params()
    out = {}
    for ins in portfolio_insureds():
        q = price({k: v for k, v in ins.items() if k != "_source"}, params, n_outer=400, n_inner=200)
        out[ins["id"]] = {p: {"claims": d["expected_claims"], "loss": d["expected_loss"]} for p, d in q["by_peril"].items()}
    return out


def simulate(seed: int = 7) -> dict:
    """Write a SIMULATED claims book: COPIES policies per portfolio insured over YEARS."""
    params = load_params()
    exp = expected_by_peril(params)
    rng = np.random.default_rng(seed)
    start = date(2024, 11, 22)
    claims = []
    for ins_id, perils in exp.items():
        for peril, e in perils.items():
            lam = e["claims"] * TRUE_WORLD.get(peril, 1.0) * COPIES * YEARS
            n = int(rng.poisson(lam))
            mean_sev = e["loss"] / e["claims"] if e["claims"] > 0 else 0
            for _ in range(n):
                claims.append({"insured_id": ins_id, "peril": peril,
                               "date": str(start + timedelta(days=int(rng.integers(0, 365 * YEARS)))),
                               "paid": float(rng.exponential(mean_sev)) if mean_sev else 0.0, "simulated": True})
    CLAIMS.parent.mkdir(parents=True, exist_ok=True)
    CLAIMS.write_text("".join(json.dumps(c) + "\n" for c in sorted(claims, key=lambda c: c["date"])))
    EXPOSURE.write_text(json.dumps({"policy_years": {i: COPIES * YEARS for i in exp}, "simulated": True,
                                    "true_world": TRUE_WORLD, "copies": COPIES, "years": YEARS}, indent=2))
    return {"claims": len(claims), "policy_years": COPIES * YEARS * len(exp)}


def report(params: dict | None = None) -> dict | None:
    """A/E by peril with credibility-weighted multipliers, or None without claims."""
    if not CLAIMS.exists() or not EXPOSURE.exists():
        return None
    params = params or load_params()
    claims = [json.loads(l) for l in CLAIMS.read_text().splitlines() if l.strip()]
    pys = json.loads(EXPOSURE.read_text())["policy_years"]
    exp = expected_by_peril(params)
    rows = {}
    for ins_id, perils in exp.items():
        years = pys.get(ins_id, 0)
        for peril, e in perils.items():
            r = rows.setdefault(peril, {"peril": peril, "expected_claims": 0.0, "actual_claims": 0,
                                        "expected_loss": 0.0, "actual_loss": 0.0})
            r["expected_claims"] += e["claims"] * years
            r["expected_loss"] += e["loss"] * years
    for c in claims:
        if c["peril"] in rows:
            rows[c["peril"]]["actual_claims"] += 1
            rows[c["peril"]]["actual_loss"] += c["paid"]
    for r in rows.values():
        E, A = r["expected_claims"], r["actual_claims"]
        r["ae_ratio"] = A / E if E else None
        r["credibility_z"] = E / (E + K)
        r["multiplier"] = (K + A) / (K + E)
        lo, hi = _gamma_interval(K + A, K + E)
        r["multiplier_90"] = [lo, hi]
        r["parameter"] = _frequency_param(params, r["peril"])
        # Propose only a credible, material drift: the 90% interval must exclude 1.
        r["propose"] = abs(r["multiplier"] - 1) > THRESHOLD and (lo > 1 or hi < 1)
        r["strength"] = "strong" if E >= 30 else "moderate" if E >= 10 else "weak"
    simulated = any(c.get("simulated") for c in claims)
    return {"perils": sorted(rows.values(), key=lambda r: -r["expected_claims"]), "claims": len(claims),
            "policy_years": sum(pys.values()), "K": K, "threshold": THRESHOLD, "simulated": simulated,
            "true_world": json.loads(EXPOSURE.read_text()).get("true_world") if simulated else None}


def _gamma_interval(shape: float, rate: float) -> tuple[float, float]:
    from scipy import stats
    return float(stats.gamma(shape, scale=1 / rate).ppf(0.05)), float(stats.gamma(shape, scale=1 / rate).ppf(0.95))


def evidence_items(rep: dict) -> tuple[list[dict], list[dict]]:
    """A/E rows that cross the threshold, as evidence items plus deterministic decisions
    (no LLM: the experience itself sets the implied change and strength)."""
    items, decisions = [], []
    for r in rep["perils"]:
        if not r["propose"]:
            continue
        iid = f"experience:{r['peril']}:{rep['claims']}:{rep['policy_years']}"
        label = "SIMULATED claims book" if rep["simulated"] else "claims experience"
        items.append({"id": iid, "source": "claims_experience", "type": "experience",
                      "title": f"A/E {r['ae_ratio']:.2f} for {r['peril'].replace('_', ' ')} "
                               f"({r['actual_claims']} actual vs {r['expected_claims']:.1f} expected claims)",
                      "url": None, "date": str(date.today()),
                      "summary": f"{label}: {rep['policy_years']} policy-years."})
        decisions.append({"item_id": iid, "relevant": True, "parameter": r["parameter"],
                          "implied_change": r["multiplier"] - 1, "strength": r["strength"],
                          "event_description": (f"Over {rep['policy_years']:,} policy-years of {label}, {r['peril'].replace('_', ' ')} "
                                                f"produced {r['actual_claims']} claims against {r['expected_claims']:.1f} expected "
                                                f"(A/E {r['ae_ratio']:.2f})."),
                          "reasoning": (f"Credibility-weighted frequency multiplier m = (K + A) / (K + E) = ({K:.0f} + {r['actual_claims']}) / "
                                        f"({K:.0f} + {r['expected_claims']:.1f}) = {r['multiplier']:.3f}, credibility Z = {r['credibility_z']:.2f}, "
                                        f"90% interval {r['multiplier_90'][0]:.2f}–{r['multiplier_90'][1]:.2f}. Strength '{r['strength']}' "
                                        f"from {r['expected_claims']:.0f} expected claims. Applied to {r['parameter']}.")})
    return items, decisions


def main():
    ap = argparse.ArgumentParser(prog="agents.experience")
    ap.add_argument("--simulate", action="store_true")
    a = ap.parse_args()
    if a.simulate:
        print("Simulated claims book:", simulate())
    rep = report()
    if not rep:
        print("No claims data. Run with --simulate for the demo book."); return
    print(f"A/E over {rep['policy_years']:,} policy-years, {rep['claims']} claims{' (SIMULATED)' if rep['simulated'] else ''}")
    for r in rep["perils"]:
        print(f"  {r['peril']:<24} A {r['actual_claims']:>4}  E {r['expected_claims']:>7.1f}  A/E {r['ae_ratio']:.2f}  "
              f"m {r['multiplier']:.3f} [{r['multiplier_90'][0]:.2f}, {r['multiplier_90'][1]:.2f}]  Z {r['credibility_z']:.2f}"
              f"{'  -> propose ' + r['strength'] if r['propose'] else ''}")


if __name__ == "__main__":
    main()
