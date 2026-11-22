import copy

import pytest

from engine import credibility as C
from engine import distributions as D
from engine.cli import load_insured
from engine.model import insured_priors, load_params, price

FAST = dict(n_outer=300, n_inner=100)


def _exp(rows, suite="s", measure="p_error"):
    return [{"insured_id": i, "suite_id": suite, "measure": measure, "tested": n, "failures": f} for i, n, f in rows]


def test_buhlmann_straub_matches_hand_calculation():
    gs = [{"insured_id": "a", "n": 10, "failures": 1}, {"insured_id": "b", "n": 20, "failures": 8},
          {"insured_id": "c", "n": 10, "failures": 6}]
    e = C.buhlmann_straub(gs)
    x = [0.1, 0.4, 0.6]; n = [10, 20, 10]; m = 40
    mu = sum(a * b for a, b in zip(n, x)) / m
    epv = sum(a * b * (1 - b) for a, b in zip(n, x)) / (9 + 19 + 9)
    vhm = (sum(a * (b - mu) ** 2 for a, b in zip(n, x)) - 2 * epv) / (m - sum(a * a for a in n) / m)
    assert e["mu"] == pytest.approx(mu) and e["epv"] == pytest.approx(epv) and e["vhm"] == pytest.approx(vhm)
    assert e["k"] == pytest.approx(epv / vhm)


def test_z_grows_with_tests_and_estimate_shrinks_to_mean():
    exp = _exp([(f"i{j}", 30, f) for j, f in enumerate([1, 3, 5, 8, 12, 15, 20])])
    small = C.credibility("x", "p_error", "s", 10, 6, exp)
    big = C.credibility("x", "p_error", "s", 1000, 600, exp)
    assert 0 < small["z"] < big["z"] < 1
    assert small["mu"] < small["estimate"] < small["observed"]       # pulled toward the portfolio
    assert abs(big["estimate"] - big["observed"]) < abs(small["estimate"] - small["observed"])


def test_leave_one_out():
    exp = _exp([(f"i{j}", 30, f) for j, f in enumerate([1, 3, 5, 8, 12])]) + _exp([("x", 30, 30)])
    with_self = C.groups(exp, "p_error", "s")
    without = C.groups(exp, "p_error", "s", exclude="x")
    assert len(with_self) == 6 and len(without) == 5


def test_insufficient_experience_falls_back_to_uniform():
    c = C.credibility("x", "p_error", "s", 31, 19, _exp([("a", 31, 3), ("b", 31, 9)]))
    assert c["status"] == "insufficient_experience" and (c["prior_alpha"], c["prior_beta"]) == (1.0, 1.0)


def test_posterior_mean_equals_buhlmann_estimate():
    params = load_params()
    sb = copy.deepcopy(load_insured("shopbot"))
    sb["red_team"] = {"tested": 31, "failures": 19, "suite": "adversarial", "suite_id": "customer_service",
                      "data_leak": {"tested": 9, "failures": 5}}
    pri = insured_priors(params, sb)
    for path in (("misinformation", "p_error"), ("privacy_regulatory", "leak_rate")):
        spec = pri["perils"][path[0]]["frequency"][path[1]]
        cred = spec["credibility"]
        assert cred["status"] == "ok" and cred["r"] >= C.MIN_RISKS
        mean = spec["alpha"] / (spec["alpha"] + spec["beta"])
        assert mean == pytest.approx(cred["estimate"])
        assert D.quantile(spec, 0.5) > 0


def test_credibility_reported_in_quote_and_benchmarks_untouched(isolated_experience):
    params = load_params()
    sb = copy.deepcopy(load_insured("shopbot"))
    sb["red_team"] = {"tested": 31, "failures": 19, "suite": "adversarial"}
    r = price(sb, params, **FAST)
    assert "credibility" in r["by_peril"]["misinformation"]
    assert "credibility" not in price(load_insured("shopbot"), params, **FAST)["by_peril"]["misinformation"]


def test_record_replaces_and_updates_collective(isolated_experience):
    before = C.credibility("new", "p_error", "customer_service", 31, 19, C.load_experience())
    C.record("bad_bot", "customer_service", {"p_error": {"tested": 31, "failures": 31}}, "test")
    C.record("bad_bot", "customer_service", {"p_error": {"tested": 31, "failures": 30}}, "test")
    exp = C.load_experience()
    assert sum(e["insured_id"] == "bad_bot" for e in exp) == 1                  # replaced, not appended
    after = C.credibility("new", "p_error", "customer_service", 31, 19, exp)
    assert after["r"] == before["r"] + 1 and after["mu"] > before["mu"]          # collective moved
