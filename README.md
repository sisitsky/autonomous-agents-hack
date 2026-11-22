# AI Underwriter

Autonomous, self-calibrating pricing for AI liability risk, built for the
"Build business loops" hackathon brief. Start with CLAUDE.md.

```bash
pip install -r requirements.txt
python -m pytest -q
python -m engine.cli quote shopbot
python -m engine.cli tornado shopbot
```

All parameters are illustrative. This is not an offer of insurance.

## Loop 1: underwriting (in progress)

Details: [README_INTAKE_AGENT.md](README_INTAKE_AGENT.md), [README_REDTEAM_AGENT.md](README_REDTEAM_AGENT.md), [README_PRICING_AGENT.md](README_PRICING_AGENT.md). Pricing method: [docs/PRICING_METHOD.md](docs/PRICING_METHOD.md).

```bash
export ANTHROPIC_API_KEY=...                          # runtime agents call the Claude API
python -m agents.intake agents/submissions/northwind --id northwind
#   -> agents/runs/northwind/profile.json  (risk profile, evidence for every field)
#   -> agents/runs/northwind/insured.yaml  (engine input; unknowns left out and flagged)
python -m engine.cli quote agents/runs/northwind/insured.yaml

python -m agents.target_bot.shopbot &                 # deliberately flawed red-team target on :8765
python -m agents.redteam agents/runs/northwind        # -> red_team: {tested, failures} in insured.yaml
python -m agents.pricer agents/runs/northwind         # deterministic quote + calculation archive
```

Model names live in `agents/config.py`. Red-team suite: `agents/test_suites/customer_service.yaml`.
