"""AI liability pricing model.

Two-layer Monte Carlo:
  outer layer  = parameter uncertainty (what we don't know about the risk)
  inner layer  = process randomness (luck, given the parameters)

Gross premium = (expected loss + uncertainty load + capital load + fixed expense)
                / (1 - variable expense ratio - profit margin)

All pricing math lives here, in deterministic code. Agents call it; they never
compute prices themselves.
"""
from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
import yaml

from . import distributions as D

PARAMS_DIR = Path(__file__).resolve().parent.parent / "params"


# ---------------------------------------------------------------- loading

def load_params(params_dir: Path | str = PARAMS_DIR) -> dict:
    params_dir = Path(params_dir)
    out = {}
    for name in ("priors", "rating_factors", "pricing", "guardrails"):
        with open(params_dir / f"{name}.yaml") as f:
            out[name] = yaml.safe_load(f)
    return out


def get_path(tree: dict, path: str):
    node = tree
    for key in path.split("."):
        node = node[key]
    return node


def set_path(tree: dict, path: str, value):
    keys = path.split(".")
    node = tree
    for key in keys[:-1]:
        node = node[key]
    node[keys[-1]] = value


def uncertain_paths(priors: dict, perils: list[str]) -> list[str]:
    """Every parameter path (within the insured's perils + systemic) that is a distribution."""
    found = []

    def walk(node, prefix):
        # A distribution whose own inputs are uncertain (severity body) is a
        # container: recurse into it rather than treating it as one parameter.
        nested = isinstance(node, dict) and any(D.is_dist(v) for v in node.values())
        if D.is_dist(node) and not nested:
            found.append(prefix)
            return
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("source", "description"):
                    continue
                walk(v, f"{prefix}.{k}" if prefix else k)

    for p in perils:
        walk(priors["perils"][p], f"perils.{p}")
    walk(priors["systemic"], "systemic")
    return found


# ---------------------------------------------------------------- insured setup

def insured_priors(params: dict, insured: dict) -> dict:
    """Copy of priors with insured-specific evidence applied (red-team counts).

    Red-team counts become a Beta(failures + 1, passes + 1) for P(error), the
    posterior from a uniform prior. Two kinds of test are supported:

      suite: live_sample  (default) cases sampled from ordinary traffic, so the
                          failure rate is the per-output error rate directly.
      suite: adversarial  a stress-test suite that over-samples failure modes.
                          The failure rate is per probe, not per live output, so
                          it is scaled by `red_team.adversarial_to_live` from
                          priors (an uncertain ratio, applied as an extra factor
                          in the frequency chain).
    """
    priors = copy.deepcopy(params["priors"])
    rt = insured.get("red_team")
    if rt and "misinformation" in priors["perils"]:
        failures, tested = rt["failures"], rt["tested"]
        if not 0 <= failures <= tested or tested <= 0:
            raise ValueError(f"red_team needs 0 <= failures <= tested and tested > 0: {rt}")
        suite = rt.get("suite", "live_sample")
        freq = priors["perils"]["misinformation"]["frequency"]
        freq["p_error"] = {
            "dist": "beta",
            "alpha": failures + 1,
            "beta": tested - failures + 1,
            "source": f"Insured red-team test ({suite}): {failures} failures in {tested} cases.",
        }
        if suite == "adversarial":
            freq["adversarial_to_live"] = copy.deepcopy(priors["red_team"]["adversarial_to_live"])
        elif suite != "live_sample":
            raise ValueError(f"Unknown red_team suite '{suite}' (use live_sample or adversarial)")
    return priors


def peril_modifier(params: dict, insured: dict, peril: str) -> tuple[float, dict]:
    rf = params["rating_factors"]
    combined, detail = 1.0, {}
    for name, spec in rf["factors"].items():
        if peril not in spec["applies_to"]:
            continue
        level = insured.get("factors", {}).get(name)
        if level is None:
            continue
        if level not in spec["levels"]:
            raise ValueError(f"Unknown level '{level}' for factor '{name}'")
        rel = spec["levels"][level]
        detail[name] = rel
        combined *= rel
    b = rf["combined_modifier_bounds"]
    capped = min(max(combined, b["min"]), b["max"])
    return capped, detail


# ---------------------------------------------------------------- simulation

def _draw(priors, path, rng, n, mode, overrides):
    if overrides and path in overrides:
        return np.full(n, float(overrides[path]))
    spec = get_path(priors, path)
    if mode == "point":
        return np.full(n, D.central(spec))
    return D.sample(spec, rng, n)


def _tvar(x: np.ndarray, level: float) -> float:
    cutoff = np.quantile(x, level)
    tail = x[x >= cutoff]
    return float(tail.mean()) if tail.size else float(cutoff)


