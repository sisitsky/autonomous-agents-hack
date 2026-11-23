"""Red-team target: Claude behind the red team's simple chat contract.

Wraps the Anthropic Messages API as POST /chat {"message": ...} -> {"reply": ...},
so the red-team agent can test Claude (your own API key, billed to you). Binds to
127.0.0.1, which the red-team CLI may test without --authorized.

    python -m agents.target_bot.claude_proxy                       # plain Claude, no system prompt
    python -m agents.target_bot.claude_proxy --system-file prompt.txt --model claude-opus-5-5

This tests the model through the API, optionally with a system prompt you supply.
It is not the claude.ai product, which adds its own system prompt, tools and
safeguards.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from agents.config import MODEL_FAST, llm_available
from agents.target_bot.shopbot import ThreadingHTTPServer, make_handler


def make_reply(model: str, system: str):
    from agents.llm import client

    def reply(message: str) -> str:
        kw = {"system": system} if system else {}
        r = client().messages.create(model=model, max_tokens=1500,
                                     messages=[{"role": "user", "content": message}], **kw)
        if r.stop_reason == "refusal":
            return "[declined]"
        return "".join(b.text for b in r.content if b.type == "text")
    return reply


def main():
    ap = argparse.ArgumentParser(prog="agents.target_bot.claude_proxy")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--model", default=MODEL_FAST)
    ap.add_argument("--system-file", help="Optional system prompt for the system under test")
    a = ap.parse_args()
    if not llm_available():
        raise SystemExit("Set ANTHROPIC_API_KEY (in .env) first.")
    system = Path(a.system_file).read_text() if a.system_file else ""
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(make_reply(a.model, system)))
    print(f"Claude proxy ({a.model}{', custom system prompt' if system else ''}) on http://127.0.0.1:{a.port}/chat")
    srv.serve_forever()


if __name__ == "__main__":
    main()
