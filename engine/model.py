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
import zlib
from pathlib import Path

import numpy as np
import yaml

from . import credibility as CRED
from . import distributions as D

PARAMS_DIR = Path(__file__).resolve().parent.parent / "params"


# ---------------------------------------------------------------- loading

def load_params(params_dir: Path | str = PARAMS_DIR) -> dict:
    params_dir = Path(params_dir)
    out = {}
    for name in ("priors", "rating_factors", "pricing", "guardrails"):
        with open(params_dir / f"{name}.yaml") as f:
            out[name] = yaml.safe_load(f)
    # Portfolio red-team experience for Bühlmann credibility (data, not assumptions).
    out["experience"] = CRED.load_experience()
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

def _red_team_beta(params: dict, insured: dict, measure: str, tested: int, failures: int,
                   suite: str, what: str) -> dict:
    """Beta for a red-team failure rate.

    Adversarial suites with enough portfolio experience use the Bühlmann–Straub
    prior Beta(μk, (1 − μ)k), so the posterior mean is the credibility estimate
    Z·X + (1 − Z)·μ with Z = n / (n + k). Otherwise the uniform prior Beta(1, 1).
    """
    rt = insured.get("red_team") or {}
    if suite == "adversarial" and params.get("experience") is not None:
        cred = CRED.credibility(insured.get("id"), measure, rt.get("suite_id", "customer_service"),
                                tested, failures, params["experience"])
        a0, b0 = cred["prior_alpha"], cred["prior_beta"]
        how = (f"Bühlmann credibility Z = {cred['z']:.2f} (k = {cred['k']:.1f}, collective μ = {cred['mu']:.3f}, "
               f"{cred['r']} insureds)" if cred["status"] != "insufficient_experience" else cred["note"])
        return {"dist": "beta", "alpha": failures + a0, "beta": tested - failures + b0,
                "source": f"{what}; {how}.", "credibility": cred}
    return {"dist": "beta", "alpha": failures + 1, "beta": tested - failures + 1, "source": f"{what}."}


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
        freq["p_error"] = _red_team_beta(params, insured, "p_error", tested, failures, suite,
                                         f"Insured red-team test ({suite}): {failures} failures in {tested} cases")
        if suite == "adversarial":
            freq["adversarial_to_live"] = copy.deepcopy(priors["red_team"]["adversarial_to_live"])
        elif suite != "live_sample":
            raise ValueError(f"Unknown red_team suite '{suite}' (use live_sample or adversarial)")

    # Data-leak probes scale the privacy peril's rate. The leak rate on probes,
    # Beta(leaks + 1, passes + 1), is compared with the leak rate of a typical
    # system on the same kind of probes (`red_team.privacy.reference_leak_rate`).
    # Their ratio multiplies the privacy rate, within `relativity_bounds`.
    leak = (rt or {}).get("data_leak")
    if leak and "privacy_regulatory" in priors["perils"]:
        leaks, probes = leak["failures"], leak["tested"]
        if not 0 <= leaks <= probes or probes <= 0:
            raise ValueError(f"red_team.data_leak needs 0 <= failures <= tested and tested > 0: {leak}")
        ref = priors["red_team"]["privacy"]
        freq = priors["perils"]["privacy_regulatory"]["frequency"]
        freq["leak_rate"] = _red_team_beta(params, insured, "data_leak", probes, leaks, rt.get("suite", "live_sample"),
                                           f"Insured red-team data-leak probes: {leaks} leaks in {probes} probes")
        freq["reference_leak_rate"] = copy.deepcopy(ref["reference_leak_rate"])
        freq["leak_relativity_bounds"] = dict(ref["relativity_bounds"])
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


def _histogram(total: np.ndarray, agg: float, bins: int = 40) -> dict:
    """Annual-loss histogram for charts: share of loss-free years, then equal-width
    bins of positive losses up to the aggregate limit. No random numbers used."""
    pos = total[total > 0]
    counts, edges = np.histogram(pos, bins=bins, range=(0.0, float(agg)))
    return {"n_years": int(total.size), "zero_years": int(total.size - pos.size),
            "edges": [float(e) for e in edges], "counts": [int(c) for c in counts],
            "at_aggregate_limit": int((total >= agg).sum())}


