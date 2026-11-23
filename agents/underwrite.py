"""Loop 1 end to end: intake -> red team -> pricing -> explainer.

One command runs every step for one customer, records each step in an activity
log (events.jsonl, the web app's loop feed), and stops at a human decision:
the quote is issued as INDICATIVE, pending underwriter review of the open
questions. Nothing is bound.

    python -m agents.underwrite agents/submissions/northwind --id northwind --demo-bot deterministic
    python -m agents.underwrite agents/submissions/northwind --id acme --endpoint http://127.0.0.1:8765/chat
    python -m agents.underwrite --insured agents/runs/northwind/insured.yaml --demo-bot llm   # skip intake

    --demo-bot deterministic|llm   start our own ShopBot in-process and test it
    --export public/data/runs       also write a single JSON bundle for the web app
"""
from __future__ import annotations

import argparse
import json
import shutil
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import yaml

from engine import credibility

from . import explainer, intake, pricer, redteam
from .config import RUNS_DIR, llm_available

STEPS = ["intake", "redteam", "pricing", "explainer"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Log:
    """Append-only activity log for one run."""

    def __init__(self, path: Path, echo: bool = True):
        self.path, self.echo, self.events = path, echo, []
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")

    def add(self, step: str, status: str, summary: str, duration_s: float | None = None, **data):
        e = {"at": _now(), "step": step, "status": status, "summary": summary}
        if duration_s is not None:
            e["duration_s"] = round(duration_s, 1)
        if data:
            e["data"] = data
        self.events.append(e)
        with open(self.path, "a") as f:
            f.write(json.dumps(e, default=str) + "\n")
        if self.echo:
            mark = {"ok": "✓", "skipped": "–", "error": "✗", "review": "!"}.get(status, "·")
            dur = f" ({e['duration_s']}s)" if duration_s is not None else ""
            print(f"  {mark} {step:<10} {summary}{dur}")


def start_demo_bot(kind: str):
    from .target_bot import shopbot
    srv = shopbot.serve(port=0, use_llm=(kind == "llm"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/chat"


def run(paths: list[str] | None = None, insured_id: str | None = None, insured_file: str | None = None,
        endpoint: str | None = None, demo_bot: str | None = None, scorer: str = "auto",
        authorized: bool = False, offline: bool = False, export_dir: str | None = None,
        echo: bool = True) -> dict:
    if not paths and not insured_file:
        raise SystemExit("Give submission documents, or --insured to start from an existing insured YAML")

    # Where this run lives.
    if insured_file:
        ins0 = yaml.safe_load(Path(insured_file).read_text())
        insured_id = insured_id or ins0["id"]
    run_dir = RUNS_DIR / insured_id if insured_id else None
    started = time.monotonic()
    log = None
    srv = None
    try:
        # ---- 1. intake
        if insured_file:
            run_dir = run_dir or RUNS_DIR / ins0["id"]
            run_dir.mkdir(parents=True, exist_ok=True)
            log = Log(run_dir / "events.jsonl", echo)
            src = Path(insured_file).resolve()
            if src != (run_dir / "insured.yaml").resolve():
                shutil.copy(src, run_dir / "insured.yaml")
                prof = src.parent / "profile.json"
                if prof.exists():
                    shutil.copy(prof, run_dir / "profile.json")
            # Each run is its own insured in the portfolio experience (credibility).
            if ins0.get("id") != run_dir.name:
                text = (run_dir / "insured.yaml").read_text()
                header = "".join(l for l in text.splitlines(keepends=True) if l.startswith("#"))
                ins0["id"] = run_dir.name
                (run_dir / "insured.yaml").write_text(header + yaml.safe_dump(ins0, sort_keys=False))
            log.add("intake", "skipped", f"Started from existing insured {src.name}")
        else:
            if offline or not llm_available():
                raise SystemExit("Intake needs the Claude API (documents can't be read offline). "
                                 "Use --insured to start from an existing insured YAML.")
            t = time.monotonic()
            tmp_id = insured_id or "pending"
            run_dir = RUNS_DIR / tmp_id
            log = Log(run_dir / "events.jsonl", echo)
            r = intake.run(paths, insured_id, out_dir=str(run_dir))
            p = r["profile"]
            log.add("intake", "ok",
                    f"{p['company_name']} / {p['system_name']}: {p['use_case']['category']}, "
                    f"{len(r['insured']['perils'])} perils, {len(r['open_questions'])} open questions",
                    time.monotonic() - t, perils=list(r["insured"]["perils"]),
                    factors=r["insured"]["factors"], provider=r["insured"].get("provider"),
                    open_questions=r["open_questions"], review_notes=r["review_notes"])

        profile_path = run_dir / "profile.json"
        profile = json.loads(profile_path.read_text()) if profile_path.exists() else {}

        # ---- 2. red team
        if demo_bot:
            srv, endpoint = start_demo_bot(demo_bot)
        endpoint = endpoint or profile.get("test_endpoint")
        if endpoint:
            t = time.monotonic()
            s = redteam.run(str(run_dir), endpoint=endpoint, scorer="patterns" if offline else scorer,
                            authorized=authorized or bool(demo_bot), generate=not demo_bot)
            if demo_bot:   # label the target, not the ephemeral local port
                s["endpoint"] = f"demo ShopBot ({demo_bot})"
                (run_dir / "redteam" / "summary.json").write_text(json.dumps(s, indent=2))
            # Continuously updated credibility: add this result to the portfolio experience.
            exp = credibility.record(yaml.safe_load((run_dir / "insured.yaml").read_text())["id"], s["suite_id"],
                                     s["tested"], s["failures_by_type"], source=f"underwriting run {run_dir.name}")
            others = len({e["insured_id"] for e in exp}) - 1
            log.add("redteam", "ok",
                    f"{s['failures']} of {s['tested']} material failures ("
                    + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in s["failures_by_type"].items() if v)
                    + f"); scorer {s['scorer']}",
                    time.monotonic() - t, target=f"demo ShopBot ({demo_bot})" if demo_bot else endpoint,
                    by_category=s["by_category"], disagreements=s["disagreements"],
                    experience=f"portfolio record updated: {others + 1} insureds on suite {s['suite_id']}")
        else:
            log.add("redteam", "skipped", "No authorized test endpoint; priced on the default P(error) prior")

        # ---- 3. pricing (deterministic)
        t = time.monotonic()
        pr = pricer.run(str(run_dir))
        q, calc = pr["quote"], pr["calculation"]
        post = q.get("red_team_posterior")
        plans_txt = "; ".join(f"{v['name']} ${v['gross_premium']:,.0f}" for v in calc["plans"].values())
        log.add("pricing", "ok", f"{calc['plan']['name']} plan ${q['gross_premium']:,.0f} "
                f"(rate on line {q['rate_on_line']:.1%}); all plans: {plans_txt}; reproducibility check passed"
                + (f"; Jewell credibility Z = {post['z']:.2f} (A = {post['A']:.1f})" if post else ""),
                time.monotonic() - t, gross_premium=q["gross_premium"], expected_loss=q["expected_loss"],
                params_versions=calc["meta"]["params_versions"],
                credibility=({"z": round(post["z"], 3), "A": round(post["A"], 2), "insureds": post["r"]} if post else None))

        # ---- 4. explainer
        t = time.monotonic()
        ex = explainer.run(str(run_dir), offline=offline)
        c = ex["check"]
        log.add("explainer", "ok", f"Report written ({c['source']} narrative, numbers verified"
                + (f"; template fallback: {', '.join(c['fallback_sections'])}" if c.get("fallback_sections") else "")
                + ")", time.monotonic() - t, headline=ex["narrative"]["headline"])

        # ---- human on the loop
        notes = explainer.intake_notes(run_dir)
        n_open = len(notes["open_questions"])
        uw = calc.get("underwriting") or {"decision": "quote", "reasons": []}
        head = {"refer": "Referred to a human underwriter", "indicative_untested": "Indicative quote (untested)"}.get(
            uw["decision"], "Indicative quote ready")
        log.add("review", "review", f"{head}. Awaiting underwriter review"
                + (f" of {n_open} open questions" if n_open else "") + ". Not bound. " + " ".join(uw["reasons"]),
                decision=uw["decision"])

        result = {"id": run_dir.name, "status": "indicative_pending_review", "run_dir": str(run_dir),
                  "gross_premium": q["gross_premium"], "report": str(Path(ex["out_dir"]) / "report.html"),
                  "duration_s": round(time.monotonic() - started, 1)}
        (run_dir / "run.json").write_text(json.dumps(result, indent=2))
        if export_dir:
            result["bundle"] = str(export_bundle(run_dir, Path(export_dir)))
        return result
    except SystemExit as e:
        if log:
            log.add(_current_step(log), "error", str(e))
        raise
    except Exception as e:
        if log:
            log.add(_current_step(log), "error", f"{type(e).__name__}: {e}", trace=traceback.format_exc())
        raise
    finally:
        if srv:
            srv.shutdown()


def _current_step(log: Log) -> str:
    done = [e["step"] for e in log.events if e["status"] in ("ok", "skipped")]
    return next((s for s in STEPS if s not in done), "review")


def export_bundle(run_dir: Path, export_dir: Path) -> Path:
    """One JSON file with everything the web app shows for a run."""
    def j(p):
        return json.loads(p.read_text()) if p.exists() else None

    ins_text = (run_dir / "insured.yaml").read_text()
    rt_summary, rt_results = explainer.load_red_team(run_dir)
    bundle = {
        "id": run_dir.name,
        "exported_at": _now(),
        "profile": j(run_dir / "profile.json"),
        "insured": yaml.safe_load(ins_text),
        "review_notes": explainer.notes_from(ins_text, None)["review_notes"],
        "redteam": {"summary": rt_summary, "cases": rt_results} if rt_summary else None,
        "calculation": j(run_dir / "pricing" / "calculation.json"),
        "report": j(run_dir / "report" / "narrative.json"),
        "events": [json.loads(l) for l in (run_dir / "events.jsonl").read_text().splitlines()]
                  if (run_dir / "events.jsonl").exists() else [],
    }
    export_dir.mkdir(parents=True, exist_ok=True)
    out = export_dir / f"{run_dir.name}.json"
    out.write_text(json.dumps(bundle, default=str))
    return out


def main():
    ap = argparse.ArgumentParser(prog="agents.underwrite")
    ap.add_argument("paths", nargs="*", help="Submission documents or folders")
    ap.add_argument("--id", help="Insured id")
    ap.add_argument("--insured", help="Skip intake: start from this insured YAML")
    ap.add_argument("--endpoint", help="Chat endpoint to red-team (default: from the intake profile)")
    ap.add_argument("--demo-bot", choices=["deterministic", "llm"], help="Test our own demo ShopBot")
    ap.add_argument("--scorer", choices=["auto", "judge", "patterns"], default="auto")
    ap.add_argument("--authorized", action="store_true", help="Owner authorized testing a non-local endpoint")
    ap.add_argument("--offline", action="store_true", help="No API calls after intake (pattern scoring, template report)")
    ap.add_argument("--export", help="Also write a JSON bundle for the web app into this folder")
    a = ap.parse_args()
    print("Underwriting (Loop 1)")
    r = run(a.paths, a.id, a.insured, a.endpoint, a.demo_bot, a.scorer, a.authorized, a.offline, a.export)
    print(f"Indicative premium ${r['gross_premium']:,.0f} · pending underwriter review · {r['duration_s']}s")
    print(f"Report: {r['report']}")
    if r.get("bundle"):
        print(f"Web bundle: {r['bundle']}")


if __name__ == "__main__":
    main()
