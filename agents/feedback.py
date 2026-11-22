"""Learning from human decisions (Loop 2).

Every calibration proposal ends in a human decision on GitHub: merged as
proposed, merged after the reviewer edited the values, or closed (rejected).
This module reads those outcomes back and turns them into three things the
calibrator uses on the next run:

1. Acceptance rates by evidence source and by strength. A source the reviewer
   keeps rejecting (at least 3 decisions, 75% or more rejected) is skipped.
2. A human scale per parameter: (merged change) / (proposed change). If the
   reviewer habitually halves a parameter's moves, future implied changes for it
   are scaled by 0.5 (bounded 0 to 1.5).
3. Recent decisions as worked examples in the calibrator's prompt.

    python -m agents.feedback        # refresh data/loop2/feedback.json from GitHub
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

import yaml

from .config import ROOT

REPO = os.environ.get("GITHUB_REPOSITORY", "sisitsky/autonomous-agents-hack")
HISTORY = ROOT / "data" / "loop2" / "history.json"
FEEDBACK = ROOT / "data" / "loop2" / "feedback.json"
MIN_DECISIONS = 3
REJECT_SKIP = 0.75


def _gh(path: str):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "ai-underwriter-loop2"}
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    with urllib.request.urlopen(urllib.request.Request(f"https://api.github.com{path}", headers=headers), timeout=20) as r:
        return json.loads(r.read())


def _raw(sha: str, path: str) -> str:
    with urllib.request.urlopen(f"https://raw.githubusercontent.com/{REPO}/{sha}/{path}", timeout=20) as r:
        return r.read().decode()


def _value(priors_text: str, param: str) -> float | None:
    node = yaml.safe_load(priors_text)
    try:
        for k in param.split("."):
            node = node[k]
        return float(node["mode"])
    except (KeyError, TypeError, ValueError):
        return None


def _find_pr(pr: dict) -> dict | None:
    if pr.get("number"):
        return _gh(f"/repos/{REPO}/pulls/{pr['number']}")
    owner = REPO.split("/")[0]
    q = urllib.parse.urlencode({"state": "all", "head": f"{owner}:{pr['branch']}"})
    found = _gh(f"/repos/{REPO}/pulls?{q}")
    return found[0] if found else None


def refresh() -> dict:
    """Read every past proposal's outcome from GitHub and summarize."""
    hist = json.loads(HISTORY.read_text()) if HISTORY.exists() else []
    decisions = []
    for run in hist:
        if not run.get("pr") or not run.get("changes"):
            continue
        try:
            pr = _find_pr(run["pr"])
        except Exception:  # noqa: BLE001 - offline or rate-limited: keep what we have
            pr = None
        if not pr or pr["state"] == "open":
            continue
        merged = bool(pr.get("merged_at"))
        head_text = None
        if merged:
            try:
                head_text = _raw(pr["head"]["sha"], "params/priors.yaml")
            except Exception:  # noqa: BLE001
                head_text = None
        # Sequential changes to one parameter: compare the final proposed value with what was merged.
        final = {}
        for c in run["changes"]:
            if c["parameter"] not in final:
                final[c["parameter"]] = {"old": c["old"]["mode"], "proposed": c["new"]["mode"], "items": []}
            final[c["parameter"]]["proposed"] = c["new"]["mode"]
            final[c["parameter"]]["items"].append(c)
        for param, f in final.items():
            merged_val = _value(head_text, param) if head_text else (f["proposed"] if merged else None)
            prop_delta = f["proposed"] - f["old"]
            scale = ((merged_val - f["old"]) / prop_delta) if merged and merged_val is not None and prop_delta else (1.0 if merged else 0.0)
            outcome = "rejected" if not merged else ("edited" if abs(scale - 1) > 0.02 else "approved")
            for c in f["items"]:
                decisions.append({"run_id": run["run_id"], "pr": pr["number"], "pr_url": pr["html_url"], "outcome": outcome,
                                  "parameter": param, "source": c["source"], "strength": c["strength"],
                                  "event": c["event"][:140], "human_scale": round(scale, 3),
                                  "decided_at": pr.get("merged_at") or pr.get("closed_at")})
    by_source, by_strength, scale_by_param = {}, {}, {}
    for d in decisions:
        for table, key in ((by_source, d["source"]), (by_strength, d["strength"])):
            t = table.setdefault(key, {"approved": 0, "edited": 0, "rejected": 0})
            t[d["outcome"]] += 1
        if d["outcome"] != "rejected":
            scale_by_param.setdefault(d["parameter"], []).append(d["human_scale"])
    for table in (by_source, by_strength):
        for t in table.values():
            n = sum(t.values())
            t["n"] = n
            t["acceptance"] = (t["approved"] + t["edited"]) / n if n else None
    scales = {p: max(0.0, min(1.5, sum(v) / len(v))) for p, v in scale_by_param.items()}
    skip = [s for s, t in by_source.items() if t["n"] >= MIN_DECISIONS and 1 - t["acceptance"] >= REJECT_SKIP]
    fb = {"decisions": decisions[-100:], "by_source": by_source, "by_strength": by_strength,
          "human_scale_by_parameter": scales, "skip_sources": skip,
          "rules": {"min_decisions": MIN_DECISIONS, "skip_if_rejection_rate_at_least": REJECT_SKIP}}
    FEEDBACK.parent.mkdir(parents=True, exist_ok=True)
    FEEDBACK.write_text(json.dumps(fb, indent=1))
    return fb


