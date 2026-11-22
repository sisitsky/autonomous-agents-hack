"""Guardrail checks run in CI on every pull request that touches params/.

  python -m engine.guardrails --baseline base.json --candidate cand.json \
      --changed-files "params/priors.yaml engine/model.py"

Exits non-zero if any guardrail fails. Prints a Markdown report suitable for a
PR comment. Agents may never edit this file (see params/guardrails.yaml).
"""
from __future__ import annotations

import argparse
import json
import sys

from . import distributions as D
from .model import load_params

PROB_KEYS = ("p_error", "p_harm_given_error", "p_claim_given_harm", "prob",
             "rate", "annual_prob")
SHARE_KEYS = ("share_affected", "loss_share_of_limit")


def check_bounds(params: dict) -> list[str]:
    g = params["guardrails"]["parameter_bounds"]
    problems = []

    def walk(node, path):
        nested = isinstance(node, dict) and any(D.is_dist(v) for v in node.values())
        if D.is_dist(node) and not nested:
            key = path.split(".")[-1]
            hi = D.quantile(node, 0.999)
            lo = D.quantile(node, 0.001)
            if key in PROB_KEYS and (lo < g["probability"]["min"] or hi > g["probability"]["max"]):
                problems.append(f"`{path}` ranges {lo:.4g} to {hi:.4g}, outside probability bounds")
            if key in SHARE_KEYS and (lo < g["share"]["min"] or hi > g["share"]["max"]):
                problems.append(f"`{path}` ranges {lo:.4g} to {hi:.4g}, outside share bounds")
            if key == "alpha" and (lo < g["pareto_alpha"]["min"] or hi > g["pareto_alpha"]["max"]):
                problems.append(f"`{path}` ranges {lo:.3g} to {hi:.3g}, outside Pareto alpha bounds")
            return
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k)

    walk(params["priors"], "")
    return problems


def check_changes(baseline: dict, candidate: dict, params: dict) -> tuple[list[str], list[str]]:
    g = params["guardrails"]["pricing"]
    problems, rows = [], []
    for ins_id, cand in candidate["insureds"].items():
        base = baseline["insureds"].get(ins_id)
        if not base:
            continue
        change = cand["gross_premium"] / base["gross_premium"] - 1
        flag = "FAIL" if abs(change) > g["max_benchmark_premium_change"] else "ok"
        rows.append(f"| {ins_id} | ${base['gross_premium']:,.0f} | ${cand['gross_premium']:,.0f} "
                    f"| {change:+.1%} | {flag} |")
        if flag == "FAIL":
            problems.append(f"{ins_id} premium moves {change:+.1%} "
                            f"(limit ±{g['max_benchmark_premium_change']:.0%})")
    book = candidate["book_premium"] / baseline["book_premium"] - 1
    rows.append(f"| **book** | ${baseline['book_premium']:,.0f} | ${candidate['book_premium']:,.0f} "
                f"| {book:+.1%} | {'FAIL' if abs(book) > g['max_book_premium_change'] else 'ok'} |")
    if abs(book) > g["max_book_premium_change"]:
        problems.append(f"Book premium moves {book:+.1%} (limit ±{g['max_book_premium_change']:.0%})")
    return problems, rows


def check_protected(changed: list[str], params: dict) -> list[str]:
    protected = set(params["guardrails"]["pricing"]["protected_files"])
    return [f"Protected file changed: `{f}` (human-only)" for f in changed if f in protected]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--changed-files", default="")
    ap.add_argument("--allow-protected", action="store_true",
                    help="Set by CI only when the PR author is a human, not an agent.")
    a = ap.parse_args()

    params = load_params()
    with open(a.baseline) as f:
        baseline = json.load(f)
    with open(a.candidate) as f:
        candidate = json.load(f)

    problems = check_bounds(params)
    change_problems, rows = check_changes(baseline, candidate, params)
    problems += change_problems
    if not a.allow_protected:
        problems += check_protected(a.changed_files.split(), params)

    print("## Guardrail report\n")
    print("| Insured | Current | Proposed | Change | Check |\n|---|---|---|---|---|")
    print("\n".join(rows))
    if problems:
        print("\n**Guardrails failed:**\n" + "\n".join(f"- {p}" for p in problems))
        sys.exit(1)
    print("\nAll guardrails passed. A human must still approve and merge.")


if __name__ == "__main__":
    main()