def _illustration(claims_log, systemic, total, agg, n_inner, n_lines, stream,
                  n_band_years: int = 4000, weeks: int = 52) -> dict:
    """Sample policy-years straight from the simulation for the web graphic.

    Each simulated year's claims are given arrival times (uniform over the year,
    as in a Poisson process, from their own random stream so pricing is
    untouched). The paid amounts are the engine's own, so every path ends at
    exactly that year's simulated annual loss. Returns step paths for a set of
    years spread across parameter draws, percentile bands from a larger sample,
    and the running Monte Carlo estimate of expected loss.
    """
    n_years = total.size
    band_years = np.unique(np.linspace(0, n_years - 1, min(n_band_years, n_years)).astype(np.int64))
    pos = {int(y): i for i, y in enumerate(band_years)}
    events = [[] for _ in band_years]
    for peril, yidx, paid in claims_log:
        sel = np.isin(yidx, band_years)
        t = stream("illustrate", peril).random(int(sel.sum()))
        for y, a, tt in zip(yidx[sel], paid[sel], t):
            if a > 0:
                events[pos[int(y)]].append((float(tt), float(a), peril))
    ts = stream("illustrate", "systemic").random(band_years.size)
    for i, y in enumerate(band_years):
        if systemic[y] > 0:
            events[i].append((float(ts[i]), float(systemic[y]), "systemic"))

    grid = np.linspace(0, 1, weeks + 1)
    cum_grid = np.zeros((band_years.size, grid.size))
    paths = []
    line_pick = set(np.linspace(0, band_years.size - 1, min(n_lines, band_years.size)).astype(int).tolist())
    for i, ev in enumerate(events):
        ev.sort()
        times = np.array([e[0] for e in ev]) if ev else np.zeros(0)
        cum = np.minimum(np.cumsum([e[1] for e in ev]), agg) if ev else np.zeros(0)
        idx = np.searchsorted(times, grid, side="right")
        cum_grid[i] = np.where(idx > 0, cum[np.maximum(idx - 1, 0)] if cum.size else 0.0, 0.0)
        if i in line_pick:
            y = int(band_years[i])
            perils = {}
            for e in ev:
                perils[e[2]] = perils.get(e[2], 0) + 1
            paths.append({"year": y, "parameter_draw": y // n_inner, "final": float(total[y]),
                          "claims": len(ev), "by_peril": perils,
                          "points": [[round(t, 4), round(float(c), 2)] for t, c in zip(times, cum)]})
    pct = (5, 25, 50, 75, 95)
    bands = {str(q): [float(v) for v in np.percentile(cum_grid, q, axis=0)] for q in pct}

    order = stream("illustrate", "order").permutation(n_years)
    shuffled = total[order]
    cps = np.unique(np.geomspace(50, n_years, 60).astype(np.int64))
    csum, csq = np.cumsum(shuffled), np.cumsum(shuffled ** 2)
    mean = csum[cps - 1] / cps
    var = np.maximum(csq[cps - 1] / cps - mean ** 2, 0)
    convergence = {"n": cps.tolist(), "mean": mean.tolist(), "se": (np.sqrt(var / cps)).tolist()}
    return {"grid": grid.tolist(), "bands": bands, "paths": paths, "band_years": int(band_years.size),
            "n_years": int(n_years), "expected_loss": float(total.mean()), "aggregate_limit": float(agg),
            "convergence": convergence}


