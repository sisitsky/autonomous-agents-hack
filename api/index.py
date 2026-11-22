"""Web API for the AI Underwriter (Vercel Python function, FastAPI).

Each Loop 1 step is its own endpoint so the browser can run the loop step by
step and show progress. All of them wrap the same agent code the CLI uses.

    GET  /api/health
    POST /api/intake     documents -> risk profile + insured           (Claude; access code)
    POST /api/redteam    insured -> red-team results on a demo ShopBot (Claude judge; access code)
    POST /api/price      insured -> calculation archive                (deterministic)
    POST /api/explain    calculation -> checked narrative              (Claude; access code)
    POST /api/chat       talk to a demo ShopBot                        (llm bot needs access code)

Steps that spend Claude API credit need the DEMO_ACCESS_CODE when it is set.
Only our own demo bots can be red-teamed from the web.
"""
from __future__ import annotations

import hmac
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from agents import explainer, intake, pricer, redteam  # noqa: E402
from agents.config import llm_available  # noqa: E402
from agents.target_bot import shopbot  # noqa: E402
from engine.model import load_params  # noqa: E402

app = FastAPI(title="AI Underwriter API", docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.middleware("http")
async def _restore_path(request: Request, call_next):
    """vercel.json rewrites /api/<x> to /api/index?__path=<x>; restore /api/<x> for routing."""
    if request.url.path == "/api/index":
        sub = request.query_params.get("__path")
        if sub is not None:
            request.scope["path"] = "/api/" + sub.lstrip("/")
    return await call_next(request)
BOTS = {"deterministic", "llm"}


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
    _require_code(body); _require_llm()
    return _call(intake.analyze, body.get("documents") or [], body.get("description"), body.get("insured_id"))


@app.post("/api/redteam")
async def api_redteam(request: Request):
    body = await request.json()
    _require_code(body); _require_llm()
    bot = body.get("bot", "deterministic")
    if bot not in BOTS:
        raise HTTPException(400, "From the web, only the demo ShopBots can be tested")
    ev = _call(redteam.evaluate, _insured(body), body.get("profile") or {}, redteam.demo_transport(bot),
                          f"demo ShopBot ({bot})", scorer="judge", workers=10)
    return {"summary": ev["summary"], "cases": ev["results"], "insured": ev["insured"]}


@app.post("/api/price")
async def api_price(request: Request):
    body = await request.json()
    r = _call(pricer.compute, _insured(body), with_tornado=body.get("tornado", True),
                       with_controls=body.get("controls", True))
    return {"calculation": r["calculation"]}


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