def price(insured: dict, params: dict | None = None, mode: str = "full",
          overrides: dict | None = None, n_outer: int | None = None,
          n_inner: int | None = None, seed: int | None = None) -> dict:
    """Price one insured.

    mode="full":  sample parameters (outer layer) and simulate years (inner layer).
    mode="point": hold every parameter at its median (plus any overrides) and
                  simulate the same total number of years. Used for tornado charts;
                  the parameter-uncertainty load is zero by construction.
    """
    params = params or load_params()
    priors = insured_priors(params, insured)
    sim = priors["simulation"]
    n_outer = n_outer or sim["n_outer"]
    n_inner = n_inner or sim["n_inner"]
    seed = sim["seed"] if seed is None else seed
    if mode == "point":
        n_inner, n_outer = n_outer * n_inner, 1
    rng = np.random.default_rng(seed)
    n_years = n_outer * n_inner

    pol = insured["policy"]
    ded, per_claim, agg = pol["deductible"], pol["limit_per_claim"], pol["aggregate_limit"]

    idio = np.zeros(n_years)
    by_peril = {}

    for peril, exposure in insured["perils"].items():
        base = f"perils.{peril}"
        spec = priors["perils"][peril]
        freq = spec["frequency"]

        if freq["mode"] == "chain":
            volume = exposure[freq["exposure_field"]]
            lam = volume
            chain = ["p_error", "p_harm_given_error", "p_claim_given_harm"]
            if "adversarial_to_live" in freq:
                chain.append("adversarial_to_live")
            for p in chain:
                lam = lam * _draw(priors, f"{base}.frequency.{p}", rng, n_outer, mode, overrides)
        else:
            lam = _draw(priors, f"{base}.frequency.rate", rng, n_outer, mode, overrides)

        modifier, detail = peril_modifier(params, insured, peril)
        lam = lam * modifier

        median = _draw(priors, f"{base}.severity.body.median", rng, n_outer, mode, overrides)
        p90 = _draw(priors, f"{base}.severity.body.p90", rng, n_outer, mode, overrides)
        mu, sigma = D.lognormal_params(median, p90)
        tail = spec["severity"].get("tail")
        if tail:
            t_prob = _draw(priors, f"{base}.severity.tail.prob", rng, n_outer, mode, overrides)
            t_alpha = _draw(priors, f"{base}.severity.tail.alpha", rng, n_outer, mode, overrides)

        counts = rng.poisson(np.repeat(lam, n_inner))          # claims per simulated year
        year_idx = np.repeat(np.arange(n_years), counts)        # which year each claim is in
        outer_idx = year_idx // n_inner                         # which parameter draw
        x = rng.lognormal(mu[outer_idx], sigma[outer_idx])
        if tail:
            is_tail = rng.random(x.size) < t_prob[outer_idx]
            u = rng.random(int(is_tail.sum()))
            x[is_tail] = tail["threshold"] * u ** (-1.0 / t_alpha[outer_idx][is_tail])
        layer = np.clip(x - ded, 0, per_claim)
        annual = np.bincount(year_idx, weights=layer, minlength=n_years)

        idio += annual
        by_peril[peril] = {
            "expected_claims": float(lam.mean()),
            "expected_loss": float(annual.mean()),
            "modifier": modifier,
            "factors": detail,
        }

    # Systemic: correlated event at the insured's model provider.
    ev_p = _draw(priors, "systemic.annual_prob", rng, n_outer, mode, overrides)
    share = _draw(priors, "systemic.share_affected", rng, n_outer, mode, overrides)
    loss_share = _draw(priors, "systemic.loss_share_of_limit", rng, n_outer, mode, overrides)
    o = np.repeat(np.arange(n_outer), n_inner)
    hit = (rng.random(n_years) < ev_p[o]) & (rng.random(n_years) < share[o])
    systemic = np.where(hit, loss_share[o] * agg, 0.0)

    total = np.minimum(idio + systemic, agg)
    idio_c, sys_c = np.minimum(idio, agg), np.minimum(systemic, agg)

    pr = params["pricing"]
    expected_loss = float(total.mean())

    per_outer = total.reshape(n_outer, n_inner).mean(axis=1)
    unc_load = 0.0 if n_outer == 1 else max(
        0.0, float(np.percentile(per_outer, pr["uncertainty_load"]["percentile"])) - expected_loss)

    cap = pr["capital"]
    tail_fn = _tvar if cap["tail_metric"] == "tvar" else (lambda x, q: float(np.quantile(x, q)))
    conc = cap["provider_concentration"]
    conc_mult = conc.get(insured.get("provider"), np.mean(list(conc.values()))) / np.mean(list(conc.values()))
    idio_capital = (1 / np.sqrt(cap["portfolio_size"])) * max(0.0, tail_fn(idio_c, cap["level"]) - idio_c.mean())
    sys_capital = conc_mult * max(0.0, tail_fn(sys_c, cap["level"]) - sys_c.mean())
    capital = idio_capital + sys_capital
    capital_load = cap["cost_of_capital"] * capital

    ex = pr["expenses"]
    technical = expected_loss + unc_load + capital_load + ex["fixed_per_policy"]
    gross = technical / (1 - ex["variable_ratio"] - ex["profit_margin"])

    return {
        "insured_id": insured.get("id"),
        "mode": mode,
        "expected_loss": expected_loss,
        "expected_systemic_loss": float(sys_c.mean()),
        "by_peril": by_peril,
        "uncertainty_load": unc_load,
        "capital": {"idiosyncratic": idio_capital, "systemic": sys_capital,
                    "concentration_multiplier": float(conc_mult), "total": capital},
        "capital_load": capital_load,
        "fixed_expense": ex["fixed_per_policy"],
        "technical_premium": technical,
        "gross_premium": gross,
        "rate_on_line": gross / agg,
        "loss_percentiles": {str(q): float(np.quantile(total, q / 100))
                             for q in (50, 75, 90, 95, 99, 99.5)},
        "prob_any_loss": float((total > 0).mean()),
    }
