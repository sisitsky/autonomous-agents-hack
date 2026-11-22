"""Calculation archive: every equation and number behind a quote.

`EQUATIONS` is the single source for the pricing method. The pricing agent saves
`calculation(...)` output with every quote (calculation.json), so the web app can
show how a premium was built, with the exact inputs, and anyone can reproduce it
from the recorded parameter versions, file hashes, and seed.
"""
from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import distributions as D
from .model import PARAMS_DIR, get_path, insured_priors, peril_modifier

ROOT = Path(__file__).resolve().parent.parent
ENGINE_FILES = ("engine/model.py", "engine/distributions.py", "engine/tornado.py", "engine/controls.py")

DISCLAIMER = ("Illustrative pricing on illustrative assumptions. This is a pricing and "
              "risk-assessment engine, not an offer of insurance.")

# Each equation: plain-text formula, LaTeX (for KaTeX in the web app), and meaning.
EQUATIONS = [
    {"id": "frequency_chain", "name": "Claim frequency (chain perils)",
     "formula": "λ = N × P(error) × P(harm | error) × P(claim | harm) [× r_adv]",
     "latex": r"\lambda = N \cdot P(\text{error}) \cdot P(\text{harm}\mid\text{error}) \cdot P(\text{claim}\mid\text{harm})\ [\cdot\, r_{adv}]",
     "meaning": "Expected claims per year from N outputs per year. r_adv applies only when P(error) comes "
                "from an adversarial red-team suite."},
    {"id": "frequency_rate", "name": "Claim frequency (rate perils)",
     "formula": "λ = rate [× ρ_leak]",
     "latex": r"\lambda = \text{rate}\ [\cdot\, \rho_{leak}]",
     "meaning": "A direct annual claim rate. For the privacy peril, red-team data-leak results multiply it."},
    {"id": "red_team_beta", "name": "Red-team evidence",
     "formula": "P(error) ~ Beta(F + 1, T − F + 1)",
     "latex": r"P(\text{error}) \sim \mathrm{Beta}(F+1,\ T-F+1)",
     "meaning": "F failures in T tested cases, updating a uniform prior. Fewer tests give a wider distribution "
                "and a larger uncertainty load. The same form is used for the leak rate on data-leak probes."},
    {"id": "adversarial_ratio", "name": "Adversarial suite adjustment",
     "formula": "P(error)_live = P(fail | probe) × r_adv",
     "latex": r"P(\text{error})_{live} = P(\text{fail}\mid\text{probe}) \cdot r_{adv}",
     "meaning": "Stress-test suites over-sample failure modes, so the per-probe failure rate is scaled to a "
                "per-live-output rate by an uncertain ratio."},
    {"id": "leak_relativity", "name": "Data-leak relativity",
     "formula": "ρ_leak = clip(L / L_ref, min, max)",
     "latex": r"\rho_{leak} = \mathrm{clip}\!\left(\tfrac{L}{L_{ref}},\ \min,\ \max\right)",
     "meaning": "The insured's leak rate on probes divided by a typical system's, bounded."},
    {"id": "rating_modifier", "name": "Rating-factor modifier",
     "formula": "M = clip(∏ f_i, min, max)",
     "latex": r"M = \mathrm{clip}\!\left(\prod_i f_i,\ \min,\ \max\right)",
     "meaning": "Relativities of the rating factors that apply to the peril, multiplied, then capped so "
                "stacked factors can't produce extreme prices."},
    {"id": "claim_count", "name": "Claims in a simulated year",
     "formula": "n ~ Poisson(λ × M)",
     "latex": r"n \sim \mathrm{Poisson}(\lambda \cdot M)",
     "meaning": "Claim count per simulated year for one peril."},
    {"id": "severity", "name": "Claim size",
     "formula": "X ~ Lognormal(μ = ln m, σ = (ln p90 − ln m) / 1.2816); with probability q, X = T × U^(−1/α)",
     "latex": r"X \sim \mathrm{LN}\!\left(\ln m,\ \tfrac{\ln p_{90} - \ln m}{1.2816}\right);\ \text{w.p. } q:\ X = T\,U^{-1/\alpha}",
     "meaning": "Lognormal body set by its median m and 90th percentile p90, plus an optional Pareto tail "
                "above threshold T with shape α (lower α = fatter tail)."},
    {"id": "policy_layer", "name": "Policy terms",
     "formula": "paid = min(max(X − d, 0), L_claim); annual total ≤ A",
     "latex": r"\text{paid} = \min(\max(X - d, 0),\ L_{claim});\quad \textstyle\sum \le A",
     "meaning": "Deductible d and per-claim limit apply to each claim; the aggregate limit A caps the year."},
    {"id": "systemic", "name": "Systemic event",
     "formula": "loss_sys = ℓ × A, if an event occurs (prob p_s) and hits this insured (prob s)",
     "latex": r"\text{loss}_{sys} = \ell \cdot A \quad \text{w.p. } p_s \cdot s",
     "meaning": "A correlated failure at the insured's model provider (bad update, new jailbreak, adverse ruling)."},
    {"id": "expected_loss", "name": "Expected loss",
     "formula": "EL = mean over simulated years of min(idiosyncratic + systemic, A)",
     "latex": r"EL = \mathbb{E}\left[\min(\text{idio} + \text{sys},\ A)\right]",
     "meaning": "Two-layer Monte Carlo: n_outer parameter draws (what we don't know) × n_inner years each (luck)."},
    {"id": "uncertainty_load", "name": "Parameter-uncertainty load",
     "formula": "U = max(0, P_k(EL by parameter draw) − EL)",
     "latex": r"U = \max\!\left(0,\ P_k\!\left[EL_j\right] - EL\right)",
     "meaning": "Charges for not knowing the parameters: the gap between the k-th percentile of expected loss "
                "across parameter draws and its mean."},
    {"id": "capital", "name": "Risk capital",
     "formula": "C = (TVaR(idio) − mean(idio)) / √n_pool + c × (TVaR(sys) − mean(sys))",
     "latex": r"C = \frac{\mathrm{TVaR}(\text{idio}) - \overline{\text{idio}}}{\sqrt{n_{pool}}} + c\,\left(\mathrm{TVaR}(\text{sys}) - \overline{\text{sys}}\right)",
     "meaning": "Independent losses diversify across the pool; systemic losses get no diversification credit."},
    {"id": "concentration", "name": "Provider concentration",
     "formula": "c = provider share of portfolio / average provider share",
     "latex": r"c = \frac{\text{share}_{provider}}{\overline{\text{share}}}",
     "meaning": "Policies on heavily used model providers carry more systemic capital."},
    {"id": "capital_load", "name": "Capital load",
     "formula": "K = cost of capital × C",
     "latex": r"K = r_{coc} \cdot C",
     "meaning": "The cost of holding the risk capital for a year."},
    {"id": "gross_premium", "name": "Gross premium",
     "formula": "G = (EL + U + K + F) / (1 − v − π)",
     "latex": r"G = \frac{EL + U + K + F}{1 - v - \pi}",
     "meaning": "Technical premium (losses, loads, fixed expense F) grossed up for variable expenses v and profit π."},
    {"id": "rate_on_line", "name": "Rate on line",
     "formula": "ROL = G / A",
     "latex": r"\mathrm{ROL} = G / A",
     "meaning": "Premium as a share of the aggregate limit."},
]


