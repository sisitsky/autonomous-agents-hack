# Pricing method (model 1.0)

How the AI Underwriter turns a risk profile and red-team results into a premium.
It is written for the web app's "how was this priced?" page and for reviewers.

> Illustrative pricing on illustrative assumptions. This is a pricing and
> risk-assessment engine, not an offer of insurance.

## Who computes what

| Step | Done by | LLM? |
|---|---|---|
| Read documents, fill in the risk profile | Intake agent | Yes (classification only, with cited evidence) |
| Test the AI system, label each failure with one type | Red-team agent | Yes (grading against explicit rubrics) |
| **Everything below: Dirichlet / Jewell credibility, frequencies, rating factors, Monte Carlo, loads, plans** | **Pricing agent → `engine/`** | **No. Deterministic Python** |
| Write the plain-English report | Explainer agent | Yes (uses only numbers from the archive, each one checked) |

The pricing agent prices the chosen plan **twice** and checks that both results
are identical before archiving. Same insured + same parameter files + same
experience store + same seed ⇒ identical numbers.

## Where the numbers live

| File | Contents |
|---|---|
| `params/priors.yaml` | The eight perils, red-team settings, distributions for every uncertain parameter (each with a `source:` note) |
| `params/rating_factors.yaml` | Rating factors, their levels and relativities, which perils each applies to, the modifier cap |
| `params/plans.yaml` | Coverage tiers: covered perils, limits, deductibles, minimum premiums |
| `params/pricing.yaml` | Uncertainty percentile, capital (TVaR level, pool size, cost of capital, provider shares), expenses |
| `params/guardrails.yaml` | Bounds and change limits enforced on every parameter change |
| `data/portfolio/redteam_experience.jsonl` | Portfolio red-team outcomes for Jewell credibility (data, updated by every run) |

## Perils: common AI pitfalls

| Peril | How its frequency is measured | Typical harm |
|---|---|---|
| Wrong information (hallucination, wrong policy or price) | Red team | Small individual losses; rare group claims |
| Unauthorized commitment (refunds, credits, discounts, exceptions) | Red team | Promises the company must honor or defend |
| Data leakage (personal or internal data disclosed) | Red team | Privacy claims; rare regulatory actions |
| Prompt injection (hidden instructions take over the AI) | Red team | Fraud, unauthorized actions, breaches |
| Bias and discrimination | Profile and rating factors | Individual or class discrimination claims |
| IP infringement | Profile and rating factors | Copyright or trademark claims |
| Harmful content (toxic or defamatory output) | Profile and rating factors | Defamation and harassment claims |
| Unsafe advice (medical, financial, legal, safety) | Profile and rating factors | Bodily or financial harm from reliance |

Each peril has its own frequency chain, severity distribution and set of rating
factors, so risks are priced differently by what can go wrong and how much it costs.

## Plans

| Plan | Covers | Per claim / aggregate | Deductible | Minimum premium |
|---|---|---|---|---|
| Essential | Wrong information, unauthorized commitment | $250k / $500k | $5k | $2.5k |
| Professional | + data leakage, prompt injection, IP | $1M / $2M | $10k | $7.5k |
| Enterprise | All eight perils | $2.5M / $5M | $10k | $20k |

Every quote prices all three plans with the same seed. A plan prices only the
perils it covers *and* the insured is exposed to.

**Calibration.** Severities reflect that most chatbot harms are small and that the
expensive cases are rare group claims and regulatory actions. The parameters are
set so a well-tested customer-facing bot lands near a 1% rate on line on the
Professional plan. Market reference points (November 2026, indicative):
- Brokers quote $2.5k–$10k a year for low-exposure AI startups with a $1M limit.
- A chatbot endorsement for a company with modest customer-facing exposure runs $8k–$25k.
- Published AI-liability tiers start around $15k a year.
- Insurance Insider reports about $25k per $1M of coverage for smaller insureds, and up to $100k per $1M for large ones.