def price(insured: dict, params: dict | None = None, mode: str = "full",
          overrides: dict | None = None, n_outer: int | None = None,
          n_inner: int | None = None, seed: int | None = None, illustrate: int = 0) -> dict:
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
    # Separate, named random streams (common random numbers): each parameter path
    # and each peril's simulation has its own stream, so changing one peril or
    # parameter never shifts the random numbers used by any other.
    def stream(*key: str) -> np.random.Generator:
        return np.random.default_rng([seed, *(zlib.crc32(k.encode()) for k in key)])
    n_years = n_outer * n_inner

    pol = insured["policy"]
    ded, per_claim, agg = pol["deductible"], pol["limit_per_claim"], pol["aggregate_limit"]

    idio = np.zeros(n_years)
    by_peril = {}
    claims_log = []   # (peril, year index, paid) per claim, kept only for illustration
    used = {}  # trace: mean of every parameter as actually drawn (no extra randomness)

    def draw(path):
        v = _draw(priors, path, stream("param", path), n_outer, mode, overrides)
        used[path] = float(v.mean())
        return v

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
                lam = lam * draw(f"{base}.frequency.{p}")
        else:
            lam = draw(f"{base}.frequency.rate")

        base_claims = float(np.mean(lam))
        leak_rel = None
        if "leak_rate" in freq:
            leak = draw(f"{base}.frequency.leak_rate")
            ref = draw(f"{base}.frequency.reference_leak_rate")
            b = freq["leak_relativity_bounds"]
            leak_rel = np.clip(leak / ref, b["min"], b["max"])
            lam = lam * leak_rel

        modifier, detail = peril_modifier(params, insured, peril)
        lam = lam * modifier

        median = draw(f"{base}.severity.body.median")
        p90 = draw(f"{base}.severity.body.p90")
        mu, sigma = D.lognormal_params(median, p90)
        tail = spec["severity"].get("tail")
        if tail:
            t_prob = draw(f"{base}.severity.tail.prob")
            t_alpha = draw(f"{base}.severity.tail.alpha")

        rng = stream("simulate", peril)
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
        if illustrate:
            claims_log.append((peril, year_idx, layer))
        by_peril[peril] = {
            "expected_claims": float(lam.mean()),
            "expected_loss": float(annual.mean()),
            "modifier": modifier,
            "factors": detail,
            "base_claims_per_year": base_claims,        # before red-team leak and rating factors
            "parameters": {k: v for k, v in used.items() if k.startswith(base + ".")},
        }
        for key in ("p_error", "leak_rate"):
            if isinstance(freq.get(key), dict) and freq[key].get("credibility"):
                by_peril[peril]["credibility"] = freq[key]["credibility"]
        if leak_rel is not None:
            by_peril[peril]["red_team_leak_relativity"] = float(leak_rel.mean())

    # Systemic: correlated event at the insured's model provider.
    ev_p = draw("systemic.annual_prob")
    share = draw("systemic.share_affected")
    loss_share = draw("systemic.loss_share_of_limit")
    o = np.repeat(np.arange(n_outer), n_inner)
    rng = stream("simulate", "systemic")
    hit = (rng.random(n_years) < ev_p[o]) & (rng.random(n_years) < share[o])
    systemic = np.where(hit, loss_share[o] * agg, 0.0)

    total = np.minimum(idio + systemic, agg)
    illustration = (_illustration(claims_log, systemic, total, agg, n_inner, illustrate, stream)
                    if illustrate and n_outer > 1 else None)
    idio_c, sys_c = np.minimum(idio, agg), np.minimum(systemic, agg)

    pr = params["pricing"]
    expected_loss = float(total.mean())

    per_outer = total.reshape(n_outer, n_inner).mean(axis=1)
    el_pct = float(np.percentile(per_outer, pr["uncertainty_load"]["percentile"]))
    unc_load = 0.0 if n_outer == 1 else max(0.0, el_pct - expected_loss)

    cap = pr["capital"]
    tail_fn = _tvar if cap["tail_metric"] == "tvar" else (lambda x, q: float(np.quantile(x, q)))
    conc = cap["provider_concentration"]
    conc_mult = conc.get(insured.get("provider"), np.mean(list(conc.values()))) / np.mean(list(conc.values()))
    idio_tail, sys_tail = tail_fn(idio_c, cap["level"]), tail_fn(sys_c, cap["level"])
    idio_capital = (1 / np.sqrt(cap["portfolio_size"])) * max(0.0, idio_tail - idio_c.mean())
    sys_capital = conc_mult * max(0.0, sys_tail - sys_c.mean())
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
        "illustration": illustration,
        "trace": {
            "loss_histogram": _histogram(total, agg),
            "simulation": {"seed": seed, "n_outer": n_outer, "n_inner": n_inner, "n_years": n_years},
            "policy": {"deductible": ded, "limit_per_claim": per_claim, "aggregate_limit": agg},
            "systemic_parameters": {k: v for k, v in used.items() if k.startswith("systemic.")},
            "expected_idiosyncratic_loss": float(idio_c.mean()),
            "uncertainty": {"percentile": pr["uncertainty_load"]["percentile"],
                            "expected_loss_at_percentile": el_pct if n_outer > 1 else expected_loss},
            "capital": {"tail_metric": cap["tail_metric"], "level": cap["level"],
                        "portfolio_size": cap["portfolio_size"], "cost_of_capital": cap["cost_of_capital"],
                        "idiosyncratic_tail": idio_tail, "idiosyncratic_mean": float(idio_c.mean()),
                        "systemic_tail": sys_tail, "systemic_mean": float(sys_c.mean()),
                        "provider": insured.get("provider"),
                        "provider_share": conc.get(insured.get("provider")),
                        "average_provider_share": float(np.mean(list(conc.values())))},
            "expenses": dict(ex),
        },
    }
