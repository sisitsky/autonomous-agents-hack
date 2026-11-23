"""Loop 3: growth (the business itself, via Egbe).

    python -m agents.growth.loop3 research     # research agent: target segments (Egbe research, else Claude web search)
    python -m agents.growth.loop3 campaign     # campaign agent: landing copy, outreach + ad drafts, budget plan -> PR for approval
    python -m agents.growth.loop3 analytics    # analytics agent: conversion by segment -> rewrites agents/growth_playbook.md
    python -m agents.growth.loop3 all

1. Research: finds segments such as startups shipping customer-facing AI agents,
   with evidence, then prices an archetype system for each segment with the real
   engine (indicative premium and a 0-100 risk score), so messaging uses real numbers.
2. Campaign: drafts, per segment, a "free AI risk score" landing variant (served at
   /score on the site), an outreach email and ad copy, with UTM campaign ids and a
   budget split. Guardrails (params/guardrails.yaml, human-only): spend caps are
   enforced in code before anything is planned; every outreach template is created
   as "pending approval" and lands in a pull request. Nothing is sent or bought
   until a human merges it. Payment for the full report uses an Egbe Stripe
   checkout link (created through Egbe's payments skill; its URL is stored in
   data/growth/config.json).
3. Analytics: reads conversion by UTM campaign from Egbe analytics (visits ->
   score requests -> checkout -> purchase), computes funnel rates and cost per
   purchase in code, and has Claude rewrite the targeting playbook for the next
   cycle. Rule: growth data never changes price (it only changes targeting/messaging).
   Without Egbe analytics there is no data, and the loop says so; `--simulate` writes
   a clearly labeled SIMULATED funnel for the demo.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from datetime import datetime, timezone

import numpy as np
import yaml

from engine.model import apply_plan, load_params, price

from .. import llm
from ..config import MODEL_FAST, MODEL_REASONING, ROOT, llm_available
from . import egbe

DATA = ROOT / "data" / "growth"
PLAYBOOK = ROOT / "agents" / "growth_playbook.md"
PUBLIC = ROOT / "public" / "data" / "growth.json"
CONFIG = DATA / "config.json"
FULL_REPORT_PRICE = 49


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load(name, default):
    p = DATA / name
    return json.loads(p.read_text()) if p.exists() else default


def _save(name, obj):
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / name).write_text(json.dumps(obj, indent=1, default=float))


def guardrails() -> dict:
    return load_params()["guardrails"]["growth"]


# ---------------------------------------------------------------- 1. research

SEG_SCHEMA = {"type": "object", "properties": {"segments": {"type": "array", "items": {
    "type": "object", "properties": {
        "id": {"type": "string", "description": "short slug"},
        "name": {"type": "string"}, "description": {"type": "string"},
        "why_they_need_cover": {"type": "string"},
        "buying_signals": {"type": "array", "items": {"type": "string"}},
        "example_companies": {"type": "array", "items": {"type": "string"}, "description": "public companies or products, only if found in research"},
        "channels": {"type": "array", "items": {"type": "string", "enum": ["linkedin_ads", "google_ads", "reddit_ads", "cold_email", "communities", "partnerships"]}},
        "evidence": {"type": "array", "items": {"type": "string"}, "description": "sources or facts from the research"},
        "archetype": {"type": "object", "properties": {
            "use_case_criticality": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
            "autonomy": {"type": "string", "enum": ["advisory_human_signoff", "human_review_sampled", "autonomous", "autonomous_actions"]},
            "data_sensitivity": {"type": "string", "enum": ["none", "personal", "financial_or_health"]},
            "jurisdiction": {"type": "string", "enum": ["us_low_reg", "us_high_reg", "eu"]},
            "customer_facing": {"type": "string", "enum": ["internal", "public"]},
            "outputs_per_year": {"type": "integer"},
            "perils": {"type": "array", "items": {"type": "string", "enum": ["wrong_information", "unauthorized_commitment", "data_leakage",
                       "prompt_injection", "bias_discrimination", "ip_infringement", "harmful_content", "unsafe_advice"]}}},
            "required": ["use_case_criticality", "autonomy", "data_sensitivity", "jurisdiction", "customer_facing", "outputs_per_year", "perils"],
            "additionalProperties": False}},
    "required": ["id", "name", "description", "why_they_need_cover", "buying_signals", "example_companies", "channels", "evidence", "archetype"],
    "additionalProperties": False}}}, "required": ["segments"], "additionalProperties": False}

RESEARCH_Q = ("Which segments of companies shipping customer-facing AI (chatbots, AI agents, copilots) are most likely to buy "
              "AI liability insurance or an AI risk assessment in the next 6 months? For each segment: who they are, why "
              "they face liability (recent incidents, lawsuits, regulation such as the EU AI Act or state laws, AI exclusions "
              "in standard E&O/cyber policies), buying signals, example public companies, and the channels to reach them.")


VOLUME_RANGE = (100_000, 5_000_000)   # a typical early-stage customer in the segment


def score_archetype(a: dict, params: dict) -> dict:
    """Price the segment's typical early-stage system with the real engine (no red
    team: portfolio prior). Volume is clamped to a startup-sized range."""
    rt = {"wrong_information", "unauthorized_commitment", "data_leakage", "prompt_injection"}
    a["outputs_per_year"] = int(min(max(a["outputs_per_year"], VOLUME_RANGE[0]), VOLUME_RANGE[1]))
    ins = {"id": "archetype", "provider": None,
           "perils": {p: ({"outputs_per_year": a["outputs_per_year"]} if p in rt else {}) for p in a["perils"]},
           "factors": {k: a[k] for k in ("use_case_criticality", "autonomy", "data_sensitivity", "jurisdiction", "customer_facing")}}
    out = {}
    for plan in params["plans"]["plans"]:
        q = price(apply_plan(ins, params, plan), params, n_outer=500, n_inner=200)
        out[plan] = {"premium": q["gross_premium"], "rate_on_line": q["rate_on_line"], "expected_loss": q["expected_loss"]}
    # 0-100 risk score from the Professional rate on line (1% -> 25, 4% -> 100), for messaging only.
    rol = out.get("professional", next(iter(out.values())))["rate_on_line"]
    return {"plans": out, "risk_score": int(round(min(100, max(1, rol * 2500))))}


def rescore(echo=print) -> dict:
    segs = _load("segments.json", None)
    params = load_params()
    for s in segs["segments"]:
        s["score"] = score_archetype(s["archetype"], params)
        echo(f"   {s['name'][:50]:<50} {s['archetype']['outputs_per_year']:>10,}/yr  risk score {s['score']['risk_score']:>3} · "
             f"Essential ${s['score']['plans']['essential']['premium']:,.0f} · Professional ${s['score']['plans']['professional']['premium']:,.0f}")
    _save("segments.json", segs)
    return segs


def research(echo=print) -> dict:
    if not llm_available():
        raise SystemExit("Research needs ANTHROPIC_API_KEY")
    params = load_params()
    key = "market-research:segments:" + hashlib.sha256(RESEARCH_Q.encode()).hexdigest()[:16] + ":" + datetime.now(timezone.utc).strftime("%Y%m")
    if egbe.available():
        echo("   research via Egbe /research (counts 1 of 20 monthly jobs)")
        md, via = egbe.research(RESEARCH_Q, "Cite sources. Be specific about company examples and dates.", key), "egbe_research"
    else:
        echo("   Egbe not connected: research via Claude web search")
        md, via = llm.web_research("You are a B2B market researcher for an AI-liability insurance startup. Cite sources with URLs.",
                                   RESEARCH_Q), "claude_web_search"
    segs = llm.structured("Turn the research into 4-6 target segments. Only cite companies and facts that appear in the research. "
                          "The archetype describes a typical EARLY-STAGE customer's AI system in the segment, for pricing "
                          "(outputs_per_year between 100,000 and 5,000,000).",
                          f"<research>\n{md[:60000]}\n</research>", SEG_SCHEMA, model=MODEL_REASONING, max_tokens=16000)["segments"]
    for s in segs:
        s["id"] = re.sub(r"[^a-z0-9-]+", "-", s["id"].lower()).strip("-")[:40]
        s["score"] = score_archetype(s["archetype"], params)
    out = {"run_at": _now(), "via": via, "research_markdown": md, "segments": segs}
    _save("segments.json", out)
    for s in segs:
        echo(f"   {s['name'][:50]:<50} risk score {s['score']['risk_score']:>3} · Professional ${s['score']['plans']['professional']['premium']:,.0f}")
    return out


# ---------------------------------------------------------------- 2. campaign

CAMP_SCHEMA = {"type": "object", "properties": {"campaigns": {"type": "array", "items": {
    "type": "object", "properties": {
        "segment_id": {"type": "string"},
        "landing": {"type": "object", "properties": {"headline": {"type": "string"}, "subhead": {"type": "string"},
                    "bullets": {"type": "array", "items": {"type": "string"}}, "cta": {"type": "string"}},
                    "required": ["headline", "subhead", "bullets", "cta"], "additionalProperties": False},
        "outreach_email": {"type": "object", "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
                           "required": ["subject", "body"], "additionalProperties": False},
        "ad": {"type": "object", "properties": {"channel": {"type": "string"}, "headline": {"type": "string"}, "text": {"type": "string"}},
               "required": ["channel", "headline", "text"], "additionalProperties": False},
        "budget_usd": {"type": "number"}},
    "required": ["segment_id", "landing", "outreach_email", "ad", "budget_usd"], "additionalProperties": False}}},
    "required": ["campaigns"], "additionalProperties": False}


def campaign(cycle: int | None = None, echo=print) -> dict:
    segs = _load("segments.json", None)
    if not segs:
        raise SystemExit("Run research first")
    g = guardrails()
    playbook = PLAYBOOK.read_text() if PLAYBOOK.exists() else ""
    past = _load("cycles.json", [])
    cycle = cycle or (len(past) + 1)
    spent = sum(c.get("spend_usd", 0) for c in past)
    budget_left = max(0.0, g["max_total_hackathon_spend_usd"] - spent)
    facts = [{"id": s["id"], "name": s["name"], "why": s["why_they_need_cover"], "risk_score": s["score"]["risk_score"],
              "professional_premium": round(s["score"]["plans"]["professional"]["premium"]),
              "essential_premium": round(s["score"]["plans"]["essential"]["premium"]), "channels": s["channels"]} for s in segs["segments"]]
    res = llm.structured(
        "You are the campaign agent for iacta.ai, an AI-liability underwriting startup. Offer: a free AI risk score in 2 minutes, "
        f"then a full underwriting report for ${FULL_REPORT_PRICE}. Write concise, specific, honest copy. Never promise insurance "
        "coverage: say 'indicative' and 'not an offer of insurance'. Quote only the risk scores and premiums given. "
        "Pick the 3 best segments for this cycle per the playbook. Outreach emails are short (under 120 words), plain text, with "
        "an opt-out line. Total budget across campaigns must not exceed the budget given.",
        f"<playbook>\n{playbook}\n</playbook>\n<segments>\n{json.dumps(facts)}\n</segments>\n"
        f"<budget>Cycle {cycle}: at most ${min(budget_left, g['max_daily_ad_spend_usd'] * 3):.0f} total for this cycle "
        f"(daily cap ${g['max_daily_ad_spend_usd']}, hackathon cap ${g['max_total_hackathon_spend_usd']}, already spent ${spent:.0f}).</budget>",
        CAMP_SCHEMA, model=MODEL_FAST, max_tokens=12000)["campaigns"]
    # Guardrails, enforced in code (the model's budget is advisory).
    cap = min(budget_left, g["max_daily_ad_spend_usd"] * 3)
    total = sum(max(0.0, c["budget_usd"]) for c in res) or 1
    scale = min(1.0, cap / total)
    notes = []
    if scale < 1:
        notes.append(f"Budgets scaled by {scale:.2f} to respect the cap (${cap:.0f} this cycle).")
    by_id = {s["id"]: s for s in segs["segments"]}
    camps = []
    for c in res:
        if c["segment_id"] not in by_id:
            continue
        utm = f"iacta-c{cycle}-{c['segment_id']}"[:60]
        camps.append({**c, "budget_usd": round(max(0.0, c["budget_usd"]) * scale, 2), "utm_campaign": utm,
                      "landing_url": f"https://iacta-ai.vercel.app/score?seg={c['segment_id']}&utm_source={c['ad']['channel']}&utm_medium=paid&utm_campaign={utm}",
                      "approval": "pending" if g["outreach_template_requires_approval"] else "approved",
                      "daily_cap_usd": g["max_daily_ad_spend_usd"], "max_emails_per_day": g["max_outreach_emails_per_day"]})
    plan = {"cycle": cycle, "created_at": _now(), "campaigns": camps, "total_budget_usd": round(sum(c["budget_usd"] for c in camps), 2),
            "budget_left_before": budget_left, "guardrail_notes": notes,
            "status": "pending_approval", "how_to_approve": "Merge the growth pull request; nothing is sent or bought before that."}
    _save("campaign_plan.json", plan)
    for c in camps:
        echo(f"   {c['segment_id']:<36} ${c['budget_usd']:>6.2f}  {c['ad']['channel']:<12} {c['landing']['headline'][:60]}")
    echo(f"   total ${plan['total_budget_usd']:.2f} (cap ${cap:.0f}); templates pending approval")
    return plan


def approval_pr(echo=print) -> dict:
    """Put the cycle's templates and budget in a pull request. The PR's copy of the plan
    says 'approved'; main keeps 'pending' until a human merges it."""
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path
    plan = _load("campaign_plan.json", None)
    if not plan:
        raise SystemExit("Run campaign first")
    approved = copy.deepcopy(plan)
    approved["status"] = "approved"
    for c in approved["campaigns"]:
        c["approval"] = "approved_by_merge"
    md = [f"# Growth cycle {plan['cycle']}: campaign templates for approval", "",
          "Merging this pull request approves every template and the budget below. Nothing is sent or bought before that.",
          f"Spend this cycle: ${plan['total_budget_usd']:.2f} (daily cap ${guardrails()['max_daily_ad_spend_usd']}, "
          f"hackathon cap ${guardrails()['max_total_hackathon_spend_usd']}, max {guardrails()['max_outreach_emails_per_day']} emails/day).", ""]
    for c in plan["campaigns"]:
        md += [f"## {c['segment_id']} · ${c['budget_usd']:.2f} · {c['ad']['channel']}", "",
               f"Landing: {c['landing_url']}", "", f"**Headline:** {c['landing']['headline']}  ", f"**Subhead:** {c['landing']['subhead']}", ""]
        md += [f"- {b}" for b in c["landing"]["bullets"]]
        md += ["", f"**Outreach email:** {c['outreach_email']['subject']}", "", "```", c["outreach_email"]["body"], "```", "",
               f"**Ad ({c['ad']['channel']}):** {c['ad']['headline']} | {c['ad']['text']}", ""]
    branch = f"growth/cycle-{plan['cycle']}-{datetime.now(timezone.utc).strftime('%H%M%S')}"
    wt = Path(tempfile.mkdtemp(prefix="growth-"))
    run = lambda *a, cwd=ROOT: subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout
    run("fetch", "origin", "main")
    run("worktree", "add", "-b", branch, str(wt), "origin/main")
    try:
        (wt / "data" / "growth").mkdir(parents=True, exist_ok=True)
        (wt / "data" / "growth" / "campaign_plan.json").write_text(json.dumps(approved, indent=1, default=float))
        (wt / "growth").mkdir(exist_ok=True)
        (wt / "growth" / f"cycle-{plan['cycle']}.md").write_text("\n".join(md) + "\n")
        run("add", "data/growth/campaign_plan.json", f"growth/cycle-{plan['cycle']}.md", cwd=wt)
        run("-c", "user.name=iacta-loop3", "-c", "user.email=loop3@users.noreply.github.com", "commit", "-m",
            f"Growth cycle {plan['cycle']}: campaign templates and budget for approval", cwd=wt)
        run("push", "origin", branch, cwd=wt)
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=ROOT, capture_output=True)
    url = f"https://github.com/sisitsky/autonomous-agents-hack/compare/main...{branch}?expand=1"
    plan["approval_pr"] = {"branch": branch, "url": url}
    _save("campaign_plan.json", plan)
    echo(f"   approval branch pushed: {url}")
    return plan["approval_pr"]


# ---------------------------------------------------------------- 3. analytics

FUNNEL = ["visits", "score_requests", "checkout_started", "purchases"]


def read_funnel(plan: dict, simulate: bool = False) -> dict:
    """Conversion by campaign: Egbe analytics when connected, else a labeled simulation."""
    cfg = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    if egbe.available() and cfg.get("analytics_app") and not simulate:
        rows = egbe.campaigns(cfg["analytics_app"]).get("data", [])
        out = {}
        for c in plan["campaigns"]:
            r = next((x for x in rows if x.get("utm_campaign") == c["utm_campaign"]), {}) if isinstance(rows, list) else {}
            out[c["utm_campaign"]] = {"visits": r.get("campaign-visit", r.get("visits", 0)),
                                      "score_requests": r.get("signup_submitted", 0),
                                      "checkout_started": r.get("checkout_started", 0), "purchases": r.get("purchase", 0),
                                      "spend_usd": c["budget_usd"]}
        return {"source": "egbe_analytics", "simulated": False, "by_campaign": out}
    if not simulate:
        return {"source": "none", "simulated": False, "by_campaign": {}}
    rng = np.random.default_rng(plan["cycle"])
    out = {}
    for c in plan["campaigns"]:
        rs = next((s for s in _load("segments.json", {"segments": []})["segments"] if s["id"] == c["segment_id"]), None)
        pull = 0.6 + (rs["score"]["risk_score"] / 100 if rs else 0.3)        # riskier segments convert better (hidden truth)
        visits = int(rng.poisson(max(1, c["budget_usd"]) * 9))
        score = int(rng.binomial(visits, min(0.9, 0.12 * pull)))
        chk = int(rng.binomial(score, min(0.9, 0.18 * pull)))
        buy = int(rng.binomial(chk, 0.45))
        out[c["utm_campaign"]] = {"visits": visits, "score_requests": score, "checkout_started": chk, "purchases": buy, "spend_usd": c["budget_usd"]}
    return {"source": "simulated", "simulated": True, "by_campaign": out}


def analytics(simulate: bool = False, echo=print) -> dict:
    plan = _load("campaign_plan.json", None)
    if not plan:
        raise SystemExit("Run campaign first")
    f = read_funnel(plan, simulate)
    rows = []
    for c in plan["campaigns"]:
        m = f["by_campaign"].get(c["utm_campaign"])
        if not m:
            continue
        rows.append({"segment_id": c["segment_id"], "utm_campaign": c["utm_campaign"], "channel": c["ad"]["channel"], **m,
                     "visit_to_score": m["score_requests"] / m["visits"] if m["visits"] else None,
                     "score_to_checkout": m["checkout_started"] / m["score_requests"] if m["score_requests"] else None,
                     "checkout_to_purchase": m["purchases"] / m["checkout_started"] if m["checkout_started"] else None,
                     "cost_per_purchase": m["spend_usd"] / m["purchases"] if m["purchases"] else None,
                     "revenue_usd": m["purchases"] * FULL_REPORT_PRICE})
    result = {"cycle": plan["cycle"], "run_at": _now(), "source": f["source"], "simulated": f["simulated"], "rows": rows}
    if not rows:
        echo("   no conversion data yet (connect Egbe analytics, or run with --simulate for the demo)")
        _save("analytics.json", result)
        return result
    for r in rows:
        echo(f"   {r['segment_id']:<36} visits {r['visits']:>4} → scores {r['score_requests']:>3} → checkout {r['checkout_started']:>3} → buy {r['purchases']:>2}")
    # Rewrite the playbook (targeting and messaging only; never price).
    old = PLAYBOOK.read_text() if PLAYBOOK.exists() else ""
    new = llm.text(
        "You are the analytics agent for iacta.ai. Rewrite the growth playbook for the next cycle from the conversion data. "
        "Keep the same Markdown structure: 'Current hypothesis (cycle N)', 'Limits', and the 'Cycle log' table (append one row "
        "per campaign this cycle). Shift budget toward segments and channels that convert; state what to test next. Growth data "
        "may change targeting and messaging only, never price. " + ("The data is SIMULATED; say so in the log." if f["simulated"] else ""),
        f"<current_playbook>\n{old}\n</current_playbook>\n<conversion_data>\n{json.dumps(rows, default=float)}\n</conversion_data>",
        model=MODEL_FAST, max_tokens=6000)
    PLAYBOOK.write_text(new.strip() + "\n")
    past = _load("cycles.json", [])
    past = [c for c in past if c["cycle"] != plan["cycle"]] + [{"cycle": plan["cycle"], "run_at": _now(), "simulated": f["simulated"],
             "spend_usd": sum(r["spend_usd"] for r in rows), "purchases": sum(r["purchases"] for r in rows),
             "revenue_usd": sum(r["revenue_usd"] for r in rows)}]
    _save("cycles.json", past)
    _save("analytics.json", result)
    echo("   playbook rewritten for the next cycle")
    return result


# ---------------------------------------------------------------- export

def export() -> dict:
    cfg = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    segs = _load("segments.json", {})
    out = {"updated_at": _now(), "egbe_connected": egbe.available(),
           "checkout_url": cfg.get("checkout_url"), "analytics_app": cfg.get("analytics_app"),
           "checkout_status": cfg.get("checkout_status"),
           "stripe": {k: cfg["stripe"].get(k) for k in ("mode", "plan_key", "unit_amount_cents", "currency")} if cfg.get("stripe") else None,
           "full_report_price": FULL_REPORT_PRICE, "guardrails": guardrails(),
           "segments": [{k: v for k, v in s.items()} for s in segs.get("segments", [])],
           "research_via": segs.get("via"), "research_at": segs.get("run_at"),
           "plan": _load("campaign_plan.json", None), "analytics": _load("analytics.json", None),
           "cycles": _load("cycles.json", []), "playbook": PLAYBOOK.read_text() if PLAYBOOK.exists() else ""}
    try:
        out["wallet"] = egbe.wallet() if egbe.available() else None
    except Exception as e:  # noqa: BLE001
        out["wallet"] = {"error": str(e)[:200]}
    PUBLIC.write_text(json.dumps(out, default=float))
    return out


def main():
    ap = argparse.ArgumentParser(prog="agents.growth.loop3")
    ap.add_argument("step", choices=["research", "rescore", "campaign", "approve-pr", "analytics", "all", "export"])
    ap.add_argument("--simulate", action="store_true", help="Simulated conversion data (labeled) when Egbe analytics isn't connected")
    a = ap.parse_args()
    print(f"Loop 3 ({'Egbe connected' if egbe.available() else 'Egbe not connected'})")
    if a.step in ("research", "all"):
        print("1. Research"); research()
    if a.step == "rescore":
        print("Rescore"); rescore()
    if a.step in ("campaign", "all"):
        print("2. Campaign"); campaign(); approval_pr()
    if a.step == "approve-pr":
        approval_pr()
    if a.step in ("analytics", "all"):
        print("3. Analytics"); analytics(a.simulate)
    export()
    print(f"Wrote {PUBLIC.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
