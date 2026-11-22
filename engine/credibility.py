"""Bühlmann–Straub credibility for red-team results.

Each underwriting run tests one AI system with the same adversarial suite and
records its counts in the portfolio experience store
(data/portfolio/redteam_experience.jsonl). Before an insured is priced, the
engine estimates from the rest of the portfolio:

    μ    collective mean failure rate             μ = Σ n_i X_i / m
    EPV  expected process variance (within risk)  EPV = Σ_i Σ_j (x_ij − X_i)² / Σ (n_i − 1)
                                                      = Σ n_i X_i (1 − X_i) / Σ (n_i − 1)   for pass/fail cases
    VHM  variance of hypothetical means (between) VHM = [Σ n_i (X_i − μ)² − (r − 1) EPV] / (m − Σ n_i² / m)
    k    credibility constant                     k = EPV / VHM
    Z    credibility of the insured's own tests   Z = n / (n + k)

and the credibility-weighted failure rate Z · X + (1 − Z) · μ.

This plugs into the engine's Beta for the failure rate as the prior
Beta(μk, (1 − μ)k): the posterior Beta(F + μk, n − F + (1 − μ)k) has exactly
the Bühlmann mean, so the credibility estimate and the parameter uncertainty
come from one distribution. With too little portfolio experience the engine
falls back to the uniform prior Beta(1, 1).

The insured's own record is left out of the collective (leave-one-out), so its
result is never counted twice. The store is append-and-replace: a new run for
the same insured replaces its earlier record, and every run re-estimates μ, EPV,
VHM, and k, so the collective keeps updating as the portfolio grows.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPERIENCE_PATH = ROOT / "data" / "portfolio" / "redteam_experience.jsonl"
MEASURES = ("p_error", "data_leak")
MIN_RISKS = 5          # fewer insureds than this: not enough experience, use the uniform prior
VHM_FLOOR = 1e-6       # no detectable between-risk variation -> Z near 0


def load_experience(path: Path | str | None = None) -> list[dict]:
    path = Path(path or EXPERIENCE_PATH)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def groups(experience: list[dict], measure: str, suite_id: str, exclude: str | None = None) -> list[dict]:
    """One group per insured: counts summed over its records for this measure and suite."""
    out: dict[str, dict] = {}
    for rec in experience:
        if rec["measure"] != measure or rec["suite_id"] != suite_id or rec["insured_id"] == exclude:
            continue
        g = out.setdefault(rec["insured_id"], {"insured_id": rec["insured_id"], "n": 0, "failures": 0,
                                               "source": rec.get("source", "")})
        g["n"] += int(rec["tested"])
        g["failures"] += int(rec["failures"])
    return [g for g in out.values() if g["n"] > 0]


def buhlmann_straub(gs: list[dict]) -> dict:
    """Non-parametric Bühlmann–Straub estimates from pass/fail groups."""
    r = len(gs)
    if r < 2:
        return {"status": "insufficient_experience", "r": r}
    m = sum(g["n"] for g in gs)
    xs = [g["failures"] / g["n"] for g in gs]
    mu = sum(g["n"] * x for g, x in zip(gs, xs)) / m
    dof = sum(g["n"] - 1 for g in gs)
    epv = sum(g["n"] * x * (1 - x) for g, x in zip(gs, xs)) / dof if dof > 0 else 0.0
    c = m - sum(g["n"] ** 2 for g in gs) / m
    between = sum(g["n"] * (x - mu) ** 2 for g, x in zip(gs, xs))
    vhm_raw = (between - (r - 1) * epv) / c if c > 0 else 0.0
    vhm = max(vhm_raw, VHM_FLOOR)
    k = epv / vhm if epv > 0 else 0.0
    return {"status": "ok" if vhm_raw > 0 else "no_heterogeneity", "r": r, "m": m, "mu": mu,
            "epv": epv, "vhm": vhm, "vhm_raw": vhm_raw, "k": k}


def credibility(insured_id: str | None, measure: str, suite_id: str, tested: int, failures: int,
                experience: list[dict], min_risks: int = MIN_RISKS) -> dict:
    """Credibility analysis for one insured's result on one measure."""
    gs = groups(experience, measure, suite_id, exclude=insured_id)
    est = buhlmann_straub(gs)
    x = failures / tested
    out = {"measure": measure, "suite_id": suite_id, "n": tested, "failures": failures, "observed": x, **est}
    if est["status"] == "insufficient_experience" or est["r"] < min_risks:
        out["status"] = "insufficient_experience"
        out.update(prior_alpha=1.0, prior_beta=1.0, z=tested / (tested + 2), k=2.0, mu=est.get("mu", 0.5),
                   note=f"Only {est['r']} other insureds on this suite (need {min_risks}); uniform prior Beta(1, 1).")
        out["estimate"] = (failures + 1) / (tested + 2)
        out["portfolio"] = gs
        return out
    k, mu = est["k"], est["mu"]
    z = tested / (tested + k)
    out.update(z=z, estimate=z * x + (1 - z) * mu, prior_alpha=mu * k, prior_beta=(1 - mu) * k)
    # Shrinkage for the rest of the portfolio, for the chart.
    out["portfolio"] = [{**g, "observed": g["failures"] / g["n"], "z": g["n"] / (g["n"] + k),
                         "estimate": g["n"] / (g["n"] + k) * g["failures"] / g["n"] + k / (g["n"] + k) * mu}
                        for g in gs]
    return out


def record(insured_id: str, suite_id: str, counts: dict, source: str,
           path: Path | str | None = None, run_at: str | None = None) -> list[dict]:
    """Add this insured's latest red-team result to the experience store,
    replacing its earlier records for the same suite. Returns the new store."""
    path = Path(path or EXPERIENCE_PATH)
    run_at = run_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    exp = [e for e in load_experience(path) if not (e["insured_id"] == insured_id and e["suite_id"] == suite_id)]
    for measure in MEASURES:
        c = counts.get(measure)
        if c and c.get("tested"):
            exp.append({"insured_id": insured_id, "suite_id": suite_id, "measure": measure,
                        "tested": int(c["tested"]), "failures": int(c["failures"]), "run_at": run_at, "source": source})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in exp))
    return exp
