"""Command line entry points.

  python -m engine.cli quote shopbot              price one benchmark insured
  python -m engine.cli quote path/to/insured.yaml price an insured from a file
  python -m engine.cli tornado shopbot            sensitivity analysis
  python -m engine.cli benchmark --out out.json   price the whole benchmark book
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from .model import load_params, price
from .tornado import tornado

BENCHMARKS = Path(__file__).resolve().parent.parent / "benchmarks" / "insureds.yaml"


def load_insured(ref: str) -> dict:
    p = Path(ref)
    if p.suffix in (".yaml", ".yml", ".json") and p.exists():
        with open(p) as f:
            return yaml.safe_load(f)
    with open(BENCHMARKS) as f:
        for ins in yaml.safe_load(f)["insureds"]:
            if ins["id"] == ref:
                return ins
    raise SystemExit(f"No insured '{ref}' in {BENCHMARKS}")


def benchmark(params_dir: str | None = None) -> dict:
    params = load_params(params_dir) if params_dir else load_params()
    with open(BENCHMARKS) as f:
        book = yaml.safe_load(f)["insureds"]
    results = {ins["id"]: price(ins, params) for ins in book}
    return {
        "priors_version": params["priors"]["version"],
        "book_premium": sum(r["gross_premium"] for r in results.values()),
        "insureds": {k: {"gross_premium": v["gross_premium"],
                         "expected_loss": v["expected_loss"],
                         "rate_on_line": v["rate_on_line"]} for k, v in results.items()},
    }


def _money(x):
    return f"${x:,.0f}"


def main():
    ap = argparse.ArgumentParser(prog="engine")
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("quote"); q.add_argument("insured"); q.add_argument("--json", action="store_true")
    t = sub.add_parser("tornado"); t.add_argument("insured"); t.add_argument("--json", action="store_true")
    b = sub.add_parser("benchmark"); b.add_argument("--out"); b.add_argument("--params-dir")
    a = ap.parse_args()

    if a.cmd == "quote":
        r = price(load_insured(a.insured))
        if a.json:
            print(json.dumps(r, indent=2)); return
        print(f"Quote for {r['insured_id']}")
        for peril, d in r["by_peril"].items():
            print(f"  {peril:<20} claims/yr {d['expected_claims']:.3f}  "
                  f"loss {_money(d['expected_loss'])}  modifier x{d['modifier']:.2f}")
        print(f"  Expected loss (after limits)  {_money(r['expected_loss'])}")
        print(f"  Parameter-uncertainty load    {_money(r['uncertainty_load'])}")
        print(f"  Capital load                  {_money(r['capital_load'])}  "
              f"(capital {_money(r['capital']['total'])})")
        print(f"  Fixed expense                 {_money(r['fixed_expense'])}")
        print(f"  Gross premium                 {_money(r['gross_premium'])}  "
              f"(rate on line {r['rate_on_line']:.1%})")
    elif a.cmd == "tornado":
        r = tornado(load_insured(a.insured))
        if a.json:
            print(json.dumps(r, indent=2)); return
        print(f"Tornado for {r['insured_id']} (point premium {_money(r['base_premium'])})")
        for bar in r["bars"]:
            print(f"  {bar['parameter']:<55} {_money(bar['premium_at_low']):>10} "
                  f"to {_money(bar['premium_at_high']):>10}  swing {_money(bar['swing'])}")
    elif a.cmd == "benchmark":
        r = benchmark(a.params_dir)
        text = json.dumps(r, indent=2)
        if a.out:
            Path(a.out).write_text(text)
        print(text)


if __name__ == "__main__":
    main()
