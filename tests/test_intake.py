import copy
import json

import pytest
import yaml

from agents import intake
from engine.model import load_params, price

FAST = dict(n_outer=200, n_inner=50)
SUBMISSION = "agents/submissions/northwind"

# What a correct extraction of the Northwind submission looks like.
NORTHWIND_PROFILE = {
    "company_name": "Northwind Goods, Inc.",
    "system_name": "ShopBot",
    "summary": "Customer-service chatbot for an online home-goods retailer.",
    "use_case": {"category": "customer_service_chatbot", "description": "Orders, returns, shipping.",
                 "criticality": "medium", "evidence": "01_application.md: answers questions about orders..."},
    "autonomy": {"level": "human_review_sampled", "evidence": "02_architecture.md: reviews a random 5% sample"},
    "data_sensitivity": {"level": "personal", "evidence": "02_architecture.md: name, email, address, last four"},
    "jurisdictions": {"regions": ["us_high_reg", "us_low_reg", "other_non_us_eu"],
                      "details": "California, Oregon, Texas, Washington; Canada",
                      "evidence": "01_application.md: largest states..."},
    "customer_facing": {"level": "public", "evidence": "01_application.md: retail customers"},
    "governance": {"level": "adequate", "evidence": "02_architecture.md: runbook, no NIST alignment"},
    "vendor_indemnity": {"level": "none", "evidence": "02_architecture.md: no IP indemnity clause"},
    "model_provider": {"name": "Acme AI (Acme Large)", "hosting": "vendor_api",
                       "evidence": "02_architecture.md: Acme AI's hosted LLM API"},
    "perils": [{"peril": "wrong_information", "rationale": "Wrong policy answers."},
               {"peril": "unauthorized_commitment", "rationale": "Refund promises."},
               {"peril": "data_leakage", "rationale": "Order lookup exposes personal data."},
               {"peril": "prompt_injection", "rationale": "Pasted text reaches the bot."},
               {"peril": "ip_infringement", "rationale": "Public text output."}],
    "exposure": {"outputs_per_year": 2_000_000, "evidence": "01_application.md: roughly 2 million responses"},
    "requested_policy": {"limit_per_claim": 1_000_000, "aggregate_limit": 2_000_000, "deductible": 10_000},
    "test_endpoint": "http://127.0.0.1:8765/chat",
    "open_questions": ["Is the order lookup gated on email verification?"],
}


@pytest.fixture(scope="module")
def params():
    return load_params()


