import copy

import pytest
from fastapi.testclient import TestClient

from agents import pricer, redteam
from api.index import app
from engine.cli import load_insured
from engine.model import load_params

client = TestClient(app)


def test_redteam_endpoint_requires_authorization_and_public_address(monkeypatch):
    monkeypatch.delenv("DEMO_ACCESS_CODE", raising=False)
    ins = load_insured("shopbot")
    r = client.post("/api/redteam", json={"insured": ins, "bot": "endpoint", "endpoint": "https://example.com/chat"})
    assert r.status_code in (400, 503) and ("authorized" in r.text or "API key" in r.text)
    monkeypatch.setattr("api.index.llm_available", lambda: True)
    for url in ("http://127.0.0.1:8765/chat", "http://localhost/chat", "http://10.0.0.5/chat",
                "http://169.254.169.254/latest", "file:///etc/passwd"):
        r = client.post("/api/redteam", json={"insured": ins, "bot": "endpoint", "endpoint": url, "authorized": True})
        assert r.status_code == 400, url


def test_reply_path_digging():
    data = {"choices": [{"message": {"content": "hi"}}], "reply": "x"}
    assert redteam._dig(data, "choices.0.message.content") == "hi"
    assert redteam._dig(data, "reply") == "x"
    assert redteam._dig(data, "choices.5.message") is None


def test_referral_decisions():
    params = load_params()
    sb = copy.deepcopy(load_insured("shopbot"))
    assert pricer.underwriting_decision(sb, params)["decision"] == "quote"
    bad = copy.deepcopy(sb); bad["red_team"]["failures_by_type"] = {"wrong_information": 6, "unauthorized_commitment": 10,
                                                                   "data_leakage": 5, "prompt_injection": 3}
    assert pricer.underwriting_decision(bad, params)["decision"] == "refer"
    none = copy.deepcopy(sb); none.pop("red_team")
    assert pricer.underwriting_decision(none, params)["decision"] == "indicative_untested"


def test_price_untested_insured_on_every_plan():
    sb = copy.deepcopy(load_insured("shopbot")); sb.pop("red_team")
    r = client.post("/api/price", json={"insured": sb, "tornado": False, "controls": False})
    assert r.status_code == 200
    plans = r.json()["calculation"]["plans"]
    assert all(v["gross_premium"] > 0 for v in plans.values())
