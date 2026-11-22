"""Single place for runtime-agent configuration (model names, paths)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path = ROOT / ".env") -> None:
    """Load KEY=VALUE lines from the repo's .env (gitignored) without overriding
    variables already set in the environment. Never prints values."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.removeprefix("export ").strip()
        os.environ.setdefault(key, value.strip().strip("'\""))


_load_dotenv()
RUNS_DIR = ROOT / "agents" / "runs"
SUITES_DIR = ROOT / "agents" / "test_suites"

# Model choices from CLAUDE.md: Sonnet for speed, Opus for calibration reasoning.
MODEL_FAST = os.environ.get("UNDERWRITER_MODEL_FAST", "claude-sonnet-5-5")
MODEL_REASONING = os.environ.get("UNDERWRITER_MODEL_REASONING", "claude-opus-5-5")


def llm_available() -> bool:
    """True when the Anthropic SDK can find credentials.

    Every agent has a deterministic offline path so the loop stays demoable
    without a key; the LLM path is used when credentials exist.
    """
    if os.environ.get("UNDERWRITER_OFFLINE") == "1":
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
