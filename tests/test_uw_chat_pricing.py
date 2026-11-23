"""Every change the underwriter chat claims reaches the price."""
import copy

from fastapi.testclient import TestClient

from agents import underwriter_chat
from api.index import app
from engine.cli import load_insured
from engine.model import load_params

client = TestClient(app)
NONE = {"factors": {}, "outputs_per_year": 0, "add_perils": [], "remove_perils": [], "provider": "unchanged",
        "adjustments": [], "shock_events": [], "answered_question_indexes": [0], "follow_up_questions": []}


def test_retry_when_reply_claims_a_change_but_fields_are_empty(monkeypatch):
    calls = []
    def fake(system, user, schema, **kw):
        calls.append(user)
        if len(calls) == 1:
            return dict(NONE, reply="Thanks, I'll factor this into the pricing.")
        return dict(NONE, reply="Raised data leakage for the disclosed gap.",
                    adjustments=[{"peril": "data_leakage", "multiplier": 1.3, "reason": "no verification on lookups"}])
    monkeypatch.setattr(underwriter_chat.llm, "structured", fake)
    r = underwriter_chat.chat(load_insured("shopbot"), {}, ["Is order lookup verified?"], [], "It isn't verified.")
    assert len(calls) == 2 and "previous_attempt" in calls[1]
    assert any("data leakage: ×1.3" in c for c in r["changes"])


def test_claim_without_change_is_flagged(monkeypatch):
    monkeypatch.setattr(underwriter_chat.llm, "structured",
                        lambda *a, **k: dict(NONE, reply="I've adjusted the risk accordingly."))
    r = underwriter_chat.chat(load_insured("shopbot"), {}, ["q"], [], "something")
    assert r["changes"] == [] and "No pricing change was applied" in r["reply"]


def test_adjustment_on_missing_peril_adds_it_and_credit_is_reported():
    params = load_params()
    ins = load_insured("shopbot")
    ign = []
    new, changes = underwriter_chat.apply_updates(ins, dict(NONE, adjustments=[
        {"peril": "unsafe_advice", "multiplier": 1.4, "reason": "gives dosage advice"},
        {"peril": "bias_discrimination", "multiplier": 0.8, "reason": "audited"}]), params, ign)
    assert "unsafe_advice" in new["perils"] and new["plan"] == "enterprise"
    assert "bias_discrimination" not in new["perils"] and any("nothing to lower" in x for x in ign)


def test_api_prices_on_the_plan_the_agent_moved_to(monkeypatch):
    monkeypatch.delenv("DEMO_ACCESS_CODE", raising=False)
    monkeypatch.setattr("api.index.llm_available", lambda: True)
    monkeypatch.setattr(underwriter_chat.llm, "structured", lambda *a, **k: dict(NONE, reply="Added.",
                        add_perils=["harmful_content"]))
    ins = load_insured("shopbot")
    r = client.post("/api/underwriter/chat", json={"insured": ins, "message": "it writes public reviews",
                                                    "plan": ins["plan"], "open_questions": ["q"]})
    assert r.status_code == 200, r.text
    calc = r.json()["calculation"]
    assert calc["insured"]["plan"] == "enterprise" and "harmful_content" in calc["perils"]
