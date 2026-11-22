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
from scipy import stats

from . import credibility as CRED
from . import distributions as D

PARAMS_DIR = Path(__file__).resolve().parent.parent / "params"


# ---------------------------------------------------------------- loading

def load_params(params_dir: Path | str = PARAMS_DIR) -> dict:
    params_dir = Path(params_dir)
    out = {}
    for name in ("priors", "rating_factors", "pricing", "guardrails", "plans"):
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

def red_team_posterior(params: dict, insured: dict) -> dict:
    """Dirichlet posterior over red-team outcomes, with a Jewell credibility prior.

    Outcomes are pass plus the failure types in priors.red_team.failure_types.
    The prior Dirichlet(A·μ) is estimated from the rest of the portfolio; the
    insured's own counts are added to it. With no red-team test the posterior is
    the prior (the portfolio average, with its full uncertainty).
    """
    rt_cfg = params["priors"]["red_team"]
    types = list(rt_cfg["failure_types"])
    rt = insured.get("red_team") or {}
    tested = int(rt.get("tested", 0))
    counts = rt.get("failures_by_type") or {}
    unknown = set(counts) - set(types)
    if unknown:
        raise ValueError(f"Unknown red-team failure types: {sorted(unknown)}")
    if any(int(v) < 0 for v in counts.values()) or sum(int(v) for v in counts.values()) > tested:
        raise ValueError(f"red_team needs 0 <= failures and sum(failures_by_type) <= tested: {rt}")
    if rt and rt.get("suite", "adversarial") != "adversarial":
        raise ValueError("Only adversarial red-team suites are supported (suite: adversarial)")
    return CRED.credibility(insured.get("id"), rt.get("suite_id", "customer_service"), tested, counts,
                            ["pass"] + types, params.get("experience") or [], rt_cfg["fallback_prior"])


def insured_priors(params: dict, insured: dict) -> dict:
    """Copy of priors with insured-specific evidence applied.

    Each red-team-measured peril gets p_fail, the marginal of the joint
    Dirichlet for its failure type (a Beta(α_k, α_0 − α_k)), and the
    adversarial-to-live ratio. The joint draw happens in price().
    """
    priors = copy.deepcopy(params["priors"])
    rt_perils = [p for p in insured["perils"] if priors["perils"][p]["frequency"]["mode"] == "red_team"]
    if not rt_perils:
        return priors
    post = red_team_posterior(params, insured)
    priors["red_team"]["posterior"] = post
    for p in rt_perils:
        freq = priors["perils"][p]["frequency"]
        k = freq["failure_type"]
        how = (f"Jewell credibility Z = {post['z']:.2f} (A = {post['A']:.1f}, portfolio share "
               f"{post['mu'][k]:.3f}, {post['r']} insureds)" if post["status"] != "insufficient_experience"
               else post["note"])
        freq["p_fail"] = {"dist": "dirichlet_component", "component": k, "alpha": post["alpha"][k],
                          "alpha0": post["alpha0"],
                          "source": f"Red-team {k.replace('_', ' ')}: {post['counts'][k]} of {post['n']} cases; {how}."}
        freq["adversarial_to_live"] = copy.deepcopy(priors["red_team"]["adversarial_to_live"])
    return priors


def apply_plan(insured: dict, params: dict, plan_id: str | None = None) -> dict:
    """Copy of the insured restricted to a coverage plan: only perils the plan
    covers, with the plan's limits and deductible."""
    plans = params["plans"]
    plan_id = plan_id or insured.get("plan") or plans["default_plan"]
    if plan_id not in plans["plans"]:
        raise ValueError(f"Unknown plan '{plan_id}'")
    plan = plans["plans"][plan_id]
    out = copy.deepcopy(insured)
    out["perils"] = {k: v for k, v in insured["perils"].items() if k in plan["covers"]}
    out["policy"] = dict(plan["policy"])
    out["plan"] = plan_id
    return out


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

