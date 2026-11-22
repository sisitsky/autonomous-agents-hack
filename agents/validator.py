"""Validation agent (Loop 2, step 3): re-price, check guardrails, accumulation.

- Re-prices the fixed benchmark book with the current and the candidate
  parameters and runs the guardrail checks (engine/guardrails.py): no benchmark
  premium moves more than 15%, the book no more than 8%, every parameter inside
  its bounds, no protected file touched.
- Recalculates portfolio accumulation by model provider (benchmarks plus the
  quoted demo insureds): limits, expected loss, systemic loss and capital, and
  premium per provider, before and after.
"""
from __future__ import annotations

import json
from pathlib import Path

from engine.cli import benchmark
from engine.guardrails import check_bounds, check_changes, check_protected
from engine.model import PARAMS_DIR, load_params, price

from .config import ROOT

BUNDLES = ROOT / "public" / "data" / "runs"


def portfolio_insureds() -> list[dict]:
    import yaml
    book = yaml.safe_load((ROOT / "benchmarks" / "insureds.yaml").read_text())["insureds"]
    out = [dict(i, _source="benchmark") for i in book]
    for f in sorted(BUNDLES.glob("*.json")):
        if f.name == "index.json":
            continue
        b = json.loads(f.read_text())
        ins = (b.get("calculation") or {}).get("insured")
        if ins:
            out.append(dict(ins, id=b["id"], _source="quoted"))
    return out


def accumulation(params_dir: Path | str) -> dict:
    params = load_params(params_dir)
    rows: dict[str, dict] = {}
    for ins in portfolio_insureds():
        q = price({k: v for k, v in ins.items() if k != "_source"}, params, n_outer=500, n_inner=200)
        prov = ins.get("provider") or "unmapped"
        r = rows.setdefault(prov, {"provider": prov, "policies": 0, "aggregate_limits": 0.0, "expected_loss": 0.0,
                                   "systemic_expected_loss": 0.0, "systemic_capital": 0.0, "premium": 0.0})
        r["policies"] += 1
        r["aggregate_limits"] += ins["policy"]["aggregate_limit"]
        r["expected_loss"] += q["expected_loss"]
        r["systemic_expected_loss"] += q["expected_systemic_loss"]
        r["systemic_capital"] += q["capital"]["systemic"]
        r["premium"] += q["gross_premium"]
    total_lim = sum(r["aggregate_limits"] for r in rows.values()) or 1
    for r in rows.values():
        r["limit_share"] = r["aggregate_limits"] / total_lim
    return {"by_provider": sorted(rows.values(), key=lambda r: -r["aggregate_limits"]),
            "policies": sum(r["policies"] for r in rows.values())}


def validate(candidate_dir: Path | str, changed_files: list[str] | None = None) -> dict:
    base = benchmark(str(PARAMS_DIR))
    cand = benchmark(str(candidate_dir))
    cparams = load_params(candidate_dir)
    problems = check_bounds(cparams)
    change_problems, rows = check_changes(base, cand, cparams)
    problems += change_problems + check_protected(changed_files or ["params/priors.yaml"], cparams)
    table = []
    for ins_id, c in cand["insureds"].items():
        b = base["insureds"][ins_id]
        table.append({"insured": ins_id, "current": b["gross_premium"], "proposed": c["gross_premium"],
                      "change": c["gross_premium"] / b["gross_premium"] - 1})
    table.append({"insured": "book", "current": base["book_premium"], "proposed": cand["book_premium"],
                  "change": cand["book_premium"] / base["book_premium"] - 1})
    g = cparams["guardrails"]["pricing"]
    return {"passed": not problems, "problems": problems, "benchmark": table,
            "limits": {"max_benchmark_premium_change": g["max_benchmark_premium_change"],
                       "max_book_premium_change": g["max_book_premium_change"]},
            "markdown_rows": rows,
            "accumulation": {"before": accumulation(PARAMS_DIR), "after": accumulation(candidate_dir)}}
