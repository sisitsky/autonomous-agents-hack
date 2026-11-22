"""Pricing agent (Loop 1, step 3).

Deterministic code, no LLM. It calls the engine to run the Monte Carlo model,
apply the rating factors, compute the sensitivity (tornado) and the controls
that would lower the price, then archives every equation and number used:

    <run dir>/pricing/calculation.json   full record for the web app and audits
    <run dir>/pricing/calculation.md     the same, readable
    <run dir>/pricing/quote.json         headline numbers

Before saving, it prices the insured a second time and checks the result is
identical, so every archived quote is known to be reproducible.

    python -m agents.pricer agents/runs/northwind
    python -m agents.pricer shopbot                     # a benchmark insured
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from engine.audit import EQUATIONS, calculation
from engine.cli import load_insured
from engine.controls import control_options
from engine.model import load_params, price
from engine.tornado import tornado

from .config import RUNS_DIR

EQ = {e["id"]: e for e in EQUATIONS}


class NotReproducible(RuntimeError):
    pass


def resolve(target: str) -> tuple[dict, Path]:
    p = Path(target)
    if p.is_dir():
        p = p / "insured.yaml"
    if p.is_file():
        return yaml.safe_load(p.read_text()), p.parent
    insured = load_insured(target)               # benchmark id
    return insured, RUNS_DIR / insured["id"]


def compute(insured: dict, with_tornado: bool = True, with_controls: bool = True) -> dict:
    """Pure core (no files): price, verify reproducibility, build the archive."""
    params = load_params()
    quote = price(insured, params, illustrate=120)
    again = price(insured, params, illustrate=120)
    if again["gross_premium"] != quote["gross_premium"] or again["expected_loss"] != quote["expected_loss"]:
        raise NotReproducible("Two runs with the same inputs and seed gave different premiums")

    tor = tornado(insured, params) if with_tornado else None
    ctl = control_options(insured, params, base=quote) if with_controls else None
    calc = calculation(insured, params, quote, tor, ctl)
    calc["meta"]["reproducibility_check"] = "passed: priced twice, identical results"
    return {"quote": quote, "calculation": calc}


def run(target: str, with_tornado: bool = True, with_controls: bool = True,
        out_dir: str | None = None) -> dict:
    insured, run_dir = resolve(target)
    r = compute(insured, with_tornado, with_controls)
    quote, calc = r["quote"], r["calculation"]

    out = Path(out_dir) if out_dir else run_dir / "pricing"
    out.mkdir(parents=True, exist_ok=True)
    archive_previous(out)
    (out / "calculation.json").write_text(json.dumps(calc, indent=2, default=float))
    (out / "calculation.md").write_text(render_markdown(calc))
    headline = {k: quote[k] for k in ("insured_id", "gross_premium", "technical_premium", "expected_loss",
                                      "uncertainty_load", "capital_load", "fixed_expense", "rate_on_line",
                                      "loss_percentiles", "prob_any_loss")}
    headline["meta"] = {k: calc["meta"][k] for k in ("priced_at", "git_commit", "params_versions")}
    (out / "quote.json").write_text(json.dumps(headline, indent=2))
    return {"quote": quote, "calculation": calc, "out_dir": str(out)}


def archive_previous(out: Path) -> None:
    """Keep earlier quotes: move the last calculation into history/ before overwriting,
    so the explainer can say what changed between quotes."""
    prev = out / "calculation.json"
    if not prev.exists():
        return
    stamp = json.loads(prev.read_text())["meta"]["priced_at"].replace(":", "").replace("+0000", "Z")
    hist = out / "history"
    hist.mkdir(exist_ok=True)
    prev.replace(hist / f"calculation-{stamp}.json")


def previous_calculation(out: Path) -> dict | None:
    hist = sorted((out / "history").glob("calculation-*.json")) if (out / "history").exists() else []
    return json.loads(hist[-1].read_text()) if hist else None


# ---------------------------------------------------------------- markdown

def _m(x):
    return f"${x:,.0f}"


def _num(x):
    if x is None:
        return "n/a"
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        if x == 0:
            return "0"
        if abs(x) >= 1000:
            return f"{x:,.0f}"
        if abs(x) >= 1:
            return f"{x:.3g}"
        return f"{x:.4g}"
    return str(x)


def _dist(d: dict) -> str:
    kind = d.get("dist")
    if kind == "fixed":
        return f"fixed {_num(d['value'])}"
    args = ", ".join(f"{k} {_num(v)}" for k, v in d.items() if k != "dist")
    return f"{kind}({args})"


def render_markdown(c: dict) -> str:
    meta, ins, pol = c["meta"], c["insured"], c["policy"]
    L = [f"# Calculation record: {ins.get('name', ins.get('id'))}", "",
         f"> {c['disclaimer']}", "",
         "## Provenance", "",
         f"- Priced at: {meta['priced_at']}",
         f"- Code version (git): `{meta['git_commit']}`",
         f"- Parameter versions: " + ", ".join(f"{k} {v}" for k, v in meta["params_versions"].items()),
         f"- Simulation: seed {meta['simulation']['seed']}, {meta['simulation']['n_outer']:,} parameter draws "
         f"× {meta['simulation']['n_inner']:,} years = {meta['simulation']['n_years']:,} simulated years",
         f"- Reproducibility: {meta.get('reproducibility_check', 'not checked')}",
         f"- Policy: deductible {_m(pol['deductible'])}, {_m(pol['limit_per_claim'])} per claim, "
         f"{_m(pol['aggregate_limit'])} aggregate",
         f"- Model provider bucket: {ins.get('provider', 'unmapped (portfolio average)')}", ""]

    L += ["## Premium build-up", "", "| Step | Amount | Equation |", "|---|---|---|"]
    for s in c["premium_build_up"]:
        val = f"{s['value']:.1%}" if s["id"] == "rate_on_line" else _m(s["value"])
        L.append(f"| {s['label']} | {val} | `{EQ[s['equation']]['formula']}` |")
    by = {s["id"]: s for s in c["premium_build_up"]}
    g, cap = by["gross_premium"]["inputs"], by["capital"]["inputs"]
    L += ["", "With the numbers:", "",
          f"- Capital = ({_m(cap['idiosyncratic_tail'])} − {_m(cap['idiosyncratic_mean'])}) / √{cap['pool_size']} "
          f"+ {cap['concentration_multiplier']:.2f} × ({_m(cap['systemic_tail'])} − {_m(cap['systemic_mean'])}) "
          f"= {_m(by['capital']['value'])}",
          f"- Gross premium = {_m(g['technical_premium'])} / (1 − {g['variable_ratio']} − {g['profit_margin']}) "
          f"= {_m(by['gross_premium']['value'])}", ""]

    L += ["## Perils", ""]
    for name, p in c["perils"].items():
        L += [f"### {name}", "", p["description"], "",
              f"- Frequency: `{EQ[p['equation']]['formula']}`",
              f"- Exposure: {p['exposure'] or 'annual rate (no exposure field)'}",
              f"- Base claims per year (before rating factors): {_num(p['base_claims_per_year'])}"]
        if p.get("red_team_leak_relativity") is not None:
            L.append(f"- Red-team data-leak relativity ρ_leak: ×{p['red_team_leak_relativity']:.2f}")
        rels = " × ".join(f"{f['factor']} {f['level']} ({f['relativity']})" for f in p["rating_factors"]) or "none"
        b = p["modifier_bounds"]
        L += [f"- Rating factors: {rels} = {p['combined_relativity_raw']:.3f}, "
              f"capped to [{b['min']}, {b['max']}] → M = {p['modifier']:.3f}",
              f"- Expected claims per year: {_num(p['expected_claims_per_year'])}",
              f"- Expected loss: {_m(p['expected_loss'])}", "",
              "| Parameter | Distribution | Median | Mean used | Source |", "|---|---|---|---|---|"]
        for q in p["parameters"]:
            L.append(f"| `{q['path'].split(name + '.', 1)[-1]}` | {_dist(q['distribution'])} | "
                     f"{_num(q['median'])} | {_num(q['mean_used'])} | {q.get('source', '')} |")
        L.append("")

    L += ["### Systemic", "", f"- Expected systemic loss: {_m(c['systemic']['expected_loss'])}", "",
          "| Parameter | Distribution | Median | Mean used | Source |", "|---|---|---|---|---|"]
    for q in c["systemic"]["parameters"]:
        L.append(f"| `{q['path']}` | {_dist(q['distribution'])} | {_num(q['median'])} | "
                 f"{_num(q['mean_used'])} | {q.get('source', '')} |")

    ld = c["loss_distribution"]
    L += ["", "## Annual loss distribution", "", f"Probability of any loss in a year: {ld['prob_any_loss']:.1%}", "",
          "| Percentile | Annual loss |", "|---|---|"]
    L += [f"| {k} | {_m(v)} |" for k, v in ld["percentiles"].items()]

    if c.get("tornado"):
        t = c["tornado"]
        L += ["", "## Sensitivity (tornado)", "",
              f"Each parameter moved to its P{t['percentiles'][0]*100:.0f} and P{t['percentiles'][1]*100:.0f} "
              f"with the rest at their medians (point premium {_m(t['base_premium'])}; excludes the "
              "uncertainty load).", "",
              "| Parameter | Low → high value | Premium at low | Premium at high | Swing |", "|---|---|---|---|---|"]
        for bar in t["bars"][:10]:
            L.append(f"| `{bar['parameter']}` | {_num(bar['low_value'])} → {_num(bar['high_value'])} | "
                     f"{_m(bar['premium_at_low'])} | {_m(bar['premium_at_high'])} | {_m(bar['swing'])} |")

    if c.get("controls"):
        L += ["", "## What would lower the price", "",
              "Re-priced with the same seed, so differences come only from the change.", "",
              "| Change | Premium | Saving |", "|---|---|---|"]
        for o in c["controls"]["options"]:
            L.append(f"| {o['factor']}: {o['from_level']} → {o['to_level']} | {_m(o['premium'])} | "
                     f"{_m(o['saving'])} ({o['saving_pct']:.0%}) |")

    L += ["", "## Equations", ""]
    L += [f"- **{e['name']}**: `{e['formula']}`. {e['meaning']}" for e in c["equations"]]
    L += ["", "Full method: `docs/PRICING_METHOD.md`.", ""]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(prog="agents.pricer")
    ap.add_argument("target", help="Run directory, insured YAML, or benchmark id")
    ap.add_argument("--out-dir")
    ap.add_argument("--no-tornado", action="store_true")
    ap.add_argument("--no-controls", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = run(a.target, not a.no_tornado, not a.no_controls, a.out_dir)
    if a.json:
        print(json.dumps(r["calculation"], indent=2, default=float)); return
    q, c = r["quote"], r["calculation"]
    print(f"Quote for {q['insured_id']}  (params {c['meta']['params_versions']}, seed {c['meta']['simulation']['seed']})")
    for s in c["premium_build_up"]:
        val = f"{s['value']:.1%}" if s["id"] == "rate_on_line" else _m(s["value"])
        print(f"  {s['label']:<52} {val:>12}")
    if c.get("controls") and c["controls"]["options"]:
        print("  Cheapest changes:")
        for o in c["controls"]["options"][:4]:
            print(f"    {o['factor']}: {o['from_level']} -> {o['to_level']}  saves {_m(o['saving'])} ({o['saving_pct']:.0%})")
    print(f"  {c['meta']['reproducibility_check']}")
    print(f"  Archived to {r['out_dir']}/calculation.json and calculation.md")


if __name__ == "__main__":
    main()