# ---------------------------------------------------------------- provenance

def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit() -> str | None:
    import os
    if os.environ.get("VERCEL_GIT_COMMIT_SHA"):       # deployed: no .git directory
        return os.environ["VERCEL_GIT_COMMIT_SHA"]
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                             text=True, timeout=5)
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "engine", "params"], cwd=ROOT,
                               capture_output=True, text=True, timeout=5).stdout.strip()
        return out.stdout.strip() + ("-dirty" if dirty else "") if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def provenance(params: dict, params_dir: Path | str = PARAMS_DIR) -> dict:
    params_dir = Path(params_dir)
    return {
        "priced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
        "params_versions": {k: params[k].get("version") for k in ("priors", "rating_factors", "pricing")},
        "params_sha256": {f"params/{n}.yaml": _sha256(params_dir / f"{n}.yaml")
                          for n in ("priors", "rating_factors", "pricing", "guardrails")},
        "engine_sha256": {f: _sha256(ROOT / f) for f in ENGINE_FILES if (ROOT / f).exists()},
    }


# ---------------------------------------------------------------- record

def _param_entry(priors: dict, path: str, used_mean: float) -> dict:
    spec = get_path(priors, path)
    entry = {"path": path, "mean_used": used_mean, "median": D.central(spec)}
    if D.is_dist(spec):
        entry["distribution"] = {k: v for k, v in spec.items() if k != "source"}
        entry["source"] = spec.get("source", "")
    else:
        entry["distribution"] = {"dist": "fixed", "value": spec}
    return entry


