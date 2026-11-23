from fastapi.testclient import TestClient

from agents.growth import loop3
from api.index import app
from engine.model import load_params


def test_free_score_is_engine_only_and_monotone():
    c = TestClient(app)
    base = {"outputs_per_year": 500000, "factors": {"use_case_criticality": "medium", "autonomy": "human_review_sampled",
                                                    "data_sensitivity": "personal", "governance": "adequate"}}
    a = c.post("/api/score", json=base).json()
    worse = {**base, "factors": {**base["factors"], "governance": "weak", "autonomy": "autonomous_actions"}}
    b = c.post("/api/score", json=worse).json()
    assert 1 <= a["risk_score"] <= 100 and set(a["plans"]) == {"essential", "professional", "enterprise"}
    assert b["risk_score"] >= a["risk_score"] and b["plans"]["professional"]["premium"] > a["plans"]["professional"]["premium"]


def test_archetype_volume_is_clamped():
    a = {"use_case_criticality": "medium", "autonomy": "autonomous", "data_sensitivity": "personal",
         "jurisdiction": "us_low_reg", "customer_facing": "public", "outputs_per_year": 10**9,
         "perils": ["wrong_information", "data_leakage"]}
    s = loop3.score_archetype(a, load_params())
    assert a["outputs_per_year"] == loop3.VOLUME_RANGE[1] and 1 <= s["risk_score"] <= 100


def test_no_analytics_means_no_data_unless_simulated(monkeypatch):
    monkeypatch.setattr(loop3.egbe, "available", lambda: False)   # behave as if Egbe were not connected
    plan = {"cycle": 1, "campaigns": [{"segment_id": "x", "utm_campaign": "u", "budget_usd": 25.0}]}
    assert loop3.read_funnel(plan)["by_campaign"] == {}
    sim = loop3.read_funnel(plan, simulate=True)
    assert sim["simulated"] and sim["by_campaign"]["u"]["visits"] > 0


def test_growth_never_affects_price():
    assert load_params()["guardrails"]["growth"]["growth_data_may_affect_price"] is False