def _draw(priors, path, rng, n, mode, overrides, joint=None):
    if overrides and path in overrides:
        return np.full(n, float(overrides[path]))
    spec = get_path(priors, path)
    if mode == "point":
        return np.full(n, D.central(spec))
    if D.is_dist(spec) and spec["dist"] == "dirichlet_component":
        return joint[spec["component"]]
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

    # Joint Dirichlet draw of red-team outcome shares (gamma construction with
    # inverse-transform uniforms, one named stream per outcome: CRN-friendly).
    joint = None
    post = priors.get("red_team", {}).get("posterior")
    if post and mode != "point":
        g = {k: D.gamma_ppf(stream("param", f"red_team.theta.{k}").random(n_outer), a)
             for k, a in post["alpha"].items()}
        tot = sum(g.values())
        joint = {k: v / tot for k, v in g.items()}

    def draw(path):
        v = _draw(priors, path, stream("param", path), n_outer, mode, overrides, joint)
        used[path] = float(v.mean())
        return v

    for peril, exposure in insured["perils"].items():
        base = f"perils.{peril}"
        spec = priors["perils"][peril]
        freq = spec["frequency"]

        if freq["mode"] == "red_team":
            volume = exposure[freq["exposure_field"]]
            lam = volume
            for p in ("p_fail", "adversarial_to_live", "p_harm_given_failure", "p_claim_given_harm"):
                lam = lam * draw(f"{base}.frequency.{p}")
        elif freq["mode"] == "annual_rate":
            lam = draw(f"{base}.frequency.rate")
        else:
            raise ValueError(f"Unknown frequency mode '{freq['mode']}' for {peril}")

        base_claims = float(np.mean(lam))

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
            "base_claims_per_year": base_claims,        # before rating factors
            "parameters": {k: v for k, v in used.items() if k.startswith(base + ".")},
        }
        if freq["mode"] == "red_team":
            by_peril[peril]["failure_type"] = freq["failure_type"]

    # Systemic: correlated event at the insured's model provider.
    ev_p = draw("systemic.annual_prob")
    share = draw("systemic.share_affected")
    loss_share = draw("systemic.loss_share_of_limit")
    o = np.repeat(np.arange(n_outer), n_inner)
    rng = stream("simulate", "systemic")
    event = rng.random(n_years) < ev_p[o]                  # provider-wide event this year
    hit = event & (rng.random(n_years) < share[o])          # ...and it reaches this policy
    systemic = np.where(hit, loss_share[o] * agg, 0.0)
    # For capital, allocate the provider-level event to each policy at its expected
    # share (share x loss per hit): the hit/miss lottery across the provider's book
    # diversifies; the event itself does not.
    systemic_alloc = np.where(event, share[o] * loss_share[o] * agg, 0.0)

    total = np.minimum(idio + systemic, agg)
    illustration = (_illustration(claims_log, systemic, total, agg, n_inner, illustrate, stream)
                    if illustrate and n_outer > 1 else None)
    idio_c, sys_c = np.minimum(idio, agg), np.minimum(systemic, agg)

    pr = params["pricing"]
    expected_loss = float(total.mean())

    # Parameter-uncertainty load. Each parameter draw's average over n_inner years
    # mixes parameter uncertainty with process noise; remove the noise with the
    # variance decomposition Var(EL_j) = σ²_param + E[s²_j] / n_inner, then charge
    # the gap to the chosen percentile of a normal with that σ_param.
    blocks = total.reshape(n_outer, n_inner)
    per_outer = blocks.mean(axis=1)
    pctl = pr["uncertainty_load"]["percentile"]
    if n_outer > 1:
        var_means = float(per_outer.var(ddof=1))
        process_var = float(blocks.var(axis=1, ddof=1).mean()) / n_inner
        param_sd = float(np.sqrt(max(var_means - process_var, 0.0)))
        z_p = float(stats.norm.ppf(pctl / 100))
        unc_load = max(0.0, z_p * param_sd)
    else:
        var_means = process_var = param_sd = z_p = 0.0
        unc_load = 0.0
    el_pct = expected_loss + unc_load

    cap = pr["capital"]
    tail_fn = _tvar if cap["tail_metric"] == "tvar" else (lambda x, q: float(np.quantile(x, q)))
    conc = cap["provider_concentration"]
    conc_mult = conc.get(insured.get("provider"), np.mean(list(conc.values()))) / np.mean(list(conc.values()))
    idio_tail, sys_tail = tail_fn(idio_c, cap["level"]), tail_fn(systemic_alloc, cap["level"])
    idio_capital = (1 / np.sqrt(cap["portfolio_size"])) * max(0.0, idio_tail - idio_c.mean())
    sys_capital = conc_mult * max(0.0, sys_tail - systemic_alloc.mean())
    capital = idio_capital + sys_capital
    capital_load = cap["cost_of_capital"] * capital

    ex = pr["expenses"]
    technical = expected_loss + unc_load + capital_load + ex["fixed_per_policy"]
    formula_gross = technical / (1 - ex["variable_ratio"] - ex["profit_margin"])
    plan = (params.get("plans") or {}).get("plans", {}).get(insured.get("plan")) if insured.get("plan") else None
    minimum = float(plan.get("minimum_premium", 0)) if plan else 0.0
    gross = max(formula_gross, minimum)

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
        "formula_premium": formula_gross,
        "minimum_premium": minimum,
        "minimum_applied": bool(gross > formula_gross),
        "gross_premium": gross,
        "rate_on_line": gross / agg,
        "loss_percentiles": {str(q): float(np.quantile(total, q / 100))
                             for q in (50, 75, 90, 95, 99, 99.5)},
        "prob_any_loss": float((total > 0).mean()),
        "illustration": illustration,
        "red_team_posterior": post,
        "plan": insured.get("plan"),
        "trace": {
            "loss_histogram": _histogram(total, agg),
            "simulation": {"seed": seed, "n_outer": n_outer, "n_inner": n_inner, "n_years": n_years},
            "policy": {"deductible": ded, "limit_per_claim": per_claim, "aggregate_limit": agg},
            "systemic_parameters": {k: v for k, v in used.items() if k.startswith("systemic.")},
            "expected_idiosyncratic_loss": float(idio_c.mean()),
            "uncertainty": {"percentile": pctl, "expected_loss_at_percentile": el_pct,
                            "variance_of_draw_means": var_means, "process_variance_share": process_var,
                            "parameter_sd": param_sd, "z": z_p},
            "capital": {"tail_metric": cap["tail_metric"], "level": cap["level"],
                        "portfolio_size": cap["portfolio_size"], "cost_of_capital": cap["cost_of_capital"],
                        "idiosyncratic_tail": idio_tail, "idiosyncratic_mean": float(idio_c.mean()),
                        "systemic_tail": sys_tail, "systemic_mean": float(systemic_alloc.mean()),
                        "provider": insured.get("provider"),
                        "provider_share": conc.get(insured.get("provider")),
                        "average_provider_share": float(np.mean(list(conc.values())))},
            "expenses": dict(ex),
        },
    }
