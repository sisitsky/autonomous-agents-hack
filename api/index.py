"""Web API for iacta.ai, the AI Underwriter (Vercel Python function, FastAPI).

Each Loop 1 step is its own endpoint so the browser can run the loop step by
step and show progress. All of them wrap the same agent code the CLI uses.

    GET  /api/health
    POST /api/intake     documents -> risk profile + insured           (Claude; access code)
    POST /api/redteam    insured -> red-team results on a demo ShopBot (fixed suite) or an authorized
                         endpoint (suite generated from the company's profile); Claude judge; access code
    POST /api/price      insured -> calculation archive                (deterministic)
    POST /api/explain    calculation -> checked narrative              (Claude; access code)
    POST /api/chat       talk to a demo ShopBot                        (llm bot needs access code)

Steps that spend Claude API credit need the DEMO_ACCESS_CODE when it is set.
From the web: our demo bots, Claude via our Anthropic key, or an authorized public endpoint.
    POST /api/memory     documents' content key -> pinned profile + red-team suite, if any
"""
from __future__ import annotations

import hmac
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from agents import explainer, intake, memory, pricer, redteam, underwriter_chat  # noqa: E402
from agents.config import llm_available  # noqa: E402
from agents.target_bot import shopbot  # noqa: E402
from engine.model import load_params  # noqa: E402

