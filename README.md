# iacta.ai (AI Underwriter)

Autonomous, self-calibrating pricing for AI liability risk, built for the
"Build business loops" hackathon brief. Start with CLAUDE.md.

```bash
pip install -r requirements.txt
python -m pytest -q
python -m engine.cli quote shopbot
python -m engine.cli tornado shopbot
```

All parameters are illustrative. This is not an offer of insurance.

## Loop 1: underwriting

One command runs all four steps and logs each one (`agents/runs/<id>/events.jsonl`):

```bash
python -m agents.underwrite agents/submissions/northwind --id northwind --demo-bot deterministic --export public/data/runs
```

Details: [README_INTAKE_AGENT.md](README_INTAKE_AGENT.md), [README_REDTEAM_AGENT.md](README_REDTEAM_AGENT.md), [README_PRICING_AGENT.md](README_PRICING_AGENT.md), [README_EXPLAINER_AGENT.md](README_EXPLAINER_AGENT.md). Pricing method: [docs/PRICING_METHOD.md](docs/PRICING_METHOD.md).

```bash
export ANTHROPIC_API_KEY=...                          # runtime agents call the Claude API
python -m agents.intake agents/submissions/northwind --id northwind
#   -> agents/runs/northwind/profile.json  (risk profile, evidence for every field)
#   -> agents/runs/northwind/insured.yaml  (engine input; unknowns left out and flagged)
python -m engine.cli quote agents/runs/northwind/insured.yaml

python -m agents.target_bot.shopbot &                 # deliberately flawed red-team target on :8765
python -m agents.redteam agents/runs/northwind        # -> red_team: {tested, failures} in insured.yaml
python -m agents.pricer agents/runs/northwind         # deterministic quote + calculation archive
python -m agents.explainer agents/runs/northwind      # quote report: report.html with charts
```

Model names live in `agents/config.py`. Red-team suite: `agents/test_suites/customer_service.yaml`.

## Assess any company

**On the web** (https://ai-underwriter.vercel.app, section "Run the loop live"):
1. Paste a description and attach the company's documents: applications,
   architecture notes, policies, model cards (PDF, text, markdown, HTML).
2. Choose a red-team target:
   - **no test:** priced on the portfolio average;
   - **your own chat endpoint**, with configurable request and reply fields and an
     optional Authorization header. You must confirm you are authorized to test it,
     and private or internal addresses are blocked;
   - one of the demo bots.
3. Optionally set outputs per year and a plan. If the documents don't state a
   volume, a default of 250,000 outputs per year is used and flagged for review.

Steps that call Claude need the demo access code.

**From the CLI:**

```bash
python -m agents.underwrite path/to/company_docs/ --id acme                                  # no red-team test
python -m agents.underwrite path/to/company_docs/ --id acme --endpoint https://… --authorized  # test their bot
```
