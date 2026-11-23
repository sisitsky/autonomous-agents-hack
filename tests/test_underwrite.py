import json

import yaml

from agents import config, redteam, underwrite
from engine.cli import load_insured


def test_underwrite_end_to_end_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(underwrite, "RUNS_DIR", tmp_path / "runs")
    ins = load_insured("shopbot"); ins.pop("red_team"); ins["id"] = "acme"
    src = tmp_path / "insured.yaml"; src.write_text(yaml.safe_dump(ins))
    r = underwrite.run(insured_file=str(src), demo_bot="deterministic", offline=True,
                       export_dir=str(tmp_path / "export"), echo=False)
    run_dir = tmp_path / "runs" / "acme"
    events = [json.loads(l) for l in (run_dir / "events.jsonl").read_text().splitlines()]
    assert [e["step"] for e in events] == ["intake", "redteam", "pricing", "explainer", "review"]
    assert [e["status"] for e in events] == ["skipped", "ok", "ok", "ok", "review"]
    assert r["status"] == "indicative_pending_review" and r["gross_premium"] > 0
    assert sum(yaml.safe_load((run_dir / "insured.yaml").read_text())["red_team"]["failures_by_type"].values()) == 24
    b = json.loads((tmp_path / "export" / "acme.json").read_text())
    assert b["calculation"]["premium_build_up"] and b["report"]["narrative"] and len(b["redteam"]["cases"]) == len(redteam.load_suite("customer_service")["cases"])


def test_underwrite_logs_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(underwrite, "RUNS_DIR", tmp_path / "runs")
    ins = load_insured("shopbot"); ins["id"] = "broken"
    src = tmp_path / "insured.yaml"; src.write_text(yaml.safe_dump(ins))
    try:
        underwrite.run(insured_file=str(src), endpoint="http://127.0.0.1:9/chat", offline=True, echo=False)
    except SystemExit:
        pass
    events = [json.loads(l) for l in (tmp_path / "runs" / "broken" / "events.jsonl").read_text().splitlines()]
    assert events[-1]["step"] == "redteam" and events[-1]["status"] == "error"