def load() -> dict:
    return json.loads(FEEDBACK.read_text()) if FEEDBACK.exists() else {
        "decisions": [], "by_source": {}, "by_strength": {}, "human_scale_by_parameter": {}, "skip_sources": []}


def prompt_examples(fb: dict, n: int = 8) -> str:
    """Recent human decisions, phrased for the calibrator's prompt."""
    if not fb["decisions"]:
        return "No past human decisions yet."
    lines = [f"- {d['outcome'].upper()}: {d['source']} ({d['strength']}) '{d['event']}' -> {d['parameter']}"
             + (f" (reviewer kept {d['human_scale']:.0%} of the proposed move)" if d["outcome"] == "edited" else "")
             for d in fb["decisions"][-n:]]
    acc = ", ".join(f"{k} {v['acceptance']:.0%} of {v['n']}" for k, v in fb["by_source"].items() if v.get("acceptance") is not None)
    return "Past human decisions on your proposals (learn from them):\n" + "\n".join(lines) + (f"\nAcceptance by source: {acc}." if acc else "")


def apply_policy(decisions: list[dict], items_by_id: dict, fb: dict) -> tuple[list[dict], list[str]]:
    """Deterministic learned policy on top of the calibrator's decisions."""
    notes, out = [], []
    for d in decisions:
        it = items_by_id.get(d["item_id"], {})
        if d["relevant"] and it.get("source") in fb.get("skip_sources", []):
            notes.append(f"Skipped {it.get('source')} item '{str(it.get('title'))[:60]}': the reviewer rejects this source "
                         f"({fb['by_source'][it['source']]['rejected']} of {fb['by_source'][it['source']]['n']}).")
            out.append({**d, "relevant": False, "learned_skip": True})
            continue
        sc = fb.get("human_scale_by_parameter", {}).get(d.get("parameter"))
        if d["relevant"] and sc is not None and abs(sc - 1) > 0.02:
            notes.append(f"Scaled {d['parameter']} by {sc:.2f}: the reviewer's past edits kept that share of proposed moves.")
            out.append({**d, "implied_change": d["implied_change"] * sc, "learned_scale": sc})
            continue
        out.append(d)
    return out, notes


if __name__ == "__main__":
    fb = refresh()
    print(f"{len(fb['decisions'])} decisions · by source: { {k: v['acceptance'] for k, v in fb['by_source'].items()} } "
          f"· scales: {fb['human_scale_by_parameter']} · skip: {fb['skip_sources']}")
