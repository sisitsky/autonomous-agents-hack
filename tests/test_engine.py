import copy

import numpy as np
import pytest

from engine import distributions as D
from engine.cli import load_insured
from engine.guardrails import check_bounds, check_changes
from engine.model import load_params, peril_modifier, price
from engine.tornado import tornado

FAST = dict(n_outer=300, n_inner=100)


@pytest.fixture(scope="module")
def params():
    return load_params()


@pytest.fixture(scope="module")
def shopbot():
    return load_insured("shopbot")


def test_pert_central_and_bounds():
    spec = {"dist": "pert", "min": 1, "mode": 2, "max": 6}
    assert 1 < D.central(spec) < 6
    x = D.sample(spec, np.random.default_rng(0), 5000)
    assert x.min() >= 1 and x.max() <= 6


def test_lognormal_matches_percentiles():
    spec = {"dist": "lognormal", "median": 40000, "p90": 250000}
    assert D.quantile(spec, 0.5) == pytest.approx(40000, rel=1e-6)
    assert D.quantile(spec, 0.9) == pytest.approx(250000, rel=1e-6)


def test_premium_build_up_is_consistent(params, shopbot):
    r = price(shopbot, params, **FAST)
    ex = params["pricing"]["expenses"]
    technical = r["expected_loss"] + r["uncertainty_load"] + r["capital_load"] + ex["fixed_per_policy"]
    assert r["technical_premium"] == pytest.approx(technical)
    assert r["gross_premium"] == pytest.approx(technical / (1 - ex["variable_ratio"] - ex["profit_margin"]))
    assert r["gross_premium"] > r["expected_loss"] > 0


def test_deterministic_with_seed(params, shopbot):
    a = price(shopbot, params, seed=7, **FAST)["gross_premium"]
    b = price(shopbot, params, seed=7, **FAST)["gross_premium"]
    assert a == b


def test_better_governance_lowers_price(params, shopbot):
    weak = copy.deepcopy(shopbot); weak["factors"]["governance"] = "weak"
    assert price(weak, params, **FAST)["gross_premium"] > price(shopbot, params, **FAST)["gross_premium"]


def test_more_red_team_failures_raises_price(params, shopbot):
    worse = copy.deepcopy(shopbot)
    worse["red_team"]["failures_by_type"] = {"wrong_information": 6, "unauthorized_commitment": 8,
                                             "data_leakage": 6, "prompt_injection": 6}
    assert price(worse, params, **FAST)["expected_loss"] > price(shopbot, params, **FAST)["expected_loss"]


def test_limits_respected(params, shopbot):
    r = price(shopbot, params, **FAST)
    assert r["loss_percentiles"]["99.5"] <= shopbot["policy"]["aggregate_limit"]


def test_modifier_is_capped(params, shopbot):
    extreme = copy.deepcopy(shopbot)
    extreme["factors"].update(use_case_criticality="critical", autonomy="autonomous_actions", governance="weak")
    mod, _ = peril_modifier(params, extreme, "wrong_information")
    assert mod == params["rating_factors"]["combined_modifier_bounds"]["max"]


def test_tornado_sorted_and_includes_severity(params, shopbot):
    t = tornado(shopbot, params, n_years=20000)
    swings = [b["swing"] for b in t["bars"]]
    assert swings == sorted(swings, reverse=True)
    assert any("severity.body.p90" in b["parameter"] for b in t["bars"])


def test_shipped_params_pass_bounds(params):
    assert check_bounds(params) == []


def test_guardrail_flags_large_change(params):
    base = {"book_premium": 100, "insureds": {"a": {"gross_premium": 100}}}
    cand = {"book_premium": 130, "insureds": {"a": {"gross_premium": 130}}}
    problems, _ = check_changes(base, cand, params)
    assert len(problems) == 2
