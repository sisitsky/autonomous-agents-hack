"""Shock events, deployment scale and red-team signals move the price of the right perils."""
import copy

import pytest

from agents import pricer, underwriter_chat
from engine.cli import load_insured
from engine.model import load_params, peril_modifier, price

FAST = dict(n_outer=200, n_inner=100)


@pytest.fixture(scope="module")
def params():
    return load_params()


@pytest.fixture
def agent(params):
    ins = copy.deepcopy(load_insured("shopbot"))
    ins["plan"] = "enterprise"; ins["policy"] = dict(params["plans"]["plans"]["enterprise"]["policy"])
    ins["perils"]["autonomous_action_harm"] = {}
    return ins


CRITICAL = {"title": "Agents accessed third-party production systems", "summary": "s", "severity": "critical",
            "perils": ["autonomous_action_harm", "data_leakage"], "evidence": "e", "event_date": "2026-07"}


def test_shock_multiplies_only_its_perils_after_the_cap(params, agent):
    before = {p: peril_modifier(params, agent, p)[0] for p in agent["perils"]}
    hit = copy.deepcopy(agent); hit["shock_events"] = [CRITICAL]
    sev = params["rating_factors"]["shock_events"]["severity"]["critical"]
    for p in agent["perils"]:
        m, d = peril_modifier(params, hit, p)
        if p in CRITICAL["perils"]:
            assert m == pytest.approx(before[p] * sev) and d["shock_events"] == sev
        else:
            assert m == before[p] and "shock_events" not in d
    two = copy.deepcopy(hit); two["shock_events"] = [CRITICAL, dict(CRITICAL, title="again")]
    cap = params["rating_factors"]["shock_events"]["max_total"]
    assert peril_modifier(params, two, "autonomous_action_harm")[1]["shock_events"] == cap


def test_shock_raises_premium_and_refers(params, agent):
    hit = copy.deepcopy(agent); hit["shock_events"] = [CRITICAL]
    assert price(hit, params, **FAST)["gross_premium"] > price(agent, params, **FAST)["gross_premium"] * 1.05
    d = pricer.underwriting_decision(hit, params)
    assert d["decision"] == "refer" and any("Shock event (critical)" in r for r in d["reasons"])
    bad = copy.deepcopy(agent); bad["shock_events"] = [dict(CRITICAL, severity="apocalyptic")]
    with pytest.raises(ValueError):
        peril_modifier(params, bad, "data_leakage")


def test_chat_records_unrelated_event_adds_peril_and_upgrades_plan(params):
    ins = copy.deepcopy(load_insured("shopbot"))          # professional plan, no autonomous_action_harm
    upd = {"factors": {}, "outputs_per_year": 0, "add_perils": [], "remove_perils": [], "provider": "unchanged",
           "adjustments": [], "shock_events": [CRITICAL]}
    new, changes = underwriter_chat.apply_updates(ins, upd, params)
    assert "autonomous_action_harm" in new["perils"] and new["plan"] == "enterprise"
    assert new["shock_events"][0]["severity"] == "critical"
    assert any(c.startswith("shock event (critical") for c in changes) and any(c.startswith("plan:") for c in changes)
    again, _ = underwriter_chat.apply_updates(new, upd, params)          # same event isn't double-counted
    assert len(again["shock_events"]) == 1


def test_deployment_scale_applies_after_cap_to_profile_perils(params, agent):
    big = copy.deepcopy(agent); big["factors"]["deployment_scale"] = "frontier"
    m, d = peril_modifier(params, big, "autonomous_action_harm")
    assert m == pytest.approx(peril_modifier(params, agent, "autonomous_action_harm")[0] * 8.0)
    assert peril_modifier(params, big, "wrong_information")[0] == peril_modifier(params, agent, "wrong_information")[0]


def test_red_team_signal_is_credibility_weighted(params, agent):
    cfg = params["rating_factors"]["red_team_signals"]
    clean = copy.deepcopy(agent); clean["red_team"]["signals"] = {"autonomous_action_harm": {"tested": 5, "failures": 0}}
    dirty = copy.deepcopy(agent); dirty["red_team"]["signals"] = {"autonomous_action_harm": {"tested": 5, "failures": 4}}
    r0 = peril_modifier(params, clean, "autonomous_action_harm")[1]["red_team_signal"]
    r1 = peril_modifier(params, dirty, "autonomous_action_harm")[1]["red_team_signal"]
    z = 5 / (5 + cfg["credibility_k"])
    assert r0 == pytest.approx(1 - z, abs=1e-4) and r1 == pytest.approx(min(1 + z * (0.8 / 0.1 - 1), cfg["bounds"]["max"]), abs=1e-4)
    assert "red_team_signal" not in peril_modifier(params, dirty, "wrong_information")[1]