**Model 1.1 recalibration (2026-11-22).** Prices were checked against the market
reference points above, and four assumptions changed:
- Unauthorized-commitment claims are rarer, because honoring a promise is the
  insured's own cost, not a covered claim.
- The uncertainty load is charged at P65 rather than P75; capital already covers
  the 1-in-200 tail.
- The cost of capital is 6%, the Solvency II risk-margin rate.
- A systemic event is a burst of ordinary claims (1.2–5× normal annual loss)
  rather than a fixed share of the limit, so systemic capital scales with risk.

Results for Northwind:

| Plan | Passes all 40 tests | Fails 24 of 40 | Untested (portfolio average) |
|---|---|---|---|
| Essential | ≈ $5.6k | ≈ $19.6k | ≈ $10.4k |
| Professional | ≈ $11.3k | ≈ $49.8k | ≈ $29.4k |
| Enterprise | $20k (minimum) | ≈ $56k | ≈ $32.6k |

**Referral.** Quotes stay risk-based, but a system failing more than 35% of red-team
cases is referred to a human underwriter. Published AI-liability carriers cap
surcharges around +100%; beyond that an underwriter would require remediation and
a re-test before binding.

## Equations

The ids match `engine/audit.py: EQUATIONS` and the `equation` fields in each quote's
`calculation.json`.

### 1. Red-team outcomes: Dirichlet with Jewell credibility

Each of the n test cases ends in a pass or exactly one failure type (the red-team
judge records the most serious consequence). The outcome shares
θ = (θ_pass, θ_wrong_info, θ_commitment, θ_leak, θ_injection) sum to one.

**`dirichlet`**: θ ~ Dirichlet(A μ + counts). Each share's marginal is
Beta(α_k, α_0 − α_k). The shares are drawn jointly with the gamma construction
(inverse-transform uniforms, one random stream per outcome). They are negatively
correlated: a system that fails more one way has less room to fail another.

**`jewell_credibility`**: Jewell (1974) showed the Dirichlet–multinomial has exact
credibility. The posterior mean is linear:

    E[θ_k | counts] = Z × X_k + (1 − Z) × μ_k,   Z = n / (n + A)

One Z applies to every failure type.

**`buhlmann_straub`**: μ and A are estimated from every *other* insured tested on
the same suite (leave-one-out). For each outcome k:

    μ_k   = Σ nᵢ Xᵢₖ / m
    EPV_k = Σ nᵢ Xᵢₖ (1 − Xᵢₖ) / Σ (nᵢ − 1)
    VHM_k = [Σ nᵢ (Xᵢₖ − μ_k)² − (r − 1) EPV_k] / (m − Σ nᵢ² / m)
    A     = Σ_k EPV_k / Σ_k VHM_k

Under the Dirichlet, EPV_k / VHM_k = A for every outcome, so the outcomes are pooled.
**This updates continuously.** Every underwriting run records its outcome counts in
the experience store, replacing any earlier record for that insured, and the next
quote re-estimates μ and A. The store ships with a FICTIONAL seed portfolio of 12
bots. With fewer than 5 other insureds a weak fallback prior is used (worth 10 cases).

**`adversarial_ratio`**: adversarial suites over-sample failure modes, so the live
failure rate is θ_k × r_adv, with r_adv ~ pert(0.002, 0.006, 0.02).

### 2. Claim frequency

**`frequency_red_team`**, for the four red-team perils:

    λ_k = N × θ_k × r_adv × P(harm | failure_k) × P(claim | harm_k)

Here N is outputs per year from intake. Each type has its own harm and claim
propensities.

**`frequency_rate`**, for the four profile perils: λ = rate.

**`rating_modifier`**: M = clip(∏ f_i, 0.5, 3.0) over the factors that apply to the
peril. A factor intake couldn't determine is left out, which is neutral. Measured
behavior is not a factor: it enters through the red-team counts.

### 3. Simulation