app = FastAPI(title="iacta.ai API", docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.middleware("http")
async def _restore_path(request: Request, call_next):
    """vercel.json rewrites /api/<x> to /api/index?__path=<x>; restore /api/<x> for routing."""
    if request.url.path == "/api/index":
        sub = request.query_params.get("__path")
        if sub is not None:
            request.scope["path"] = "/api/" + sub.lstrip("/")
    return await call_next(request)
BOTS = {"deterministic", "llm"}
COOLDOWN_S = 600   # minimum gap between manual Loop 2 runs


def _require_code(body: dict) -> None:
    expected = os.environ.get("DEMO_ACCESS_CODE")
    if not expected:
        return
    if not hmac.compare_digest(str(body.get("access_code") or ""), expected):
        raise HTTPException(401, "This step calls the Claude API. Enter the demo access code to run it.")


def _require_llm() -> None:
    if not llm_available():
        raise HTTPException(503, "The Claude API key is not configured on this deployment.")


def _insured(body: dict) -> dict:
    ins = body.get("insured")
    if not isinstance(ins, dict) or "perils" not in ins or "policy" not in ins:
        raise HTTPException(400, "Body needs an insured with perils and policy")
    return ins


def _call(fn, *args, **kw):
    """Agents raise SystemExit with a user-facing message; return it as a 400."""
    try:
        return fn(*args, **kw)
    except SystemExit as e:
        raise HTTPException(400, str(e))


@app.exception_handler(ValueError)
async def _valueerror(_: Request, e: ValueError):
    return JSONResponse({"detail": str(e)}, status_code=400)


@app.get("/api/health")
def health():
    p = load_params()
    return {"ok": True, "llm": llm_available(), "access_code_required": bool(os.environ.get("DEMO_ACCESS_CODE")),
            "params_versions": {k: p[k].get("version") for k in ("priors", "rating_factors", "pricing")}}


@app.post("/api/intake")
async def api_intake(request: Request):
    body = await request.json()
    _require_code(body)
    vol = body.get("outputs_per_year")
    remembered = body.get("profile")      # from submission memory: same documents -> same profile
    if remembered is not None:
        memory.check_record({"key": "0" * 64, "profile": remembered})
    else:
        _require_llm()
    r = _call(intake.analyze, body.get("documents") or [], body.get("description"), body.get("insured_id"),
              int(vol) if vol else None, body.get("plan") or None, body.get("endpoint") or None, remembered)
    r["from_memory"] = remembered is not None
    return r


@app.post("/api/memory")
async def api_memory(request: Request):
    """A pinned memory (data/memory/<key>.json) for these documents, if any. The key is
    the SHA-256 over the files' SHA-256s, computed in the browser; no documents are sent."""
    body = await request.json()
    rec = memory.get(str(body.get("key") or ""))
    return {"found": bool(rec), "memory": rec}


def _public_endpoint(url: str) -> str:
    """Only public http(s) endpoints: block loopback, private, link-local and reserved
    addresses so the red team can't be pointed at internal networks."""
    import ipaddress
    import socket
    from urllib.parse import urlparse
    u = urlparse(url or "")
    if u.scheme not in ("https", "http") or not u.hostname:
        raise HTTPException(400, "Endpoint must be an http(s) URL")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80))
    except socket.gaierror:
        raise HTTPException(400, f"Cannot resolve {u.hostname}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise HTTPException(400, "Endpoint resolves to a private or reserved address")
    return url


@app.post("/api/redteam")
async def api_redteam(request: Request):
    body = await request.json()
    _require_code(body); _require_llm()
    bot = body.get("bot", "deterministic")
    generate = False
    if bot in BOTS:
        transport, label = redteam.demo_transport(bot), f"demo ShopBot ({bot})"
    elif bot == "claude":
        from agents.config import MODEL_FAST
        transport, label = redteam.claude_transport(MODEL_FAST), f"Claude via the Anthropic API ({MODEL_FAST})"
        generate = True
    elif bot == "endpoint":
        if body.get("authorized") is not True:
            raise HTTPException(400, "Confirm that you own or are authorized to test this endpoint")
        url = _public_endpoint(str(body.get("endpoint") or ""))
        headers = {}
        if body.get("auth_header"):
            headers["Authorization"] = str(body["auth_header"])[:2000]
        transport = redteam.http_transport(url, message_field=str(body.get("message_field") or "message")[:64],
                                           reply_path=str(body.get("reply_path") or "reply")[:128], headers=headers)
        label = url
        generate = True     # tests written for this company from its profile, graded against its own policy
    else:
        raise HTTPException(400, "Unknown red-team target")
    suite = body.get("suite")             # from submission memory: rerun the exact same tests
    if suite is not None:
        memory.check_record({"key": "0" * 64, "suite": suite})
        suite = {**suite, "_path": suite.get("id") or "remembered"}
    ev = _call(redteam.evaluate, _insured(body), body.get("profile") or {}, transport,
               label, scorer="judge", workers=10, generate=generate, suite=suite)
    keep = {k: v for k, v in ev["suite"].items() if k != "_path"}
    return {"summary": ev["summary"], "cases": ev["results"], "insured": ev["insured"],
            "suite": keep if keep.get("plan") else None, "suite_from_memory": suite is not None}


@app.post("/api/price")
async def api_price(request: Request):
    body = await request.json()
    r = _call(pricer.compute, _insured(body), with_tornado=body.get("tornado", True),
                       with_controls=body.get("controls", True), plan=body.get("plan"))
    return {"calculation": r["calculation"]}


@app.post("/api/simulate")
async def api_simulate(request: Request):
    """Re-run the Monte Carlo with a fresh seed (the official quote stays on seed 42)."""
    import random
    from engine.model import apply_plan, price
    body = await request.json()
    params = load_params()
    ins = apply_plan(_insured(body), params, body.get("plan"))
    seed = int(body.get("seed") or random.randint(1, 2**31 - 1))
    q = _call(price, ins, params, seed=seed, illustrate=int(min(max(body.get("lines", 120), 10), 200)))
    il = q["illustration"]
    return {"seed": seed, "expected_loss": q["expected_loss"], "gross_premium": q["gross_premium"],
            "rate_on_line": q["rate_on_line"], "uncertainty_load": q["uncertainty_load"],
            "capital_load": q["capital_load"], "loss_percentiles": q["loss_percentiles"],
            "standard_error": il["convergence"]["se"][-1],
            "calculation_like": {"simulation_illustration": il,
                                 "loss_distribution": {"histogram": q["trace"]["loss_histogram"]},
                                 "meta": {"simulation": q["trace"]["simulation"]}}}


@app.post("/api/explain")
async def api_explain(request: Request):
    body = await request.json()
    _require_code(body); _require_llm()
    calc = body.get("calculation")
    if not isinstance(calc, dict) or "premium_build_up" not in calc:
        raise HTTPException(400, "Body needs a calculation from /api/price")
    notes = {"review_notes": body.get("review_notes") or [],
             "open_questions": (body.get("profile") or {}).get("open_questions", [])}
    rt = body.get("redteam") or {}
    r = explainer.compose(calc, notes, rt.get("summary"), rt.get("cases"), body.get("previous"))
    return {"narrative": r["narrative"], "check": r["check"], "facts": r["facts"]}


@app.post("/api/chat")
async def api_chat(request: Request):
    body = await request.json()
    bot = body.get("bot", "deterministic")
    msg = str(body.get("message") or "")[:2000]
    if bot not in BOTS:
        raise HTTPException(400, "Unknown bot")
    if bot == "llm":
        _require_code(body); _require_llm()
        return {"reply": shopbot.llm_reply(msg)}
    return {"reply": shopbot.deterministic_reply(msg)}



@app.post("/api/loop2/run")
async def api_loop2_run(request: Request):
    """Start the Loop 2 workflow now (GitHub workflow_dispatch). The hourly schedule
    is unaffected; this just adds a run. Needs GITHUB_DISPATCH_TOKEN (a fine-grained
    token with Actions: read and write on this repository) in the Vercel environment."""
    import json as _json
    import urllib.error
    import urllib.request
    tok = os.environ.get("GITHUB_DISPATCH_TOKEN")
    if not tok:
        raise HTTPException(503, "Run-now is not configured: add GITHUB_DISPATCH_TOKEN in the Vercel project settings.")
    repo = os.environ.get("GITHUB_REPOSITORY", "sisitsky/autonomous-agents-hack")
    # No access code: instead, a cooldown caps how often anyone can start a run.
    from datetime import datetime, timezone
    gh = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json", "User-Agent": "ai-underwriter"}
    try:
        with urllib.request.urlopen(urllib.request.Request(
                f"https://api.github.com/repos/{repo}/actions/workflows/calibrate.yml/runs?per_page=1", headers=gh), timeout=15) as r:
            runs = _json.loads(r.read()).get("workflow_runs", [])
    except urllib.error.URLError:
        runs = []
    if runs:
        last = runs[0]
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(last["created_at"].replace("Z", "+00:00"))).total_seconds()
        if last["status"] != "completed":
            raise HTTPException(429, f"Loop 2 is already running ({last['status'].replace('_', ' ')}). Follow it: {last['html_url']}")
        if age < COOLDOWN_S:
            raise HTTPException(429, f"Loop 2 ran {int(age // 60)} min ago; the next manual run is available in "
                                     f"{int((COOLDOWN_S - age) // 60) + 1} min (hourly runs continue automatically).")
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/workflows/calibrate.yml/dispatches",
                                 method="POST", data=_json.dumps({"ref": "main"}).encode(),
                                 headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
                                          "User-Agent": "ai-underwriter"})
    try:
        urllib.request.urlopen(req, timeout=20)
    except urllib.error.HTTPError as e:
        raise HTTPException(502, f"GitHub refused the dispatch ({e.code}): check the token's Actions permission.")
    return {"ok": True, "actions_url": f"https://github.com/{repo}/actions/workflows/calibrate.yml"}


