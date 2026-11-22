# Explainer agent

Loop 1, step 4. Writes the quote report from the pricing agent's calculation
archive. The report covers the premium build-up, the loss distribution, the tornado
chart, the controls that would lower the price, red-team findings, what changed
since the last quote, and open questions. Code: `agents/explainer.py`, with charts
in `agents/charts.py`.

All figures are illustrative. This is a pricing and risk-assessment engine, not an
offer of insurance.

```bash
python -m agents.explainer agents/runs/northwind            # Claude narrative
python -m agents.explainer agents/runs/northwind --offline  # template narrative, no API call
python -m agents.explainer agents/runs/northwind --reprice  # run the pricing agent first
```

Outputs in `agents/runs/<id>/report/`:

- `report.html`: self-contained, with charts inline (no external files)
- `report.md`: the same, with the charts as separate SVG files
- `loss_distribution.svg`, `tornado.svg`, `controls.svg`
- `narrative.json`: the narrative, the fact sheet it was written from, and the
  number-check result

Example from the live Northwind run: [docs/examples/northwind_report/](docs/examples/northwind_report/report.md).

## Who does what

| Part | Done by |
|---|---|
| Fact sheet: every number, pre-formatted, from `calculation.json` | Code |
| Charts (SVG, byte-identical for the same archive) | Code |
| Narrative: what drives the price, a typical and a bad year, what evidence would narrow the range, recommendations | Claude (`claude-sonnet-5-5`) |
| Checking every number in the narrative against the fact sheet | Code |

**Number guardrail.** Claude is told to copy numbers from the fact sheet exactly and
never compute one. Code then extracts every number from the narrative and checks
it against the fact sheet. Correct rounding passes ("$729k" for $728,976), and
case ids and percentile labels like "P90" are ignored. If any section has a number
that isn't in the fact sheet, Claude gets one retry with the offending numbers
listed. A section that still fails is replaced with deterministic template text,
and the report says so. Any control the narrative skips gets template text too.
Without an API key, the whole narrative comes from templates.

**What changed since the last quote.** The pricing agent now moves the previous
`calculation.json` to `pricing/history/` before saving a new one. The explainer
compares the two: premium before and after, parameter versions, which parameter
files changed (by hash), whether the insured changed, and the git commits that
touched `params/` in between. This is the "re-quote after calibration" step in the
demo.

## The charts

- **Loss distribution:** a histogram of annual losses to the policy over all simulated
  years. It has dashed markers for expected loss, P90 and P99.5, and marks the per-claim
  and aggregate limits, where losses pile up.
- **Tornado:** the eight parameters that move the premium most. Each runs from the
  premium with that parameter at its P10 (green) to its P90 (red), with the rest held at
  their medians.
- **Controls:** the premium after each change the insured could make, re-priced with
  the same simulation.

## Tests

`tests/test_explainer.py` (10 tests). It checks report contents, that template
numbers all verify, that the fact sheet matches the archive, the number checker,
retry-then-fallback when Claude invents a number, that a clean narrative is kept,
HTML escaping of untrusted text, deterministic charts, the histogram covering every
simulated year, and the changes-since-last-quote section.
