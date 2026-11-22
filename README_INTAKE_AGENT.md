# Intake agent

Loop 1, step 1. The intake agent reads a company's description and documents
about one AI system and turns them into a structured risk profile, then into
the insured YAML the pricing engine prices. Code: `agents/intake.py`.

All figures are illustrative. This is a pricing and risk-assessment engine, not an
offer of insurance.

## Run it

```bash
export ANTHROPIC_API_KEY=...
python -m agents.intake agents/submissions/northwind --id northwind
python -m engine.cli quote agents/runs/northwind/insured.yaml
```

Inputs can be files or folders of markdown, text, or PDF. Add `--description "..."`
for a short free-text note and `--json` for machine-readable output.

## What it produces

**`profile.json`, the risk profile.** It covers:

- use case and how critical it is
- autonomy (how much human review)
- data sensitivity
- jurisdictions
- model provider
- customer-facing or internal
- governance
- IP indemnity from the vendor
- which perils apply
- yearly volume of outputs

Every field quotes the document that supports it. Claude answers in a fixed JSON
format (structured outputs), so the result always has the same shape. The
allowed levels are read from `params/` on every run, so they stay in step when
calibration changes the rating factors.

**`insured.yaml`, the pricing engine's input.** Plain code builds it from the
profile; Claude does not. Where the documents don't support a level, Claude
answers "unknown". That factor is then left out (priced as neutral) and becomes
an open question for the underwriter instead of a guess. Review notes are written
as comments at the top of the file. Intake never invents red-team results; those
come from the red-team agent (`README_REDTEAM_AGENT.md`).

## Choices to review

- **Jurisdictions.** The engine takes one jurisdiction level, so a company in
  several is rated at the strictest one. Regions outside the US and EU (for
  example Canada) have no rating level and are flagged for manual review.
- **Model provider.** The pricing settings use anonymous provider names
  (`provider_a`, etc.), and which real vendor counts as which is a portfolio
  decision for a human. `agents/provider_aliases.yaml` maps vendor names to
  them and holds only the fictional demo vendor "Acme AI". Self-hosted open models
  map automatically. Unmapped vendors are priced at the portfolio-average
  concentration and flagged.
- **Volume.** If the documents don't state how many responses the system gives
  per year, the "wrong answer" peril can't be priced, so it is left out and
  flagged.
- **Policy terms.** If the company doesn't request limits, defaults are used
  ($1M per claim, $2M aggregate, $10k deductible) and noted.

## Demo submission

`agents/submissions/northwind/` is a fictional application for Northwind Goods'
ShopBot: an application form, an architecture and controls note, and the
customer policy. Extracted correctly, it prices at about $221k, versus $190k for
the benchmark ShopBot. The main difference is that its California customers
trigger the stricter-regulation jurisdiction level.

## Tests

`tests/test_intake.py` (10 tests) replaces the Claude call with a hand-written
correct profile for Northwind. The tests check the output format, the mapping to
the insured YAML, the handling of unknowns, missing volume, self-hosted models,
default policy terms, PDF handling, and the clear error when there is no API key.
