"""Closed-form checks: the Monte Carlo engine against exact formulas.

With every parameter fixed (plain numbers) the expected annual loss has a
closed form, so the simulation must match it to within sampling error."""
import copy
import math

import pytest
from scipy import stats

from engine.model import load_params, price
from engine import distributions as D


def _lev(u, mu, sigma):
    """Limited expected value E[min(X, u)] for X ~ Lognormal(mu, sigma)."""
    if u <= 0:
        return 0.0
    z = (math.log(u) - mu) / sigma
    return math.exp(mu + sigma ** 2 / 2) * stats.norm.cdf(z - sigma) + u * (1 - stats.norm.cdf(z))


def _fixed_params(rate, median, p90):
    p = load_params()
    p = copy.deepcopy(p)
    p["priors"]["perils"]["unsafe_advice"] = {
        "description": "test", "frequency": {"mode": "annual_rate", "rate": rate},
        "severity": {"body": {"dist": "lognormal", "median": median, "p90": p90}}}
    for k in ("annual_prob", "share_affected", "loss_share_of_limit"):
        p["priors"]["systemic"][k] = 0.0
    return p


def _insured(ded, per_claim, agg):
    return {"id": "t", "perils": {"unsafe_advice": {}}, "factors": {},
            "policy": {"deductible": ded, "limit_per_claim": per_claim, "aggregate_limit": agg}}


@pytest.mark.parametrize("ded,per_claim", [(0, 1e12), (10_000, 1e12), (10_000, 100_000)])
def test_expected_loss_matches_closed_form(ded, per_claim):
    rate, median, p90 = 2.0, 20_000.0, 120_000.0
    p = _fixed_params(rate, median, p90)
    mu, sigma = D.lognormal_params(median, p90)
    per_claim_paid = _lev(ded + per_claim, mu, sigma) - _lev(ded, mu, sigma)
    exact = rate * per_claim_paid                         # aggregate limit set far away
    r = price(_insured(ded, per_claim, 1e15), p)
    assert r["expected_loss"] == pytest.approx(exact, rel=0.02)
    assert r["by_peril"]["unsafe_advice"]["expected_claims"] == pytest.approx(rate)


def test_gross_up_and_capital_arithmetic():
    p = _fixed_params(1.0, 10_000.0, 50_000.0)
    r = price(_insured(0, 1e9, 1e9), p)
    ex = p["pricing"]["expenses"]
    tech = r["expected_loss"] + r["uncertainty_load"] + r["capital_load"] + ex["fixed_per_policy"]
    assert r["technical_premium"] == pytest.approx(tech)
    assert r["gross_premium"] == pytest.approx(tech / (1 - ex["variable_ratio"] - ex["profit_margin"]))
    # Nothing is uncertain: after removing process noise only estimator noise is left
    # (heavy-tailed sample variances), a few percent of EL. Before the correction
    # the load charged pure luck (~9% of EL here).
    assert r["uncertainty_load"] < 0.04 * r["expected_loss"]
    assert r["expected_systemic_loss"] == 0.0


def test_aggregate_limit_caps_every_year():
    p = _fixed_params(20.0, 50_000.0, 300_000.0)
    r = price(_insured(0, 1e9, 500_000), p)
    assert r["loss_percentiles"]["99.5"] <= 500_000 + 1e-6
    assert r["expected_loss"] < 20 * 50_000 * 3


def test_uncertainty_load_reflects_parameter_uncertainty_not_luck():
    p = _fixed_params(1.0, 10_000.0, 50_000.0)
    fixed = price(_insured(0, 1e9, 1e9), p)
    p2 = copy.deepcopy(p)
    p2["priors"]["perils"]["unsafe_advice"]["frequency"]["rate"] = {"dist": "uniform", "min": 0.5, "max": 1.5}
    unc = price(_insured(0, 1e9, 1e9), p2)
    # Rate ~ U(0.5, 1.5): EL_j = rate_j × E[X], so σ_param = E[X] / √12 and U = 0.674 σ_param.
    ex = math.exp(sum(D.lognormal_params(10_000.0, 50_000.0)[i] * c for i, c in ((0, 1),)) +
                  D.lognormal_params(10_000.0, 50_000.0)[1] ** 2 / 2)
    expected_u = stats.norm.ppf(0.75) * ex / math.sqrt(12)
    assert unc["uncertainty_load"] == pytest.approx(expected_u, rel=0.15)
    assert unc["uncertainty_load"] > 5 * fixed["uncertainty_load"]
