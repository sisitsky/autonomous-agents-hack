"""Thin wrapper around the Anthropic SDK used by all runtime agents."""
from __future__ import annotations

import json

import anthropic

from .config import MODEL_FAST

_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


class LLMError(RuntimeError):
    pass


def _create(model: str, system: str, user, max_tokens: int, effort: str, **extra):
    """`user` is a string or a list of content blocks (text, document)."""
    resp = client().beta.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort, **extra.pop("output_config", {})},
        # Server-side refusal fallback, routed by refusal category.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        **extra,
    )
    if resp.stop_reason == "refusal":
        cat = resp.stop_details.category if resp.stop_details else None
        raise LLMError(f"Model declined the request (category: {cat})")
    if resp.stop_reason == "max_tokens":
        raise LLMError("Response hit max_tokens before finishing")
    return resp


def text(system: str, user: str, model: str = MODEL_FAST, max_tokens: int = 8000,
         effort: str = "low") -> str:
    resp = _create(model, system, user, max_tokens, effort)
    return "".join(b.text for b in resp.content if b.type == "text")


def structured(system: str, user, schema: dict, model: str = MODEL_FAST,
               max_tokens: int = 8000, effort: str = "low") -> dict:
    """Return JSON guaranteed to match `schema` (structured outputs)."""
    resp = _create(model, system, user, max_tokens, effort,
                   output_config={"format": {"type": "json_schema", "schema": schema}})
    raw = next(b.text for b in resp.content if b.type == "text")
    return json.loads(raw)


def web_research(system: str, user: str, model: str = MODEL_FAST, max_uses: int = 6) -> str:
    """Text answer grounded in Claude's server-side web search (used when Egbe research
    isn't connected). Handles pause_turn by continuing the turn."""
    messages = [{"role": "user", "content": user}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": max_uses}]
    for _ in range(4):
        resp = client().messages.create(model=model, max_tokens=16000, system=system, messages=messages,
                                        tools=tools, output_config={"effort": "medium"})
        if resp.stop_reason == "refusal":
            raise LLMError("Model declined the research request")
        if resp.stop_reason != "pause_turn":
            return "".join(b.text for b in resp.content if b.type == "text")
        messages.append({"role": "assistant", "content": resp.content})
    raise LLMError("Research did not finish")
