# Pricing method

How the AI Underwriter turns a risk profile and red-team results into a premium.
It is written for the web app's "how was this priced?" page and for reviewers.

> Illustrative pricing on illustrative assumptions. This is a pricing and
> risk-assessment engine, not an offer of insurance.

## Who computes what

| Step | Done by | LLM? |
|---|---|---|
| Read documents, fill in the risk profile | Intake agent | Yes (classification only, with cited evidence) |
| Test the AI system, count failures | Red-team agent | Yes (grading against explicit rubrics) |
| **Everything below: frequencies, rating factors, Monte Carlo, loads, premium** | **Pricing agent → `engine/`** | **No. Deterministic Python** |
| Write the plain-English report | Explainer agent | Yes (uses only numbers from the archive) |

The pricing agent (`agents/pricer.py`) calls the engine, prices the insured
**twice** and checks that both results are identical, then archives the record. Same
insured + same parameter files + same seed ⇒ identical numbers.

## Where the numbers live

| File | Contents |
|---|---|
| `params/priors.yaml` | Distributions for every uncertain parameter, each with a `source:` note |
| `params/rating_factors.yaml` | Rating factors, their levels and relativities, the modifier cap |
| `params/pricing.yaml` | Uncertainty percentile, capital (TVaR level, pool size, cost of capital, provider shares), expenses |
| `params/guardrails.yaml` | Bounds and change limits enforced on every parameter change |
| `data/portfolio/redteam_experience.jsonl` | Portfolio red-team results for Bühlmann credibility (data, updated by every run) |

Parameter distributions: `beta(α, β)` for probabilities from test counts,
`pert(min, mode, max)` for expert three-point estimates, `lognormal(median, p90)`, and
`uniform(min, max)`.

## Equations

The ids match `engine/audit.py: EQUATIONS` and the `equation` fields in each quote's
`calculation.json`.

### 1. Claim frequency

**`frequency_chain`**, for perils driven by output volume (misinformation):

    λ = N × P(error) × P(harm | error) × P(claim | harm) [× r_adv]

N is outputs per year from intake. P(error) comes from red-team results when
available, otherwise from the prior.

**`frequency_rate`**, for perils with a direct annual rate (privacy, discrimination, IP):

    λ = rate [× ρ_leak]

**`red_team_beta`**: F failures in T cases update a uniform prior:

    P(error) ~ Beta(F + 1, T − F + 1)

Few tests give a wide distribution, and that width becomes uncertainty load.

**`buhlmann_credibility`**: how much to trust the insured's own red-team result
versus the rest of the portfolio:

    Z = n / (n + k),   k = EPV / VHM,   priced rate = Z × X + (1 − Z) × μ

Here n is the number of cases tested and X the observed failure rate. μ is the
portfolio mean. EPV is the expected process variance (test-to-test noise within one
system) and VHM is the variance of hypothetical means (real differences between
systems). More tests give more credibility.

**`buhlmann_straub`**: μ, EPV and VHM are estimated non-parametrically from every
*other* insured tested with the same suite. The insured's own record is left out,
so its result is never counted twice.

    μ   = Σ nᵢ Xᵢ / m
    EPV = Σ nᵢ Xᵢ (1 − Xᵢ) / Σ (nᵢ − 1)
    VHM = [Σ nᵢ (Xᵢ − μ)² − (r − 1) EPV] / (m − Σ nᵢ² / m)

This is separate for error cases (P(error)) and data-leak probes (the leak rate).
**It updates continuously.** Every underwriting run adds its result to
`data/portfolio/redteam_experience.jsonl`, replacing any earlier record for the same
insured, and the next quote re-estimates μ, EPV, VHM and k from the larger
portfolio. The store ships with a FICTIONAL seed portfolio of 12 earlier
customer-service bots. The SHA-256 of the store is recorded with every quote. With
fewer than 5 other insureds, the uniform prior Beta(1, 1) is used.

**`credibility_prior`**: credibility enters the Beta as its prior:

    P(fail) ~ Beta(F + μk, n − F + (1 − μ)k)

The posterior mean of this Beta is exactly the Bühlmann estimate, so credibility
and parameter uncertainty come from one distribution. Live-sample suites (the
benchmark book) keep Beta(F + 1, n − F + 1).

**`adversarial_ratio`**: adversarial suites over-sample failure modes, so

    P(error)_live = P(fail | probe) × r_adv,   r_adv ~ pert(0.003, 0.01, 0.03)