def calculation(insured: dict, params: dict, quote: dict, tornado: dict | None = None,
                controls: dict | None = None, params_dir: Path | str = PARAMS_DIR) -> dict:
    """Assemble the full archive for one quote from the engine's own outputs."""
    priors = insured_priors(params, insured)
    rf = params["rating_factors"]
    t = quote["trace"]

    perils = {}
    for name, d in quote["by_peril"].items():
        freq = priors["perils"][name]["frequency"]
        _, detail = peril_modifier(params, insured, name)
        raw = 1.0
        for v in detail.values():
            raw *= v
        perils[name] = {
            "description": priors["perils"][name]["description"],
            "frequency_mode": freq["mode"],
            "exposure": insured["perils"][name],
            "equation": "frequency_chain" if freq["mode"] == "chain" else "frequency_rate",
            "base_claims_per_year": d["base_claims_per_year"],
            "red_team_leak_relativity": d.get("red_team_leak_relativity"),
            "rating_factors": [{"factor": f, "level": insured["factors"][f], "relativity": rel,
                                "source": rf["factors"][f]["source"]} for f, rel in detail.items()],
            "combined_relativity_raw": raw,
            "modifier_bounds": rf["combined_modifier_bounds"],
            "modifier": d["modifier"],
            "expected_claims_per_year": d["expected_claims"],
            "expected_loss": d["expected_loss"],
            "parameters": [_param_entry(priors, p, m) for p, m in d["parameters"].items()],
        }

    cap, unc, ex, pol = t["capital"], t["uncertainty"], t["expenses"], t["policy"]
    conc_mult = quote["capital"]["concentration_multiplier"]
    gross_up = 1 - ex["variable_ratio"] - ex["profit_margin"]
    steps = [
        {"id": "expected_loss", "label": "Expected loss (after deductible and limits)",
         "equation": "expected_loss", "value": quote["expected_loss"],
         "inputs": {"idiosyncratic": t["expected_idiosyncratic_loss"],
                    "systemic": quote["expected_systemic_loss"], "aggregate_limit": pol["aggregate_limit"]}},
        {"id": "uncertainty_load", "label": f"Parameter-uncertainty load (P{unc['percentile']} − mean)",
         "equation": "uncertainty_load", "value": quote["uncertainty_load"],
         "inputs": {"percentile": unc["percentile"],
                    "expected_loss_at_percentile": unc["expected_loss_at_percentile"],
                    "expected_loss": quote["expected_loss"]}},
        {"id": "capital", "label": f"Risk capital ({cap['tail_metric'].upper()} at {cap['level']:.1%})",
         "equation": "capital", "value": quote["capital"]["total"],
         "inputs": {"idiosyncratic_tail": cap["idiosyncratic_tail"], "idiosyncratic_mean": cap["idiosyncratic_mean"],
                    "pool_size": cap["portfolio_size"], "idiosyncratic_capital": quote["capital"]["idiosyncratic"],
                    "systemic_tail": cap["systemic_tail"], "systemic_mean": cap["systemic_mean"],
                    "concentration_multiplier": conc_mult, "systemic_capital": quote["capital"]["systemic"],
                    "provider": cap["provider"], "provider_share": cap["provider_share"],
                    "average_provider_share": cap["average_provider_share"]}},
        {"id": "capital_load", "label": "Capital load", "equation": "capital_load",
         "value": quote["capital_load"],
         "inputs": {"cost_of_capital": cap["cost_of_capital"], "capital": quote["capital"]["total"]}},
        {"id": "fixed_expense", "label": "Fixed expense (underwriting, red-team testing)",
         "equation": "gross_premium", "value": quote["fixed_expense"], "inputs": {}},
        {"id": "technical_premium", "label": "Technical premium (EL + U + K + F)",
         "equation": "gross_premium", "value": quote["technical_premium"], "inputs": {}},
        {"id": "gross_premium", "label": "Gross premium", "equation": "gross_premium",
         "value": quote["gross_premium"],
         "inputs": {"technical_premium": quote["technical_premium"], "variable_ratio": ex["variable_ratio"],
                    "profit_margin": ex["profit_margin"], "divisor": gross_up}},
        {"id": "rate_on_line", "label": "Rate on line", "equation": "rate_on_line",
         "value": quote["rate_on_line"], "inputs": {"aggregate_limit": pol["aggregate_limit"]}},
    ]

    sys_params = [_param_entry(priors, p, m) for p, m in t["systemic_parameters"].items()]
    return {
        "disclaimer": DISCLAIMER,
        "meta": {**provenance(params, params_dir), "simulation": t["simulation"],
                 "deterministic": "Same insured + same parameter files + same seed => identical numbers."},
        "insured": {k: v for k, v in insured.items()},
        "policy": pol,
        "perils": perils,
        "systemic": {"provider": insured.get("provider"), "expected_loss": quote["expected_systemic_loss"],
                     "parameters": sys_params},
        "premium_build_up": steps,
        "loss_distribution": {"percentiles": quote["loss_percentiles"], "prob_any_loss": quote["prob_any_loss"],
                              "expected_loss": quote["expected_loss"], "histogram": t["loss_histogram"]},
        "tornado": tornado,
        "controls": controls,
        "equations": EQUATIONS,
    }
