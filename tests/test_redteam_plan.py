"""Company-specific red-team plans: the right tests for the system, graded on its own policy."""
import copy
import re

from agents import redteam, redteam_plan
from tests.test_intake import NORTHWIND_PROFILE

HIRING = {
    "company_name": "Talentive", "system_name": "ScreenBot",
    "summary": "Ranks job applicants for recruiters.",
    "use_case": {"category": "hiring_or_hr_screening", "description": "Resume screening", "criticality": "high", "evidence": ""},
    "autonomy": {"level": "human_review_sampled", "evidence": ""},
    "data_sensitivity": {"level": "personal", "evidence": ""},
    "customer_facing": {"level": "internal", "evidence": ""},
    "capabilities": {"can_make_commitments": False, "commitment_types": [], "actions": [],
                     "personal_data_types": ["resumes", "contact details"], "verification_rule": "", "evidence": ""},
    "policies": [],
}


def _ids(plan):
    return {t["template_id"] for t in plan["templates"]}


def test_plan_follows_capabilities_and_policy():
    p = redteam_plan.build_plan(HIRING)
    ids = _ids(p)
    # No commitments and no written policy: those tests are skipped, with reasons, not guessed.
    assert not ids & {"pressure_exception", "guarantee_outcome", "discount_or_waiver", "false_prior_approval",
                      "policy_recall", "false_premise", "tool_hijack"}
    assert {"fabricated_specifics", "impersonation", "internal_secrets", "pasted_document", "role_override"} <= ids
    assert all(s["why"] for s in p["skipped"])
    assert p["ground_truth_source"].startswith("default")
    assert redteam_plan.ground_truth(HIRING) == redteam_plan.DEFAULT_GROUND_TRUTH


def test_policy_and_tools_turn_tests_on():
    prof = copy.deepcopy(HIRING)
    prof["capabilities"].update(can_make_commitments=True, commitment_types=["interview slots"], actions=["send email"])
    prof["policies"] = [{"rule": "Interviews can only be booked Monday to Thursday.", "source": "handbook.pdf"}]
    p = redteam_plan.build_plan(prof)
    assert {"pressure_exception", "policy_recall", "false_premise", "tool_hijack"} <= _ids(p)
    assert p["n_cases"] <= redteam_plan.MAX_CASES
    assert "Monday to Thursday" in redteam_plan.ground_truth(prof) and "handbook.pdf" in redteam_plan.ground_truth(prof)


def test_capabilities_inferred_without_intake_block():
    caps = redteam_plan.capabilities(NORTHWIND_PROFILE)       # older profile: no capabilities/policies fields
    assert caps["commitments"]["on"] and caps["personal_data"]["on"] and not caps["policy"]["on"]


def test_fallback_suite_is_valid_and_stable():
    a = redteam_plan.generate_suite(HIRING, use_llm=False)
    b = redteam_plan.generate_suite(HIRING, use_llm=False)
    assert a["id"] == b["id"] and a["id"].startswith("generated-") and a["generator"] == "template fallback"
    ids = [c["id"] for c in a["cases"]]
    assert len(ids) == len(set(ids)) == a["plan"]["n_cases"]
    for c in a["cases"]:
        assert c["category"] in a["categories"] and c["input"] and c["rubric"]
        assert "{company}" not in c["input"] and "{system}" not in c["input"]
        for pat in c["fail_patterns"]:
            re.compile(pat)
    other = redteam_plan.generate_suite(NORTHWIND_PROFILE, use_llm=False)
    assert other["id"] != a["id"]


def test_generated_cases_from_llm_are_used(monkeypatch):
    def fake(cat, items, profile, gt):
        return [{"template_id": t["id"], "input": f"custom {t['id']} {i}", "rubric": "r",
                 "fail_patterns": ["ok", "([bad"]} for t, n in items for i in range(n)]
    monkeypatch.setattr(redteam_plan, "_generate_category", fake)
    s = redteam_plan.generate_suite(HIRING, use_llm=True)
    assert all(c["source"] == "generated" and c["input"].startswith("custom") for c in s["cases"])
    assert all(c["fail_patterns"] == ["ok"] for c in s["cases"])          # invalid regex dropped


def test_evaluate_runs_a_generated_suite():
    calls = []
    transport = lambda m: (calls.append(m), {"reply": "Sorry, I can't help with that.", "latency_s": 0.0})[1]
    ev = redteam.evaluate({"perils": {}}, HIRING, transport, "stub", scorer="patterns", generate=True)
    s = ev["summary"]
    assert s["suite_id"].startswith("generated-")
    assert s["tested"] + s["signal_tested"] == len(calls) == s["plan"]["n_cases"]
    assert s["signals"]["bias_discrimination"]["tested"] > 0          # a screener gets bias tests
    assert s["failures"] == 0 and ev["insured"]["red_team"]["tested"] == s["tested"]
    assert ev["insured"]["red_team"]["signals"] == s["signals"]


def test_general_model_gets_every_category():
    prof = copy.deepcopy(HIRING)
    prof["use_case"]["category"] = "foundation_model_or_general_assistant"
    p = redteam_plan.build_plan(prof)
    cats = {t["category"] for t in p["templates"]}
    assert {"scope_violation", "unsafe_advice", "harmful_content", "bias", "ip",
            "prompt_injection", "data_leakage", "policy_facts"} <= cats
    assert p["n_cases"] <= redteam_plan.MAX_CASES and all(t["count"] >= 1 for t in p["templates"])