Tests sampled from live traffic (`suite: live_sample`) skip this step.

**`leak_relativity`**: data-leak probes scale the privacy rate:

    ρ_leak = clip(L / L_ref, 0.5, 3.0),   L ~ Beta(leaks + 1, passes + 1),   L_ref ~ pert(0.05, 0.15, 0.35)

Each red-team case prices one peril only. Leak probes price privacy; everything else
prices P(error).

### 2. Rating factors

**`rating_modifier`**:

    M = clip(∏ f_i, 0.5, 3.0)

The product runs over the factors that apply to the peril (for example, autonomy
applies to misinformation and discrimination, not to privacy). A factor intake
couldn't determine is left out, which is neutral (× 1.0). Measured model
performance is not a factor: it enters through the red-team counts, and counting
it twice would double-charge.

### 3. Simulation

**`claim_count`**: claims in a simulated year, `n ~ Poisson(λ × M)`.

**`severity`**: each claim's size,

    X ~ Lognormal(μ = ln m, σ = (ln p90 − ln m) / 1.2816)

With probability q, the claim instead comes from a Pareto tail, `X = T × U^(−1/α)`.

**`policy_layer`**: `paid = min(max(X − d, 0), L_claim)`. The year's total is capped
at the aggregate limit A.

**`systemic`**: a correlated event at the insured's model provider. It happens with
probability p_s, hits this insured with probability s, and costs ℓ × A.

The Monte Carlo has two layers: n_outer parameter draws (what we don't know) ×
n_inner simulated years per draw (luck). The default is 2,000 × 200 = 400,000
years, seed 42. Every parameter path and every peril's simulation draws from its
own named random stream (seeded from the seed plus a hash of the name), and
parameters use inverse-transform sampling. So changing one parameter or peril
never shifts the random numbers used by any other (common random numbers), and
price differences in the tornado and controls reflect the change, not noise.

### 4. Premium

**`expected_loss`**: `EL = mean of min(idiosyncratic + systemic, A)` over all simulated years.

**`uncertainty_load`**: `U = max(0, P75 of per-draw expected loss − EL)`. This charges for
not knowing the parameters.

**`capital`**:

    C = (TVaR₉₉.₅(idio) − mean(idio)) / √n_pool + c × (TVaR₉₉.₅(sys) − mean(sys))

Independent losses diversify across a pool of n_pool = 400 policies; systemic
losses get no diversification credit.

**`concentration`**: `c = provider share / average provider share`. Policies on a heavily
used provider carry more systemic capital.

**`capital_load`**: `K = 10% × C`.

**`gross_premium`**: `G = (EL + U + K + F) / (1 − v − π)`, with fixed expense F = $2,500,
variable expenses v = 25% and profit π = 5%.

**`rate_on_line`**: `ROL = G / A`.

## Sensitivity and controls

- **Tornado** (`engine/tornado.py`): each uncertain parameter is set to its P10 and P90
  with the others at their medians, using point pricing (no uncertainty load). Bars
  are sorted by swing.
- **Controls** (`engine/controls.py`): the insured is re-priced with the same seed under
  each change it could make. These are rating factors marked `controllable: true`
  (autonomy, governance, data sensitivity, vendor indemnity), moved to every cheaper
  level, plus "fix every red-team failure and re-test".

## The archive for each quote

`agents/runs/<id>/pricing/calculation.json` (and a readable `calculation.md`) holds:

| Key | Contents |
|---|---|
| `meta` | Timestamp, git commit, parameter versions, SHA-256 of every parameter and engine file, seed and simulation size, reproducibility check |
| `insured` | The exact engine input |
| `perils.<name>` | Exposure, base claims, leak relativity, each rating factor with its relativity and source, raw and capped modifier, expected claims and loss, and every parameter's distribution, median, mean as drawn, and source |
| `systemic` | Provider, expected systemic loss, parameters |
| `premium_build_up` | Each step with its value, equation id, and inputs |
| `loss_distribution` | Annual-loss percentiles, probability of any loss |
| `tornado`, `controls` | Sensitivity bars; cheaper options with re-priced premiums |
| `equations` | This page's equations (plain text, LaTeX for KaTeX, meaning) |

To reproduce a quote, check out the recorded commit (the SHA-256 hashes confirm
the parameter files match) and run `python -m agents.pricer <insured.yaml>`.
Example: `docs/examples/northwind_calculation.md`.