@app.post("/api/underwriter/chat")
async def api_underwriter_chat(request: Request):
    """Answer open questions: Claude proposes validated changes to this insured,
    the deterministic engine re-prices, and the new quote comes back."""
    body = await request.json()
    _require_code(body); _require_llm()
    ins = _insured(body)
    msg = str(body.get("message") or "").strip()
    if not msg:
        raise HTTPException(400, "Write an answer or a question for the underwriting agent")
    r = _call(underwriter_chat.chat, ins, body.get("profile") or {}, body.get("open_questions") or [],
              body.get("history") or [], msg)
    calc = _call(pricer.compute, r["insured"], plan=body.get("plan"))["calculation"] if r["changes"] else None
    return {**r, "calculation": calc}


@app.post("/api/score")
async def api_score(request: Request):
    """Free AI risk score for the Loop 3 landing page: a short form priced by the
    deterministic engine (no Claude call, so no access code). Indicative only."""
    from engine.model import apply_plan, price
    body = await request.json()
    params = load_params()
    f = params["rating_factors"]["factors"]
    rt = {"wrong_information", "unauthorized_commitment", "data_leakage", "prompt_injection"}
    vol = int(min(max(int(body.get("outputs_per_year") or 250_000), 1_000), 50_000_000))
    perils = [p for p in (body.get("perils") or []) if p in params["priors"]["perils"]] or sorted(rt)
    factors = {k: v for k, v in (body.get("factors") or {}).items() if k in f and v in f[k]["levels"]}
    ins = {"id": "score", "perils": {p: ({"outputs_per_year": vol} if p in rt else {}) for p in perils}, "factors": factors}
    plans = {}
    for pid, plan in params["plans"]["plans"].items():
        i = apply_plan(ins, params, pid)
        if not i["perils"]:
            continue
        q = price(i, params, n_outer=400, n_inner=200)
        plans[pid] = {"name": plan["name"], "premium": q["gross_premium"], "rate_on_line": q["rate_on_line"],
                      "limits": plan["policy"], "covers": list(i["perils"])}
    rol = (plans.get("professional") or next(iter(plans.values())))["rate_on_line"]
    score = int(round(min(100, max(1, rol * 2500))))
    q = price(apply_plan(ins, params, "enterprise"), params, n_outer=400, n_inner=200)
    drivers = sorted(((p, d["expected_loss"]) for p, d in q["by_peril"].items()), key=lambda x: -x[1])[:3]
    return {"risk_score": score, "grade": "A" if score < 25 else "B" if score < 45 else "C" if score < 65 else "D" if score < 85 else "E",
            "plans": plans, "top_drivers": [{"peril": p, "expected_loss": v} for p, v in drivers],
            "note": "Indicative, untested (no red-team test: portfolio-average failure rates). Not an offer of insurance."}
