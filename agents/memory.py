"""Submission memory: the same documents get the same intake profile and red-team tests.

A memory is keyed by the documents' content (SHA-256 of each file's bytes, sorted and
hashed together), so re-uploading AnthropicIPO.pdf, under any name, finds it.

    {"key", "files": [{"name", "sha256"}], "saved_at", "target", "profile", "suite"}

Where memories live:
  - data/memory/<key>.json in the repo: shared by every device and deploy ("pinned").
  - The browser's localStorage: every live run is remembered automatically in that
    browser and sent back with the next run of the same documents.

    python -m agents.memory key AnthropicIPO.pdf          # print a file set's key
    python -m agents.memory pin memory-<key>.json         # pin a memory downloaded from the web app
    python -m agents.memory list
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT

MEM_DIR = ROOT / "data" / "memory"
KEY_RE = re.compile(r"^[0-9a-f]{64}$")
TARGETS = {"none", "endpoint", "deterministic", "llm", "claude", "twin"}


def doc_key(file_hashes: list[str]) -> str:
    hs = sorted(h.lower() for h in file_hashes)
    if not hs or not all(KEY_RE.match(h) for h in hs):
        raise ValueError("file hashes must be SHA-256 hex digests")
    return hashlib.sha256("\n".join(hs).encode()).hexdigest()


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def valid_key(key) -> bool:
    return isinstance(key, str) and bool(KEY_RE.match(key))


def get(key: str) -> dict | None:
    """A pinned memory from the repo, or None."""
    if not valid_key(key):
        return None
    p = MEM_DIR / f"{key}.json"
    return json.loads(p.read_text()) if p.exists() else None


def check_record(rec: dict) -> dict:
    """Validate a memory record's shape (from the browser or a downloaded file)."""
    if not isinstance(rec, dict) or not valid_key(rec.get("key")):
        raise ValueError("memory needs a 64-hex `key`")
    prof, suite = rec.get("profile"), rec.get("suite")
    if prof is not None and not (isinstance(prof, dict) and "use_case" in prof and "system_name" in prof):
        raise ValueError("memory profile is not an intake profile")
    if suite is not None:
        if not (isinstance(suite, dict) and isinstance(suite.get("cases"), list) and suite.get("categories")):
            raise ValueError("memory suite is not a red-team suite")
        if len(suite["cases"]) > 60:
            raise ValueError("memory suite has too many cases")
        for c in suite["cases"]:
            if not (isinstance(c, dict) and c.get("id") and c.get("input") and c.get("rubric")
                    and c.get("category") in suite["categories"]):
                raise ValueError("memory suite has a malformed case")
    if rec.get("target") not in (None, *TARGETS):
        raise ValueError("unknown red-team target in memory")
    return rec


def pin(rec: dict) -> Path:
    rec = check_record(dict(rec))
    rec.setdefault("saved_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    MEM_DIR.mkdir(parents=True, exist_ok=True)
    p = MEM_DIR / f"{rec['key']}.json"
    p.write_text(json.dumps(rec, indent=1, ensure_ascii=False))
    return p


def main():
    ap = argparse.ArgumentParser(prog="agents.memory")
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("key"); k.add_argument("files", nargs="+")
    pn = sub.add_parser("pin"); pn.add_argument("record", help="memory JSON downloaded from the web app")
    sub.add_parser("list")
    a = ap.parse_args()
    if a.cmd == "key":
        print(doc_key([file_sha256(f) for f in a.files]))
    elif a.cmd == "pin":
        print(f"Pinned {pin(json.loads(Path(a.record).read_text()))}")
    else:
        for p in sorted(MEM_DIR.glob("*.json")):
            r = json.loads(p.read_text())
            names = ", ".join(f["name"] for f in r.get("files", []))
            print(f"{r['key'][:12]}  {names}  target={r.get('target')}  "
                  f"cases={len((r.get('suite') or {}).get('cases', []))}  saved {r.get('saved_at')}")


if __name__ == "__main__":
    main()
