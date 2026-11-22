"""Single place for runtime-agent configuration (model names, paths)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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