def _walk_objects(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for v in node.values():
            yield from _walk_objects(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_objects(v)


def test_schema_is_strict(params):
    """Structured outputs need every object closed and every property required."""
    schema = intake.profile_schema(params)
    for obj in _walk_objects(schema):
        assert obj.get("additionalProperties") is False
        assert set(obj["required"]) == set(obj["properties"])


def test_schema_enums_track_params(params):
    schema = intake.profile_schema(params)
    autonomy = schema["properties"]["autonomy"]["properties"]["level"]["enum"]
    assert set(params["rating_factors"]["factors"]["autonomy"]["levels"]) < set(autonomy)
    assert intake.UNKNOWN in autonomy
    perils = schema["properties"]["perils"]["items"]["properties"]["peril"]["enum"]
    assert perils == list(params["priors"]["perils"])


def test_profile_maps_to_priceable_insured(params):
    insured, notes = intake.to_insured(NORTHWIND_PROFILE, params, "northwind", intake.load_aliases())
    assert insured["id"] == "northwind"
    assert insured["provider"] == "provider_a"
    vol = {"outputs_per_year": 2_000_000}
    assert insured["perils"] == {"wrong_information": vol, "unauthorized_commitment": vol, "data_leakage": vol,
                                 "prompt_injection": vol, "ip_infringement": {}}
    assert insured["plan"] == "professional" and insured["policy"]["aggregate_limit"] == 2_000_000
    assert insured["factors"]["jurisdiction"] == "us_high_reg"      # strictest region wins
    assert "red_team" not in insured                                 # never invented by intake
    assert any("outside the US and EU" in n for n in notes)
    r = price(insured, params, **FAST)
    assert r["gross_premium"] > r["expected_loss"] > 0


def test_unknowns_are_left_out_and_flagged(params):
    p = copy.deepcopy(NORTHWIND_PROFILE)
    p["governance"]["level"] = "unknown"
    p["model_provider"] = {"name": "unknown", "hosting": "unknown", "evidence": ""}
    insured, notes = intake.to_insured(p, params)
    assert "governance" not in insured["factors"]
    assert "provider" not in insured
    assert any("governance" in n for n in notes)
    assert any("not mapped" in n for n in notes)
    price(insured, params, **FAST)   # still priceable with neutral factors


def test_missing_volume_uses_flagged_default(params):
    p = copy.deepcopy(NORTHWIND_PROFILE)
    p["exposure"]["outputs_per_year"] = 0
    insured, notes = intake.to_insured(p, params)
    assert insured["perils"]["wrong_information"] == {"outputs_per_year": intake.DEFAULT_OUTPUTS_PER_YEAR}
    assert any("volume not stated" in n for n in notes)


def test_self_hosted_maps_to_bucket(params):
    p = copy.deepcopy(NORTHWIND_PROFILE)
    p["model_provider"] = {"name": "Llama on our GPUs", "hosting": "self_hosted_open_model", "evidence": ""}
    insured, _ = intake.to_insured(p, params)
    assert insured["provider"] == "self_hosted_open_model"


def test_default_policy_when_not_requested(params):
    p = copy.deepcopy(NORTHWIND_PROFILE)
    p["requested_policy"] = {"limit_per_claim": 0, "aggregate_limit": 0, "deductible": 0}
    insured, notes = intake.to_insured(p, params)
    assert insured["plan"] == params["plans"]["default_plan"]
    assert insured["policy"] == params["plans"]["plans"][insured["plan"]]["policy"]


def test_requested_limits_choose_smallest_fitting_plan(params):
    for agg, plan in ((400_000, "essential"), (2_000_000, "professional"), (4_000_000, "enterprise"),
                      (50_000_000, "enterprise")):
        p = copy.deepcopy(NORTHWIND_PROFILE)
        p["requested_policy"]["aggregate_limit"] = agg
        assert intake.to_insured(p, params)[0]["plan"] == plan


def test_documents_become_labeled_blocks(tmp_path):
    (tmp_path / "a.md").write_text("hello")
    (tmp_path / "b.pdf").write_bytes(b"%PDF-1.4 fake")
    blocks = intake.content_blocks(intake.collect_documents([str(tmp_path)]), "desc")
    assert blocks[0]["text"].startswith("<company_description>")
    assert '<document name="a.md">' in blocks[1]["text"]
    assert blocks[2]["type"] == "document" and blocks[2]["source"]["media_type"] == "application/pdf"


def test_run_end_to_end_with_stubbed_llm(monkeypatch, tmp_path, params):
    calls = {}

    def fake_structured(system, user, schema, **kw):
        calls["system"], calls["user"], calls["schema"] = system, user, schema
        return copy.deepcopy(NORTHWIND_PROFILE)

    monkeypatch.setattr(intake, "llm_available", lambda: True)
    monkeypatch.setattr(intake.llm, "structured", fake_structured)
    r = intake.run([SUBMISSION], "northwind", out_dir=str(tmp_path))

    sent = json.dumps(calls["user"])
    assert "02_architecture.md" in sent and "Acme AI" in sent
    assert "use_case_criticality" in calls["system"]
    saved = yaml.safe_load((tmp_path / "insured.yaml").read_text())
    assert saved == r["insured"]
    assert json.loads((tmp_path / "profile.json").read_text())["system_name"] == "ShopBot"


def test_offline_intake_fails_clearly(monkeypatch):
    monkeypatch.setattr(intake, "llm_available", lambda: False)
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
        intake.run([SUBMISSION])