**`claim_count`**: claims in a simulated year, `n ~ Poisson(λ × M)`.

**`severity`**: each claim's size, `X ~ Lognormal(μ = ln m, σ = (ln p90 − ln m) / 1.2816)`.
With probability q, the claim instead comes from a Pareto tail, `X = T × U^(−1/α)`
(group claims and regulatory actions).

**`policy_layer`**: `paid = min(max(X − d, 0), L_claim)`, with the year's total capped
at the plan's aggregate limit A.

**`systemic`**: a provider-wide event (probability p_s) that hits this insured with
probability s. It costs b × its normal expected annual loss (a burst of ordinary
claims), capped at A.

The Monte Carlo has two layers: n_outer = 2,000 parameter draws × n_inner = 200
simulated years each, 400,000 years in total, seed 42. Every parameter path and
every peril's simulation has its own named random stream (common random numbers),
so the tornado and controls compare like with like.

**Accuracy checks** (`tests/test_math.py`): with every parameter fixed, the
simulated expected loss matches the closed form λ × [LEV(d + L) − LEV(d)] for
lognormal claims within 2%, with and without deductibles and per-claim limits.
The gross-up arithmetic and the aggregate cap are checked exactly. The
uncertainty load is checked against its closed form when only the claim rate is
uncertain.

### 4. Premium

**`expected_loss`**: `EL = mean of min(idiosyncratic + systemic, A)` over all simulated years.

**`uncertainty_load`**: `U = z₀.₆₅ × σ_param`, where
`σ²_param = Var(EL_j) − E[s²_j] / n_inner`. Each parameter draw's average loss mixes
parameter uncertainty with luck. The luck part (within-draw variance divided by
years per draw) is removed, so the load charges only for not knowing the parameters.

**`capital`**:

    C = (TVaR₉₉.₅(idio) − mean) / √n_pool + c × (TVaR₉₉.₅(event share) − mean)

Independent losses diversify across a pool of 400 policies. The provider event is
allocated to each policy at its expected share (s × burst loss), because which
policies it reaches diversifies but the event itself does not.

**`concentration`**: `c = provider share / average provider share`.

**`capital_load`**: `K = 6% × C` (the Solvency II risk-margin cost-of-capital rate).

**`gross_premium`**: `G = max((EL + U + K + F) / (1 − v − π), plan minimum)`, with fixed
expense F = $2,500, variable expenses v = 25% and profit π = 5%.

**`rate_on_line`**: `ROL = G / A`.

## Sensitivity, controls and the simulation graphic

- **Tornado** (`engine/tornado.py`): each uncertain parameter, including each Dirichlet
  share through its Beta marginal, is set to its P10 and P90 with the others at their
  medians.
- **Controls** (`engine/controls.py`): re-pricing with the same seed under each change
  the insured could make: autonomy, governance, data sensitivity and vendor indemnity
  levels, plus "fix every red-team failure and re-test".
- **Monte Carlo graphic**: sample policy years taken straight from the simulation. On
  the web page, "Run simulation" re-runs the engine with a fresh random seed, so the
  lines, the cloud and the expected-loss estimate genuinely change. Only the
  official quote is fixed on seed 42.

## The archive for each quote

`agents/runs/<id>/pricing/calculation.json` (and a readable `calculation.md`) holds:
- `meta`: provenance (timestamp, git commit, parameter versions, SHA-256 of every
  parameter, engine and experience file, seed and simulation size, reproducibility check).
- `plan` and `plans`: the quoted plan and every plan priced for comparison.
- `red_team_credibility`: counts, μ, A, Z, observed and credibility-weighted shares,
  posterior α, and every portfolio insured's shrinkage.
- `perils`, `systemic`, `premium_build_up`, `loss_distribution`, `tornado`, `controls`,
  `simulation_illustration`, and `equations`.

To reproduce a quote, check out the recorded commit (the hashes confirm the files
match) and run `python -m agents.pricer <insured.yaml>`.
