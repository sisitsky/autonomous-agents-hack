"""Loop 2 end to end: monitor -> calibrate -> validate -> pull request.

    python -m agents.loop2                 # full run, opens a PR when evidence moves a parameter
    python -m agents.loop2 --no-pr         # everything except the PR (dry run)
    python -m agents.loop2 --no-monitor    # calibrate the evidence already in the store

Runs hourly in GitHub Actions (.github/workflows/calibrate.yml). Parameter changes
never land on main directly: they go to a `calibration/<run>` branch and a pull
request whose body cites every event, its source, the Bayesian update, the
guardrail report and portfolio accumulation by provider. Merging is the human
decision; git history is the audit trail.

Every run also writes public/data/loop2.json (sources, evidence, changes, PRs) for
the web page, which reads it live from GitHub.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from engine.model import load_params

from . import calibrator, experience, feedback, monitor, suite_builder, validator
from .config import ROOT, llm_available

REPO = os.environ.get("GITHUB_REPOSITORY", "sisitsky/autonomous-agents-hack")
HISTORY = ROOT / "data" / "loop2" / "history.json"
PUBLIC = ROOT / "public" / "data" / "loop2.json"
PRIORITY = {"manual": 0, "court": 0, "enforcement": 1, "incident": 1, "regulation": 2, "disclosure": 3,
            "news": 3, "research": 4, "security": 5}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _git(*args, cwd=ROOT, check=True) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def _token() -> str | None:
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if tok:
        return tok
    gh = shutil.which("gh") or str(Path.home() / ".local" / "bin" / "gh")
    if Path(gh).exists():
        r = subprocess.run([gh, "auth", "token"], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return None


def _github(method: str, path: str, body: dict | None = None) -> dict:
    tok = _token()
    if not tok:
        raise RuntimeError("No GitHub token: run `gh auth login` or set GH_TOKEN")
    req = urllib.request.Request(f"https://api.github.com{path}", method=method,
                                 data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
                                          "User-Agent": "ai-underwriter-loop2"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"{}")


def select_items(limit: int = calibrator.MAX_ITEMS_PER_RUN) -> list[dict]:
    done = calibrator.load_processed()
    items = [i for i in monitor.load_store().values() if i["id"] not in done]
    def rank(t):
        t = (t or "").lower()
        return next((v for k, v in PRIORITY.items() if k in t), 9)
    items.sort(key=lambda i: (rank(i.get("type")), -(int((i.get("date") or "0000-00-00").replace("-", "")[:8] or 0))))
    return items[:limit]


def pr_body(run_id: str, changes: list[dict], v: dict, scaled: float | None, record: dict | None = None) -> str:
    record = record or {}
    L = [f"## Calibration run `{run_id}`", "",
         "Proposed by the Loop 2 agents. **Merging this PR is the human approval.** "
         "Illustrative pricing, not an offer of insurance.", ""]
    if scaled:
        L += [f"> The full evidence-implied changes failed the guardrails, so they were scaled to {scaled:.0%} "
              "of their size. A reviewer may want to go further over several PRs.", ""]
    L += ["### Evidence and parameter changes", "",
          "| Event | Source | Parameter | Old mode → new mode | Strength | Reasoning |", "|---|---|---|---|---|---|"]
    for c in changes:
        src = f"[{c['source']}]({c['source_url']})" if c.get("source_url") else c["source"]
        L.append(f"| {c['event'][:90]} | {src} | `{c['parameter']}` | {c['old']['mode']:.4g} → {c['new']['mode']:.4g} "
                 f"({c['mode_change_pct']:+.1%}) | {c['strength']} | {c['reasoning'][:220]} |")
    L += ["", "**Bayesian update** (engine/bayes.py): mode' = (w0·mode + w_e·implied) / (w0 + w_e), prior worth "
          "w0 = 20 observations (growing with each update), evidence weak 1 / moderate 3 / strong 6.", "",
          "### Guardrail report", "", "| Insured | Current | Proposed | Change |", "|---|---|---|---|"]
    for r in v["benchmark"]:
        L.append(f"| {r['insured']} | ${r['current']:,.0f} | ${r['proposed']:,.0f} | {r['change']:+.1%} |")
    L += ["", ("**All guardrails passed.** A human must still approve and merge." if v["passed"] else
               "**Guardrails failed:**\n" + "\n".join(f"- {p}" for p in v["problems"])), "",
          "### Portfolio accumulation by model provider", "",
          "| Provider | Policies | Aggregate limits | Expected loss (before → after) | Systemic capital (before → after) |",
          "|---|---|---|---|---|"]
    if record.get("new_tests"):
        L += ["", "### New red-team tests from evidence", "",
              "Added to `agents/test_suites/customer_service.yaml`; every future red-team run will include them.", "",
              "| Id | Category | Customer message | Inspired by | Demo ShopBot |", "|---|---|---|---|---|"]
        for t in record["new_tests"]:
            a = t["added_from_evidence"]
            src = f"[{a['source']}]({a['url']})" if a.get("url") else a["source"]
            L.append(f"| {t['id']} | {t['category']} | {t['input'][:120]} | {src}: {a['title'][:60]} | {t['demo_check']['pattern_verdict']} |")
    rep = record.get("experience")
    if rep:
        L += ["", f"### Claims experience: actual vs expected{' (SIMULATED book)' if rep['simulated'] else ''}", "",
              f"{rep['policy_years']:,} policy-years, {rep['claims']} claims. Multiplier m = (K + A) / (K + E), K = {rep['K']:.0f}; "
              "proposed only when the 90% interval excludes 1.", "",
              "| Peril | Actual | Expected | A/E | m (90%) | Proposed |", "|---|---|---|---|---|---|"]
        for r in rep["perils"]:
            L.append(f"| {r['peril']} | {r['actual_claims']} | {r['expected_claims']:.1f} | {r['ae_ratio']:.2f} | "
                     f"{r['multiplier']:.3f} ({r['multiplier_90'][0]:.2f}–{r['multiplier_90'][1]:.2f}) | {'yes' if r['propose'] else 'no'} |")
    if record.get("learning_notes"):
        L += ["", "### Learned from past reviews", ""] + [f"- {n}" for n in record["learning_notes"]]
    before = {r["provider"]: r for r in v["accumulation"]["before"]["by_provider"]}
    for r in v["accumulation"]["after"]["by_provider"]:
        b = before.get(r["provider"], r)
        L.append(f"| {r['provider']} | {r['policies']} | ${r['aggregate_limits']:,.0f} ({r['limit_share']:.0%}) | "
                 f"${b['expected_loss']:,.0f} → ${r['expected_loss']:,.0f} | ${b['systemic_capital']:,.0f} → ${r['systemic_capital']:,.0f} |")
    return "\n".join(L) + "\n"


def open_pr(run_id: str, cand_dir: Path, body: str, draft: bool) -> dict:
    """Branch from origin/main in a temporary worktree, commit priors.yaml, push, open the PR."""
    branch = f"calibration/{run_id}"
    _git("fetch", "origin", "main", check=False)
    wt = Path(tempfile.mkdtemp(prefix="loop2-"))
    try:
        _git("worktree", "add", "-b", branch, str(wt), "origin/main")
        shutil.copy(cand_dir / "priors.yaml", wt / "params" / "priors.yaml")
        _git("add", "params/priors.yaml", cwd=wt)
        if (cand_dir / "customer_service.yaml").exists():
            shutil.copy(cand_dir / "customer_service.yaml", wt / "agents" / "test_suites" / "customer_service.yaml")
            _git("add", "agents/test_suites/customer_service.yaml", cwd=wt)
        _git("-c", "user.name=ai-underwriter-loop2", "-c", "user.email=loop2@users.noreply.github.com",
             "commit", "-m", f"Calibration {run_id}: evidence-based parameter update\n\nProposed by the Loop 2 agents; "
             "see the PR for evidence, Bayesian updates, and the guardrail report.", cwd=wt)
        tok = _token()
        remote = f"https://x-access-token:{tok}@github.com/{REPO}.git" if tok and os.environ.get("CI") else "origin"
        _git("push", remote, branch, cwd=wt)
    finally:
        _git("worktree", "remove", "--force", str(wt), check=False)
    if not _token():
        # No API token locally: the branch is pushed over git; a human opens the PR in one click.
        url = f"https://github.com/{REPO}/compare/main...{branch}?expand=1"
        return {"number": None, "url": url, "branch": branch, "draft": draft, "needs_click": True}
    pr = _github("POST", f"/repos/{REPO}/pulls", {"title": f"Calibration {run_id}: evidence-based parameter update",
                                                 "head": branch, "base": "main", "body": body, "draft": draft})
    try:
        _github("POST", f"/repos/{REPO}/issues/{pr['number']}/labels", {"labels": ["calibration", "needs-human-approval"]})
    except Exception:  # noqa: BLE001 - labels are nice to have
        pass
    return {"number": pr["number"], "url": pr["html_url"], "branch": branch, "draft": draft}


def export(record: dict, sources: dict | None) -> None:
    hist = json.loads(HISTORY.read_text()) if HISTORY.exists() else []
    hist.append(record)
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(json.dumps(hist[-200:], indent=1, default=float))
    latest_run = json.loads(monitor.LATEST.read_text()) if monitor.LATEST.exists() else {}
    store = list(monitor.load_store().values())
    store.sort(key=lambda i: i.get("first_seen", ""), reverse=True)
    changes = [dict(c, run_id=r["run_id"], pr=r.get("pr"), run_at=r["run_at"]) for r in reversed(hist) for c in r.get("changes", [])]
    public = {
        "updated_at": _now(), "repo": REPO,
        "monitor": {"run_at": latest_run.get("run_at"), "window_days": latest_run.get("window_days"),
                    "sources": latest_run.get("sources", sources or {}), "totals": latest_run.get("totals", {})},
        "evidence_recent": [{k: i.get(k) for k in ("id", "source", "type", "title", "url", "date", "summary", "first_seen")}
                            for i in store[:60]],
        "changes": changes[:100],
        "runs": [{k: r.get(k) for k in ("run_id", "run_at", "status", "items_reviewed", "relevant", "pr", "scaled",
                                        "guardrails_passed")} for r in reversed(hist)][:50],
        "latest_validation": next((r.get("validation") for r in reversed(hist) if r.get("validation")), None),
        "new_tests": [dict(t, run_id=r["run_id"], pr=r.get("pr"), run_at=r["run_at"]) for r in reversed(hist) for t in r.get("new_tests", [])][:50],
        "experience": record.get("experience") or next((r.get("experience") for r in reversed(hist) if r.get("experience")), None),
        "feedback": feedback.load(),
        "learning_notes": next((r.get("learning_notes") for r in reversed(hist) if r.get("learning_notes")), []),
    }
    PUBLIC.write_text(json.dumps(public, default=float))


def run(do_monitor: bool = True, do_pr: bool = True, days: int = 30, echo: bool = True) -> dict:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    say = print if echo else (lambda *a, **k: None)
    sources = None
    if do_monitor:
        say("1. Monitor")
        sources = monitor.run(days, echo=echo)["sources"]
    items = select_items()
    record = {"run_id": run_id, "run_at": _now(), "items_reviewed": len(items), "changes": [], "new_tests": []}
    # Learn from past human decisions on GitHub (merged, edited, rejected).
    try:
        fb = feedback.refresh()
    except Exception as e:  # noqa: BLE001 - offline: use the last saved feedback
        say(f"   feedback refresh failed ({e}); using saved feedback")
        fb = feedback.load()
    # Actual vs expected claims experience (deterministic).
    params = load_params()
    done = calibrator.load_processed()
    rep = experience.report(params)
    exp_items, exp_dec = experience.evidence_items(rep) if rep else ([], [])
    exp_items = [i for i in exp_items if i["id"] not in done]
    exp_dec = [d for d in exp_dec if d["item_id"] in {i["id"] for i in exp_items}]
    record["experience"] = rep
    if not items and not exp_items:
        record["status"] = "no_new_evidence"
        export(record, sources); say("No new evidence."); return record
    if items and not llm_available():
        record["status"] = "skipped_no_api_key"
        export(record, sources); say("Calibration skipped: no ANTHROPIC_API_KEY."); return record

    say(f"2. Calibrate: {len(items)} evidence items, {len(exp_items)} claims-experience signals")
    llm_dec = calibrator.triage(items, params, feedback.prompt_examples(fb)) if items else []
    all_items = items + exp_items
    decisions, learn_notes = feedback.apply_policy(llm_dec + exp_dec, {i["id"]: i for i in all_items}, fb)
    record["learning_notes"] = learn_notes
    for n in learn_notes:
        say(f"   learned: {n}")
    prop = calibrator.propose(all_items, params, decisions)
    record["relevant"] = sum(1 for d in prop["decisions"] if d["relevant"])
    changes = prop["changes"]
    for c in changes:
        say(f"   {c['parameter']}: {c['old']['mode']:.4g} → {c['new']['mode']:.4g} ({c['mode_change_pct']:+.1%}) "
            f"[{c['strength']}] {c['event'][:70]}")

    # New red-team tests from evidence (improves how Loop 1 measures risk).
    new_tests, rejected_tests = [], []
    try:
        cands = suite_builder.candidates(items, decisions)
        if cands:
            new_tests, rejected_tests = suite_builder.validate(suite_builder.draft(cands), cands, run_id)
    except Exception as e:  # noqa: BLE001
        say(f"   test drafting failed: {e}")
    record["new_tests"], record["rejected_tests"] = new_tests, rejected_tests
    for t in new_tests:
        say(f"   new test {t['id']} [{t['category']}] from {t['added_from_evidence']['source']}: {t['input'][:70]}")
    if not changes and not new_tests:
        calibrator.mark_processed([i["id"] for i in all_items])
        record["status"] = "no_change"
        export(record, sources); say("No parameter changes or new tests warranted."); return record

    say("3. Validate")
    tmp = Path(tempfile.mkdtemp(prefix="cand-"))
    scaled = None
    calibrator.write_priors(changes, tmp, run_id)
    if new_tests:
        suite_builder.write_suite(new_tests, tmp / "customer_service.yaml")
    v = validator.validate(tmp)
    for f in (0.5, 0.25):
        if v["passed"]:
            break
        scaled = f
        changes = calibrator.scale_changes(params, prop["changes"], f)["changes"]
        calibrator.write_priors(changes, tmp, run_id)
        v = validator.validate(tmp)
    for r in v["benchmark"]:
        say(f"   {r['insured']:<12} ${r['current']:>9,.0f} → ${r['proposed']:>9,.0f} ({r['change']:+.1%})")
    say(f"   guardrails {'passed' if v['passed'] else 'FAILED: ' + '; '.join(v['problems'])}")

    record.update(changes=changes, scaled=scaled, guardrails_passed=v["passed"],
                  validation={k: v[k] for k in ("passed", "problems", "benchmark", "limits", "accumulation")})
    body = pr_body(run_id, changes, v, scaled, record)
    (ROOT / "pr_body.md").write_text(body)
    if do_pr and os.environ.get("CI") and _token():
        try:
            open_prs = _github("GET", f"/repos/{REPO}/pulls?state=open&per_page=50")
            if any(p["head"]["ref"].startswith("calibration/") for p in open_prs):
                record["status"] = "waiting_for_review"
                export(record, sources)
                say("An open calibration PR is awaiting review; new evidence waits for the next run.")
                return record
        except Exception:  # noqa: BLE001
            pass
    if do_pr:
        say("4. Pull request")
        try:
            record["pr"] = open_pr(run_id, tmp, body, draft=not v["passed"])
            say(f"   {record['pr']['url']}")
            record["status"] = "pr_opened"
        except Exception as e:  # noqa: BLE001
            record["status"] = "pr_failed"
            record["pr_error"] = str(e)[:300]
            say(f"   PR not opened: {e}")
    else:
        record["status"] = "proposed_dry_run"
    calibrator.mark_processed([i["id"] for i in all_items])
    export(record, sources)
    shutil.rmtree(tmp, ignore_errors=True)
    return record


def main():
    ap = argparse.ArgumentParser(prog="agents.loop2")
    ap.add_argument("--no-monitor", action="store_true")
    ap.add_argument("--no-pr", action="store_true")
    ap.add_argument("--days", type=int, default=30)
    a = ap.parse_args()
    print("Loop 2: actuarial self-improvement")
    r = run(not a.no_monitor, not a.no_pr, a.days)
    print(f"Status: {r['status']}")


if __name__ == "__main__":
    main()
