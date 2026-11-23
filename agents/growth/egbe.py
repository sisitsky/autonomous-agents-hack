"""Minimal Egbe client (local-project mode).

After `/egbe:egbe-setup` in Claude Code, the project's `.env.local` holds OS_URL
(https://os.hz.egbe.dev) and OS_API_KEY; requests send `Authorization: Bearer
$OS_API_KEY`. Paths follow the Egbe skills: /research, /analytics, /wallet, /payments.
Everything is optional: without a key, Loop 3 runs on its own fallbacks and says so.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request


def available() -> bool:
    return bool(os.environ.get("OS_API_KEY"))


def _base() -> str:
    return os.environ.get("OS_URL", "https://os.hz.egbe.dev").rstrip("/")


def request(method: str, path: str, body: dict | None = None, query: dict | None = None,
            idempotency_key: str | None = None, timeout: float = 30) -> dict:
    url = _base() + path + ("?" + urllib.parse.urlencode(query) if query else "")
    headers = {"Authorization": f"Bearer {os.environ['OS_API_KEY']}", "Content-Type": "application/json",
               "User-Agent": "iacta-loop3", "X-Egbe-Agent": os.environ.get("OS_AGENT_NAME", "iacta-loop3")}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    req = urllib.request.Request(url, method=method, headers=headers, data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Egbe {method} {path}: HTTP {e.code} {e.read()[:300]!r}")


def get(path: str, **query) -> dict:
    return request("GET", path, query=query or None)


def research(query: str, instructions: str, key: str, depth: str = "standard", wait_s: int = 600) -> str:
    """Submit an Egbe research job (counts against 20/month) and poll for the markdown result."""
    job = request("POST", "/research", {"query": query, "instructions": instructions, "depth": depth,
                                        "idempotency_key": key}, idempotency_key=key)
    jid = job["data"]["id"]
    t0 = time.time()
    while time.time() - t0 < wait_s:
        r = get(f"/research/{jid}")["data"]
        if r["status"] == "completed":
            return r["result"]["markdown"]
        if r["status"] == "failed":
            raise RuntimeError(f"Egbe research failed: {r.get('error')}")
        time.sleep(10)
    raise RuntimeError("Egbe research still running; re-run to pick it up")


def wallet() -> dict:
    return get("/wallet/balance")["data"]["wallet"]


def campaigns(app: str, period: str = "all") -> dict:
    return get(f"/analytics/{app}/campaigns", period=period)


def stats(app: str, period: str = "30d") -> dict:
    return get(f"/analytics/{app}/stats", period=period)
