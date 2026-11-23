"""Submission memory: same documents -> same profile and same red-team tests."""
import copy
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from agents import intake, memory, redteam, redteam_plan
from api.index import app
from engine.cli import load_insured
from tests.test_intake import NORTHWIND_PROFILE
from tests.test_redteam_plan import HIRING

client = TestClient(app)


@pytest.fixture
def mem_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "MEM_DIR", tmp_path / "memory")
    return tmp_path / "memory"


def test_key_matches_browser_formula_and_ignores_order(tmp_path):
    a, b = tmp_path / "a.pdf", tmp_path / "b.txt"
    a.write_bytes(b"%PDF fake"); b.write_text("notes")
    ha, hb = memory.file_sha256(a), memory.file_sha256(b)
    assert ha == hashlib.sha256(b"%PDF fake").hexdigest()
    k = memory.doc_key([hb, ha])
    assert k == memory.doc_key([ha, hb]) == hashlib.sha256("\n".join(sorted([ha, hb])).encode()).hexdigest()
    with pytest.raises(ValueError):
        memory.doc_key(["not-a-hash"])


def test_pin_get_roundtrip_and_validation(mem_dir):
    suite = redteam_plan.generate_suite(HIRING, use_llm=False)
    rec = {"key": "a" * 64, "files": [{"name": "x.pdf", "sha256": "b" * 64}], "target": "claude",
           "profile": HIRING | {"use_case": HIRING["use_case"]}, "suite": {k: v for k, v in suite.items() if k != "_path"}}
    memory.pin(rec)
    got = memory.get("a" * 64)
    assert got["suite"]["id"] == suite["id"] and got["target"] == "claude" and got["saved_at"]
    assert memory.get("../../etc/passwd") is None and memory.get("c" * 64) is None
    for bad in ({"key": "zz"}, {"key": "a" * 64, "target": "nope"},
                {"key": "a" * 64, "suite": {"cases": [{"id": "x"}], "categories": {"c": ""}}}):
        with pytest.raises(ValueError):
            memory.check_record(bad)


def test_intake_reuses_remembered_profile_without_claude(monkeypatch):
    monkeypatch.setattr(intake, "llm_available", lambda: False)
    r1 = intake.analyze([], None, "a", profile=NORTHWIND_PROFILE)
    r2 = intake.analyze([], None, "a", profile=NORTHWIND_PROFILE)
    assert r1["insured"] == r2["insured"] and r1["profile"] == NORTHWIND_PROFILE


def test_remembered_suite_is_rerun_exactly():
    suite = redteam_plan.generate_suite(HIRING, use_llm=False)
    seen = []
    t = lambda m: (seen.append(m), {"reply": "No.", "latency_s": 0})[1]
    ev = redteam.evaluate({"perils": {}}, HIRING, t, "stub", scorer="patterns", suite=suite)
    assert seen == [c["input"] for c in suite["cases"]] and ev["summary"]["suite_id"] == suite["id"]


def test_api_memory_lookup_and_remembered_intake(mem_dir, monkeypatch):
    monkeypatch.delenv("DEMO_ACCESS_CODE", raising=False)
    assert client.post("/api/memory", json={"key": "d" * 64}).json() == {"found": False, "memory": None}
    memory.pin({"key": "d" * 64, "target": "claude", "profile": NORTHWIND_PROFILE})
    r = client.post("/api/memory", json={"key": "d" * 64}).json()
    assert r["found"] and r["memory"]["profile"]["system_name"] == "ShopBot"
    monkeypatch.setattr("api.index.llm_available", lambda: False)     # remembered profile needs no Claude call
    r = client.post("/api/intake", json={"documents": [], "insured_id": "m", "profile": NORTHWIND_PROFILE})
    assert r.status_code == 200 and r.json()["from_memory"] is True


def test_api_redteam_rejects_malformed_remembered_suite(monkeypatch):
    monkeypatch.delenv("DEMO_ACCESS_CODE", raising=False)
    monkeypatch.setattr("api.index.llm_available", lambda: True)
    r = client.post("/api/redteam", json={"insured": load_insured("shopbot"), "bot": "deterministic",
                                          "suite": {"cases": [{"id": "x"}], "categories": {"a": ""}}})
    assert r.status_code == 400


def test_claude_transport_logs_errors_not_raises(monkeypatch):
    import agents.target_bot.claude_proxy as cp
    monkeypatch.setattr(cp, "make_reply", lambda model, system: (lambda m: (_ for _ in ()).throw(RuntimeError("boom"))))
    assert "boom" in redteam.claude_transport()("hi")["error"]
    monkeypatch.setattr(cp, "make_reply", lambda model, system: (lambda m: "hello"))
    assert redteam.claude_transport()("hi")["reply"] == "hello"
