# AI Underwriter: CLAUDE.md

## What this is

A hackathon project for the brief **"Build business loops"**: closed autonomous
loops that operate and improve a startup beyond coding, with humans **on** the
loop (setting goals, guardrails, approvals) rather than **in** it.

The product is an autonomous underwriter for AI liability risk. A company submits
its AI system, agents test it and price coverage with a Monte Carlo model, and
the system recalibrates itself as new evidence (incidents, court rulings,
regulation) arrives. A human approves every change to pricing assumptions.

This is a pricing and risk-assessment engine on illustrative data. It does not
bind insurance. Say so in the UI and the pitch.

## Architecture

| Piece | Where | Notes |
|---|---|---|
| Pricing engine | `engine/` (Python) | Deterministic, tested. The only place pricing math lives. |
| Assumptions | `params/` (YAML) | Versioned. What the agents improve. Changed only via PR. |
| Benchmark book | `benchmarks/insureds.yaml` | Fixed insureds re-priced on every PR. |
| Agents | `agents/` + `.claude/agents/` | Runtime agents call the Claude API; subagents help build. |
| Web app | `app/` (Next.js on Vercel) | Intake form, quote page, loss histogram, tornado chart, loop activity feed. |
| Loops | `.github/workflows/` | Guardrail CI on PRs; scheduled calibration loop. |
| Business ops | Egbe (MCP) | Research, landing page, ads, outreach, email, Stripe checkout, analytics. |

Runtime agents use the Claude API (`claude-sonnet-5-5` for speed, `claude-opus-5-5`
for calibration reasoning). Keep model names in one config file.

## Hard rules

1. **Pricing math lives in `engine/` only.** Agents call the engine; prompts never compute premiums.
2. **Never edit `params/` on main.** Agents propose changes on a branch and open a PR
   whose body cites evidence for every changed parameter, with the guardrail report.
3. **Never edit protected files** listed in `params/guardrails.yaml` (guardrails, the
   guardrail checker, the guardrail workflow). Those are human-only.
4. **Every parameter keeps a `source:` note.** Update it when the value changes.
5. **Growth data never affects price.** Conversion and marketing results may change
   targeting and messaging only (price-optimization rules in many US states).
6. **Respect Egbe spend and outreach limits** in `params/guardrails.yaml`. Outreach
   templates need human approval before sending.
7. **Keep the engine deterministic** given a seed. Add a test for every engine change.
8. **Never put secrets in the repo.** Use Vercel env vars, GitHub secrets, Egbe secrets.

## Commands

```bash
pip install -r requirements.txt
python -m pytest -q                                  # tests
python -m engine.cli quote shopbot                   # price a benchmark insured
python -m engine.cli quote path/to/insured.yaml      # price any insured
python -m engine.cli tornado shopbot                 # sensitivity analysis
python -m engine.cli benchmark --out bench.json      # price the benchmark book
python -m engine.guardrails --baseline base.json --candidate bench.json \
    --changed-files "params/priors.yaml"             # guardrail check
```

Add `--json` to `quote` and `tornado` for machine-readable output (the API and UI use this).

## Engine model (summary)

- Two-layer Monte Carlo: outer layer samples parameters (uncertainty), inner layer
  simulates years (luck). Inverse-transform sampling keeps random streams stable
  across parameter changes (common random numbers).
- Frequency: `chain` perils = outputs/yr × P(error) × P(harm|error) × P(claim|harm);
  `annual_rate` perils = a direct rate. Red-team counts override P(error) as a Beta.
- Severity: lognormal body (uncertain median and p90) plus optional Pareto tail.
- Rating factors multiply frequency per peril, capped by `combined_modifier_bounds`.
- Systemic event at the insured's model provider; no diversification credit, and
  capital scaled by provider concentration in the portfolio.
- Premium = (expected loss + uncertainty load + capital load + fixed) / (1 − variable − profit).
- Tornado uses point pricing (medians), so its base excludes the uncertainty load.

## The three loops

**Loop 1, underwriting (per customer)**
1. `intake` turns a description and documents into an insured YAML matching `benchmarks/insureds.yaml`.
2. `redteam` sends a test suite to the customer's AI endpoint, scores failures, writes `red_team: {tested, failures}`.
3. `pricer` calls `engine.cli quote --json` and `tornado --json`.
4. `explainer` writes the quote report: price build-up, tornado, and which controls would lower the price.

**Loop 2, actuarial self-improvement (portfolio)**
1. `monitor` (scheduled) collects new evidence into `data/incidents/` (for the demo, drop a file in).
2. `calibrator` maps evidence to parameter changes on a branch, updates `source:` notes, explains the Bayesian reasoning.
3. CI re-prices the benchmark book and runs guardrails; the report is posted on the PR.
4. A human reviews and merges. The next quote uses the new parameters.

**Loop 3, growth (the business, via Egbe)**
1. Research target segments (companies shipping customer-facing AI).
2. Launch a landing page ("free AI risk score") and small ad or outreach tests within spend limits.
3. Charge for a full report via an Egbe Stripe checkout link.
4. Read conversion by segment and rewrite `agents/growth_playbook.md` for the next cycle.

## Build plan (in order; keep something demoable at every step)

1. Engine and params (done: scaffold, tests passing).
2. API: expose `quote` and `tornado` as HTTP endpoints. Check current Vercel docs for the
   Python runtime; if mixing Python with Next.js is awkward, deploy the API as its own
   Vercel project and call it from the UI.
3. UI on Vercel: intake form, quote page (premium build-up, loss percentiles, tornado chart), loop activity feed.
4. Red-team target: a small, deliberately flawed sample chatbot so the test is real and controlled.
5. Underwriting agents (intake, redteam, explainer).
6. Calibration loop: `.github/workflows/calibrate.yml` plus the `calibrator` subagent.
7. Egbe: connect the MCP server (`claude mcp add ...` with the endpoint from Egbe's docs), run the growth loop.

## Demo script (about 3 minutes)

1. Submit ShopBot. Show quote, build-up, and tornado (claim propensity dominates).
2. Drop `data/incidents/2026-11-chatbot-ruling.md` in and trigger calibration.
3. Show the PR: changed parameter, cited evidence, guardrail report (passes, within ±15%).
4. Merge (human on the loop). Re-quote: price moves, explainer says why.
5. Show a rejected change: an aggressive edit fails guardrails and cannot merge.
6. Show the Egbe growth loop dashboard running alongside.
