import copy

import numpy as np
import pytest

from engine import credibility as C
from engine.cli import load_insured
from engine.model import insured_priors, load_params, price

FAST = dict(n_outer=400, n_inner=100)
TYPES = ["wrong_information", "unauthorized_commitment", "data_leakage", "prompt_injection"]
OUT = ["pass"] + TYPES
FALLBACK = {"concentration": 10, "mean": {"pass": 0.8, **{t: 0.05 for t in TYPES}}}


def _exp(rows, suite="s"):
    """rows: (id, n, (wi, uc, dl, pi))"""
    out = []
    for i, n, c in rows:
        o = dict(zip(TYPES, c)); o["pass"] = n - sum(c)
        out.append({"insured_id": i, "suite_id": suite, "tested": n, "outcomes": o})
    return out


def test_buhlmann_straub_matches_hand_calculation():
    ns, xs = [10, 20, 10], [0.1, 0.4, 0.6]
    e = C.buhlmann_straub(ns, xs)
    m = 40
    mu = sum(a * b for a, b in zip(ns, xs)) / m
    epv = sum(a * b * (1 - b) for a, b in zip(ns, xs)) / (9 + 19 + 9)
    vhm = (sum(a * (b - mu) ** 2 for a, b in zip(ns, xs)) - 2 * epv) / (m - sum(a * a for a in ns) / m)
    assert (e["mu"], e["epv"], e["vhm"]) == pytest.approx((mu, epv, vhm))


def test_jewell_pools_outcomes_into_one_concentration():
    exp = _exp([(f"i{j}", 40, c) for j, c in enumerate(
        [(1, 1, 0, 0), (2, 2, 2, 1), (4, 6, 5, 4), (0, 1, 0, 0), (5, 7, 6, 5), (3, 2, 2, 3)])])
    j = C.jewell(exp, "s", OUT)
    assert j["status"] == "ok" and j["A"] > 0
    assert sum(j["mu"].values()) == pytest.approx(1.0)
    epv = sum(v["epv"] for v in j["per_outcome"].values())
    vhm = sum(v["vhm"] for v in j["per_outcome"].values())
    assert j["A"] == pytest.approx(epv / vhm)


def test_exact_credibility_posterior_mean():
    """Jewell: Dirichlet posterior mean == Z·X + (1 − Z)·μ for every outcome."""
    exp = _exp([(f"i{j}", 40, c) for j, c in enumerate(
        [(1, 1, 0, 0), (2, 2, 2, 1), (4, 6, 5, 4), (0, 1, 0, 0), (5, 7, 6, 5), (3, 2, 2, 3)])])
    c = C.credibility("x", "s", 40, dict(zip(TYPES, (5, 7, 5, 7))), OUT, exp, FALLBACK)
    for k in OUT:
        assert c["alpha"][k] / c["alpha0"] == pytest.approx(c["estimate"][k])
        assert c["estimate"][k] == pytest.approx(c["z"] * c["observed"][k] + (1 - c["z"]) * c["mu"][k])
    assert sum(c["estimate"].values()) == pytest.approx(1.0)
    assert c["z"] == pytest.approx(40 / (40 + c["A"]))


def test_z_grows_with_tests():
    exp = _exp([(f"i{j}", 40, c) for j, c in enumerate(
        [(1, 1, 0, 0), (2, 2, 2, 1), (4, 6, 5, 4), (0, 1, 0, 0), (5, 7, 6, 5), (3, 2, 2, 3)])])
    small = C.credibility("x", "s", 10, {"wrong_information": 5}, OUT, exp, FALLBACK)
    big = C.credibility("x", "s", 1000, {"wrong_information": 500}, OUT, exp, FALLBACK)
    assert 0 < small["z"] < big["z"] < 1


def test_leave_one_out_and_fallback():
    exp = _exp([("a", 40, (1, 1, 1, 1)), ("x", 40, (9, 9, 9, 9))])
    assert len(C.groups(exp, "s", exclude="x")) == 1
    c = C.credibility("x", "s", 40, {}, OUT, exp, FALLBACK)
    assert c["status"] == "insufficient_experience" and c["A"] == 10


def test_failures_cannot_exceed_tests():
    with pytest.raises(ValueError):
        C.credibility("x", "s", 3, {"wrong_information": 4}, OUT, [], FALLBACK)


def test_joint_dirichlet_draws(params=None):
    """The engine's joint draw: shares sum to one and match the posterior means."""
    params = load_params()
    sb = copy.deepcopy(load_insured("shopbot"))
    r = price(sb, params, **FAST)
    post = r["red_team_posterior"]
    for peril, d in r["by_peril"].items():
        k = d["failure_type"]
        drawn = d["parameters"][f"perils.{peril}.frequency.p_fail"]
        assert drawn == pytest.approx(post["alpha"][k] / post["alpha0"], rel=0.08)


def test_more_failures_of_a_type_raise_that_peril():
    params = load_params()
    sb = copy.deepcopy(load_insured("shopbot"))
    worse = copy.deepcopy(sb); worse["red_team"]["failures_by_type"]["data_leakage"] = 8
    a, b = price(sb, params, **FAST)["by_peril"], price(worse, params, **FAST)["by_peril"]
    assert b["data_leakage"]["expected_claims"] > 2 * a["data_leakage"]["expected_claims"]
    # With n fixed, another type's posterior mean depends only on its own count.
    assert b["wrong_information"]["expected_claims"] == pytest.approx(a["wrong_information"]["expected_claims"], rel=0.02)


def test_dirichlet_components_are_negatively_correlated():
    """Shares sum to one, so draws of different failure types co-vary negatively."""
    from engine import distributions as D
    rng = np.random.default_rng(0)
    alpha = {"pass": 20.0, "a": 6.0, "b": 6.0}
    g = {k: D.gamma_ppf(rng.random(20000), a) for k, a in alpha.items()}
    tot = sum(g.values()); th = {k: v / tot for k, v in g.items()}
    assert np.allclose(sum(th.values()), 1.0)
    assert np.corrcoef(th["a"], th["b"])[0, 1] < 0
    assert th["a"].mean() == pytest.approx(6 / 32, rel=0.02)


def test_record_replaces_and_updates_collective(isolated_experience):
    before = C.jewell(C.load_experience(), "customer_service", OUT)
    C.record("bad_bot", "customer_service", 40, dict(zip(TYPES, (10, 10, 10, 10))), "test")
    C.record("bad_bot", "customer_service", 40, dict(zip(TYPES, (9, 10, 10, 10))), "test")
    exp = C.load_experience()
    assert sum(e["insured_id"] == "bad_bot" for e in exp) == 1
    after = C.jewell(exp, "customer_service", OUT)
    assert after["r"] == before["r"] + 1 and after["mu"]["pass"] < before["mu"]["pass"]
