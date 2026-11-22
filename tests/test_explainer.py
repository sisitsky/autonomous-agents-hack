import copy
import json
import shutil

import pytest
import yaml

from agents import charts, explainer, pricer
from engine.cli import load_insured


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("northwind")
    ins = copy.deepcopy(load_insured("shopbot"))
    ins["id"] = "northwind"
    ins["red_team"] = {"tested": 31, "failures": 19, "suite": "adversarial",
                       "data_leak": {"tested": 9, "failures": 5}}
    (d / "insured.yaml").write_text("# review: Jurisdiction rated as the strictest.\n" + yaml.safe_dump(ins))
    (d / "profile.json").write_text(json.dumps({"open_questions": ["Is email verification enforced in code?"]}))
    pricer.run(str(d))
    return d


@pytest.fixture(scope="module")
def offline(run_dir):
    return explainer.run(str(run_dir), offline=True)


def test_offline_report_written(offline, run_dir):
    out = run_dir / "report"
    for f in ("report.html", "report.md", "loss_distribution.svg", "tornado.svg", "controls.svg", "narrative.json"):
        assert (out / f).exists(), f
    html = (out / "report.html").read_text()
    assert "not an offer of insurance" in html
    assert html.count("<svg") == 3
    assert "Is email verification enforced in code?" in html
    assert "Jurisdiction rated as the strictest." in html
    assert offline["check"]["source"] == "template"


def test_template_numbers_all_verified(offline):
    assert explainer.check_narrative(offline["narrative"], offline["facts"]) == {}


def test_fact_sheet_matches_archive(offline, run_dir):
    calc = json.loads((run_dir / "pricing" / "calculation.json").read_text())
    gross = next(s for s in calc["premium_build_up"] if s["id"] == "gross_premium")["value"]
    assert offline["facts"]["premium"]["gross_premium"] == f"${gross:,.0f}"
    assert len(offline["facts"]["controls"]) == len(calc["controls"]["options"])
    assert offline["facts"]["tornado"]["bars"][0]["swing"] == f"${calc['tornado']['bars'][0]['swing']:,.0f}"


def test_number_checker():
    facts = {"premium": {"gross_premium": "$728,976", "rate_on_line": "36.4%"}, "controls": []}
    allowed = explainer.allowed_numbers(facts)
    assert explainer.unverified("The premium is $728,976, a 36.4% rate on line.", allowed) == []
    assert explainer.unverified("Roughly $729k.", allowed) == []          # correct rounding is fine
    assert explainer.unverified("Roughly $750k, or 40%.", allowed) == ["$750k", "40%"]
    assert explainer.unverified("Case r08 and the P90 loss.", allowed) == []  # ids and labels aren't numbers


def test_llm_narrative_with_bad_numbers_retries_then_falls_back(run_dir, monkeypatch):
    calls = []

    def fake(facts, notes, feedback=""):
        calls.append(feedback)
        n = explainer.template_narrative(facts)
        n["summary"] = "The premium is $999,999,999."          # invented number, every time
        n["controls"] = n["controls"][:1]                       # and one control missing
        return n

    monkeypatch.setattr(explainer, "llm_available", lambda: True)
    monkeypatch.setattr(explainer, "_llm_narrative", fake)
    r = explainer.run(str(run_dir))
    assert len(calls) == 2 and "$999,999,999" in calls[1]       # retried with feedback
    assert r["check"]["fallback_sections"] == ["summary"]
    assert "$999,999,999" not in r["narrative"]["summary"]
    assert len(r["narrative"]["controls"]) == len(r["facts"]["controls"])   # missing ones filled in
    assert explainer.check_narrative(r["narrative"], r["facts"]) == {}


def test_llm_narrative_clean_is_kept(run_dir, monkeypatch):
    def fake(facts, notes, feedback=""):
        n = explainer.template_narrative(facts)
        n["headline"] = f"ShopBot costs {facts['premium']['gross_premium']} a year to cover."
        return n

    monkeypatch.setattr(explainer, "llm_available", lambda: True)
    monkeypatch.setattr(explainer, "_llm_narrative", fake)
    r = explainer.run(str(run_dir))
    assert r["check"]["source"] == "claude" and not r["check"]["retried"]
    assert r["narrative"]["headline"].startswith("ShopBot costs $")


def test_html_escapes_untrusted_text(run_dir, monkeypatch):
    def fake(facts, notes, feedback=""):
        n = explainer.template_narrative(facts)
        n["red_team"] = "<script>alert(1)</script>"
        return n

    monkeypatch.setattr(explainer, "llm_available", lambda: True)
    monkeypatch.setattr(explainer, "_llm_narrative", fake)
    explainer.run(str(run_dir))
    html = (run_dir / "report" / "report.html").read_text()
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_charts_are_deterministic(run_dir):
    calc = json.loads((run_dir / "pricing" / "calculation.json").read_text())
    for fn in (charts.loss_distribution_svg, charts.tornado_svg, charts.controls_svg):
        a, b = fn(calc), fn(calc)
        assert a == b and a.startswith("<svg") and "<rect" in a


def test_histogram_accounts_for_every_year(run_dir):
    h = json.loads((run_dir / "pricing" / "calculation.json").read_text())["loss_distribution"]["histogram"]
    assert h["zero_years"] + sum(h["counts"]) == h["n_years"]


def test_changes_since_last_quote(run_dir, tmp_path):
    d = tmp_path / "rerun"
    shutil.copytree(run_dir, d)
    ins = yaml.safe_load((d / "insured.yaml").read_text())
    ins["factors"]["governance"] = "weak"
    (d / "insured.yaml").write_text(yaml.safe_dump(ins))
    pricer.run(str(d))                                    # previous quote moves to history/
    assert list((d / "pricing" / "history").glob("calculation-*.json"))
    r = explainer.run(str(d), offline=True)
    ch = r["facts"]["changes_since_last_quote"]
    assert ch["insured_changed"] is True and ch["change"].startswith("+")
    assert "moved from" in r["narrative"]["changes_since_last_quote"]
