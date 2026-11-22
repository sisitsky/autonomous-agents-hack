"""Jewell multivariate credibility for red-team outcomes (Dirichlet model).

Every red-team case ends in exactly one outcome: a pass, or one failure type
(wrong information, unauthorized commitment, data leakage, prompt injection).
For one AI system, the outcome shares θ = (θ_pass, θ_1, ..., θ_K) are unknown;
across the portfolio of tested systems we model

    θ ~ Dirichlet(A · μ)               (μ: portfolio mean shares, A: concentration)
    counts | θ ~ Multinomial(n, θ)

Jewell (1974) showed that for this conjugate pair the Bayesian posterior mean is
exactly linear (exact credibility):

    E[θ_k | counts] = Z · X_k + (1 − Z) · μ_k,     Z = n / (n + A)

and under the Dirichlet every outcome has the same Bühlmann constant
k_k = EPV_k / VHM_k = A. We estimate μ and A from every *other* tested system
(leave-one-out), non-parametrically, with Bühlmann–Straub per outcome:

    μ_k   = Σ_i n_i X_ik / m
    EPV_k = Σ_i n_i X_ik (1 − X_ik) / Σ_i (n_i − 1)
    VHM_k = [Σ_i n_i (X_ik − μ_k)² − (r − 1) EPV_k] / (m − Σ_i n_i² / m)
    A     = Σ_k EPV_k / Σ_k VHM_k            (pooled across outcomes)

The posterior Dirichlet(A μ + counts) then carries both the credibility estimate
(its mean) and the parameter uncertainty (its spread) into the Monte Carlo.

The store (data/portfolio/redteam_experience.jsonl) is append-and-replace: each
underwriting run records its outcome counts, replacing that insured's earlier
record, so the collective is re-estimated from a growing portfolio on every run.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPERIENCE_PATH = ROOT / "data" / "portfolio" / "redteam_experience.jsonl"
MIN_RISKS = 5          # fewer other insureds than this: use the fallback prior
VHM_FLOOR = 1e-6


def load_experience(path: Path | str | None = None) -> list[dict]:
    path = Path(path or EXPERIENCE_PATH)
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [r for r in rows if "outcomes" in r]


def groups(experience: list[dict], suite_id: str, exclude: str | None = None) -> list[dict]:
    """One group per insured on this suite: outcome counts summed over its records."""
    out: dict[str, dict] = {}
    for rec in experience:
        if rec["suite_id"] != suite_id or rec["insured_id"] == exclude:
            continue
        g = out.setdefault(rec["insured_id"], {"insured_id": rec["insured_id"], "n": 0, "outcomes": {},
                                               "source": rec.get("source", "")})
        g["n"] += int(rec["tested"])
        for k, v in rec["outcomes"].items():
            g["outcomes"][k] = g["outcomes"].get(k, 0) + int(v)
    return [g for g in out.values() if g["n"] > 0]


def buhlmann_straub(ns: list[int], xs: list[float]) -> dict:
    """Bühlmann–Straub estimates for one pass/fail outcome across insureds."""
    r, m = len(ns), sum(ns)
    mu = sum(n * x for n, x in zip(ns, xs)) / m
    dof = sum(n - 1 for n in ns)
    epv = sum(n * x * (1 - x) for n, x in zip(ns, xs)) / dof if dof > 0 else 0.0
    c = m - sum(n * n for n in ns) / m
    vhm = (sum(n * (x - mu) ** 2 for n, x in zip(ns, xs)) - (r - 1) * epv) / c if c > 0 else 0.0
    return {"mu": mu, "epv": epv, "vhm": vhm}


def jewell(experience: list[dict], suite_id: str, outcomes: list[str], exclude: str | None = None) -> dict:
    """Portfolio Dirichlet prior (μ, A) by Jewell / Bühlmann–Straub, leave-one-out."""
    gs = groups(experience, suite_id, exclude)
    r = len(gs)
    if r < 2:
        return {"status": "insufficient_experience", "r": r}
    ns = [g["n"] for g in gs]
    per = {}
    for k in outcomes:
        xs = [g["outcomes"].get(k, 0) / g["n"] for g in gs]
        per[k] = buhlmann_straub(ns, xs)
    epv = sum(p["epv"] for p in per.values())
    vhm_raw = sum(p["vhm"] for p in per.values())
    vhm = max(vhm_raw, VHM_FLOOR)
    A = epv / vhm if epv > 0 else 0.0
    mu = {k: per[k]["mu"] for k in outcomes}
    return {"status": "ok" if vhm_raw > 0 else "no_heterogeneity", "r": r, "m": sum(ns), "A": A,
            "mu": mu, "per_outcome": {k: {"epv": per[k]["epv"], "vhm": per[k]["vhm"],
                                          "k": per[k]["epv"] / per[k]["vhm"] if per[k]["vhm"] > 0 else None}
                                      for k in outcomes},
            "groups": gs}


def credibility(insured_id: str | None, suite_id: str, tested: int, counts: dict, outcomes: list[str],
                experience: list[dict], fallback: dict, min_risks: int = MIN_RISKS) -> dict:
    """Dirichlet posterior for one insured: Jewell prior + its own outcome counts.

    counts holds failure counts by type; passes are tested − Σ failures.
    Returns the prior (A, μ), credibility Z, observed and credibility-weighted shares,
    the posterior Dirichlet α, and every other insured's shrinkage for charts."""
    counts = {k: int(counts.get(k, 0)) for k in outcomes if k != "pass"}
    fails = sum(counts.values())
    if tested < 0 or fails > tested:
        raise ValueError(f"red-team failures ({fails}) exceed cases tested ({tested})")
    full = {"pass": tested - fails, **counts}
    est = jewell(experience, suite_id, outcomes, exclude=insured_id)
    if est["status"] == "insufficient_experience" or est["r"] < min_risks:
        A, mu = float(fallback["concentration"]), dict(fallback["mean"])
        status = "insufficient_experience"
        note = (f"Only {est['r']} other insureds on suite {suite_id} (need {min_risks}); "
                f"fallback prior worth {A:g} cases.")
    else:
        A, mu, status, note = est["A"], est["mu"], est["status"], ""
    z = tested / (tested + A) if tested + A > 0 else 0.0
    observed = {k: (full[k] / tested if tested else None) for k in outcomes}
    estimate = {k: (z * observed[k] + (1 - z) * mu[k]) if tested else mu[k] for k in outcomes}
    alpha = {k: A * mu[k] + full[k] for k in outcomes}
    portfolio = []
    for g in est.get("groups", []):
        gz = g["n"] / (g["n"] + A)
        obs = {k: g["outcomes"].get(k, 0) / g["n"] for k in outcomes}
        portfolio.append({"insured_id": g["insured_id"], "n": g["n"], "z": gz, "source": g["source"],
                          "observed": obs, "estimate": {k: gz * obs[k] + (1 - gz) * mu[k] for k in outcomes}})
    return {"status": status, "note": note, "suite_id": suite_id, "n": tested, "counts": full,
            "r": est["r"], "A": A, "k": A, "z": z, "mu": mu, "observed": observed, "estimate": estimate,
            "alpha": alpha, "alpha0": sum(alpha.values()), "per_outcome": est.get("per_outcome"),
            "portfolio": portfolio}


def record(insured_id: str, suite_id: str, tested: int, counts: dict, source: str,
           path: Path | str | None = None, run_at: str | None = None) -> list[dict]:
    """Add this insured's latest outcome counts to the experience store, replacing
    its earlier record on this suite. Returns the new store."""
    path = Path(path or EXPERIENCE_PATH)
    run_at = run_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    exp = [e for e in load_experience(path) if not (e["insured_id"] == insured_id and e["suite_id"] == suite_id)]
    fails = sum(int(v) for v in counts.values())
    exp.append({"insured_id": insured_id, "suite_id": suite_id, "tested": int(tested),
                "outcomes": {"pass": int(tested) - fails, **{k: int(v) for k, v in counts.items()}},
                "run_at": run_at, "source": source})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in exp))
    return exp
