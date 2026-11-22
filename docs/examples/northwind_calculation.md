# Calculation record: ShopBot (customer service chatbot)

> Illustrative pricing on illustrative assumptions. This is a pricing and risk-assessment engine, not an offer of insurance.

## Provenance

- Priced at: 2026-11-22T20:38:10+00:00
- Code version (git): `2efefc196fda94a93d5de9850662f2b1a6370f62`
- Parameter versions: priors 0.3.0, rating_factors 0.2.0, pricing 0.1.0
- Simulation: seed 42, 2,000 parameter draws × 200 years = 400,000 simulated years
- Reproducibility: passed: priced twice, identical results
- Policy: deductible $10,000, $1,000,000 per claim, $2,000,000 aggregate
- Model provider bucket: provider_a

## Premium build-up

| Step | Amount | Equation |
|---|---|---|
| Expected loss (after deductible and limits) | $362,556 | `EL = mean over simulated years of min(idiosyncratic + systemic, A)` |
| Parameter-uncertainty load (P75 − mean) | $109,568 | `U = max(0, P_k(EL by parameter draw) − EL)` |
| Risk capital (TVAR at 99.5%) | $356,593 | `C = (TVaR(idio) − mean(idio)) / √n_pool + c × (TVaR(sys) − mean(sys))` |
| Capital load | $35,659 | `K = cost of capital × C` |
| Fixed expense (underwriting, red-team testing) | $2,500 | `G = (EL + U + K + F) / (1 − v − π)` |
| Technical premium (EL + U + K + F) | $510,283 | `G = (EL + U + K + F) / (1 − v − π)` |
| Gross premium | $728,976 | `G = (EL + U + K + F) / (1 − v − π)` |
| Rate on line | 36.4% | `ROL = G / A` |

With the numbers:

- Capital = ($2,000,000 − $361,246) / √400 + 1.80 × ($153,930 − $1,344) = $356,593
- Gross premium = $510,283 / (1 − 0.25 − 0.05) = $728,976

## Perils

### misinformation

Wrong answers or unauthorized commitments made by the AI cause a customer financial loss.

- Frequency: `λ = N × P(error) × P(harm | error) × P(claim | harm) [× r_adv]`
- Exposure: {'outputs_per_year': 2000000}
- Base claims per year (before rating factors): 3.2
- Rating factors: use_case_criticality medium (1.0) × autonomy human_review_sampled (0.85) × governance weak (1.2) × customer_facing public (1.0) = 1.020, capped to [0.5, 3.0] → M = 1.020
- Expected claims per year: 3.26
- Expected loss: $332,429

| Parameter | Distribution | Median | Mean used | Source |
|---|---|---|---|---|
| `frequency.p_error` | beta(alpha 20, beta 13) | 0.6082 | 0.6073 | Insured red-team test (adversarial): 19 failures in 31 cases. |
| `frequency.p_harm_given_error` | pert(min 0.005, mode 0.02, max 0.06) | 0.02319 | 0.02397 | Expert elicitation (illustrative). |
| `frequency.p_claim_given_harm` | pert(min 0.001, mode 0.006, max 0.03) | 0.00843 | 0.008946 | Analog to tech E&O claim propensity, adjusted (illustrative). |
| `frequency.adversarial_to_live` | pert(min 0.003, mode 0.01, max 0.03) | 0.01166 | 0.01216 | Judgment (illustrative), 2026-11-22. Roughly 1 in 100 live outputs faces a situation comparable to an adversarial probe. A bot failing 60% of probes implies about 0.6% live material errors (3x the 0.2% default prior); one failing 5% implies about 0.05%. Replace with paired adversarial vs. live-sample test data as it accumulates. |
| `severity.body.median` | pert(min 20,000, mode 35,000, max 60,000) | 36,274 | 36,709 | Tech E&O analog (illustrative). |
| `severity.body.p90` | pert(min 120,000, mode 200,000, max 350,000) | 208,919 | 211,770 | Tech E&O analog (illustrative). |
| `severity.tail.prob` | pert(min 0.01, mode 0.03, max 0.08) | 0.03383 | 0.03475 | Share of claims that become large (illustrative). |
| `severity.tail.alpha` | pert(min 1.2, mode 1.8, max 2.6) | 1.83 | 1.84 | Pareto shape; lower = fatter tail (illustrative). |

### privacy_regulatory

Data leakage through the AI system leads to a privacy claim or regulatory action.

- Frequency: `λ = rate [× ρ_leak]`
- Exposure: annual rate (no exposure field)
- Base claims per year (before rating factors): 0.02794
- Red-team data-leak relativity ρ_leak: ×2.70
- Rating factors: governance weak (1.2) × data_sensitivity personal (1.0) × jurisdiction us_high_reg (1.2) = 1.440, capped to [0.5, 3.0] → M = 1.440
- Expected claims per year: 0.1087
- Expected loss: $36,090

| Parameter | Distribution | Median | Mean used | Source |
|---|---|---|---|---|
| `frequency.rate` | pert(min 0.005, mode 0.02, max 0.08, AI-specific share (illustrative). n/a) | 0.02575 | 0.02794 | Cyber analog |
| `frequency.leak_rate` | beta(alpha 6, beta 5) | 0.5483 | 0.5501 | Insured red-team data-leak probes: 5 leaks in 9 probes. |
| `frequency.reference_leak_rate` | pert(min 0.05, mode 0.15, max 0.35) | 0.1627 | 0.1661 | Judgment (illustrative), 2026-11-22. A typical customer-facing bot with record access leaks on about 15% of adversarial data probes. Replace with the portfolio's observed leak rate as red-team runs accumulate. |
| `severity.body.median` | pert(min 100,000, mode 200,000, max 400,000) | 212,744 | 216,250 | Cyber analog (illustrative). |
| `severity.body.p90` | pert(min 600,000, mode 1,000,000, max 2,000,000) | 1,076,518 | 1,104,032 | Cyber analog (illustrative). |

### ip_infringement

Generated content infringes third-party IP.

- Frequency: `λ = rate [× ρ_leak]`
- Exposure: annual rate (no exposure field)
- Base claims per year (before rating factors): 0.01331
- Rating factors: governance weak (1.2) × customer_facing public (1.0) × vendor_indemnity none (1.0) = 1.200, capped to [0.5, 3.0] → M = 1.200
- Expected claims per year: 0.01597
- Expected loss: $4,146

| Parameter | Distribution | Median | Mean used | Source |
|---|---|---|---|---|
| `frequency.rate` | pert(min 0.002, mode 0.01, max 0.04) | 0.01281 | 0.01331 | Media liability analog (illustrative). |
| `severity.body.median` | pert(min 50,000, mode 120,000, max 300,000) | 134,030 | 139,615 | Media liability analog (illustrative). |
| `severity.body.p90` | pert(min 400,000, mode 800,000, max 1,500,000) | 838,218 | 849,376 | Media liability analog (illustrative). |

### Systemic

- Expected systemic loss: $1,344

| Parameter | Distribution | Median | Mean used | Source |
|---|---|---|---|---|
| `systemic.annual_prob` | pert(min 0.01, mode 0.03, max 0.1) | 0.03639 | 0.03818 | Scenario workshop (illustrative). |
| `systemic.share_affected` | pert(min 0.1, mode 0.3, max 0.7) | 0.3255 | 0.3328 | Share of a provider's insureds hit in an event (illustrative). |
| `systemic.loss_share_of_limit` | pert(min 0.01, mode 0.04, max 0.15) | 0.05022 | 0.05344 | Loss per affected policy as share of aggregate limit (illustrative). |

## Annual loss distribution

Probability of any loss in a year: 81.2%

| Percentile | Annual loss |
|---|---|
| 50 | $146,053 |
| 75 | $528,688 |
| 90 | $1,080,684 |
| 95 | $1,439,408 |
| 99 | $2,000,000 |
| 99.5 | $2,000,000 |

## Sensitivity (tornado)

Each parameter moved to its P10 and P90 with the rest at their medians (point premium $505,195; excludes the uncertainty load).

| Parameter | Low → high value | Premium at low | Premium at high | Swing |
|---|---|---|---|---|
| `perils.misinformation.frequency.p_claim_given_harm` | 0.003272 → 0.01617 | $256,978 | $868,156 | $611,178 |
| `perils.misinformation.frequency.p_harm_given_error` | 0.01176 → 0.03805 | $303,906 | $758,727 | $454,822 |
| `perils.misinformation.frequency.adversarial_to_live` | 0.006147 → 0.01895 | $312,684 | $752,819 | $440,135 |
| `perils.misinformation.frequency.p_error` | 0.4963 → 0.713 | $430,306 | $574,139 | $143,834 |
| `perils.misinformation.severity.body.p90` | 156,926 → 270,547 | $435,351 | $576,693 | $141,342 |
| `perils.misinformation.severity.tail.prob` | 0.01904 → 0.05273 | $465,233 | $555,358 | $90,125 |
| `perils.privacy_regulatory.frequency.rate` | 0.01174 → 0.0459 | $477,201 | $545,376 | $68,175 |
| `systemic.loss_share_of_limit` | 0.02346 → 0.08791 | $490,673 | $525,648 | $34,975 |
| `perils.misinformation.severity.body.median` | 26,999 → 46,926 | $497,675 | $517,764 | $20,089 |
| `perils.privacy_regulatory.severity.body.median` | 145,908 → 293,337 | $497,742 | $514,129 | $16,388 |

## What would lower the price

Re-priced with the same seed, so differences come only from the change.

| Change | Premium | Saving |
|---|---|---|
| red_team: 19 of 31 failures, 5 of 9 leaks → all failures fixed and re-tested | $106,431 | $622,545 (85%) |
| governance: weak → strong | $536,612 | $192,364 (26%) |
| governance: weak → adequate | $618,774 | $110,202 (15%) |
| autonomy: human_review_sampled → advisory_human_signoff | $625,599 | $103,377 (14%) |
| data_sensitivity: personal → none | $697,821 | $31,155 (4%) |
| vendor_indemnity: none → provider_ip_indemnity | $727,902 | $1,074 (0%) |

## Equations

- **Claim frequency (chain perils)**: `λ = N × P(error) × P(harm | error) × P(claim | harm) [× r_adv]`. Expected claims per year from N outputs per year. r_adv applies only when P(error) comes from an adversarial red-team suite.
- **Claim frequency (rate perils)**: `λ = rate [× ρ_leak]`. A direct annual claim rate. For the privacy peril, red-team data-leak results multiply it.
- **Red-team evidence**: `P(error) ~ Beta(F + 1, T − F + 1)`. F failures in T tested cases, updating a uniform prior. Fewer tests give a wider distribution and a larger uncertainty load. The same form is used for the leak rate on data-leak probes.
- **Adversarial suite adjustment**: `P(error)_live = P(fail | probe) × r_adv`. Stress-test suites over-sample failure modes, so the per-probe failure rate is scaled to a per-live-output rate by an uncertain ratio.
- **Data-leak relativity**: `ρ_leak = clip(L / L_ref, min, max)`. The insured's leak rate on probes divided by a typical system's, bounded.
- **Rating-factor modifier**: `M = clip(∏ f_i, min, max)`. Relativities of the rating factors that apply to the peril, multiplied, then capped so stacked factors can't produce extreme prices.
- **Claims in a simulated year**: `n ~ Poisson(λ × M)`. Claim count per simulated year for one peril.
- **Claim size**: `X ~ Lognormal(μ = ln m, σ = (ln p90 − ln m) / 1.2816); with probability q, X = T × U^(−1/α)`. Lognormal body set by its median m and 90th percentile p90, plus an optional Pareto tail above threshold T with shape α (lower α = fatter tail).
- **Policy terms**: `paid = min(max(X − d, 0), L_claim); annual total ≤ A`. Deductible d and per-claim limit apply to each claim; the aggregate limit A caps the year.
- **Systemic event**: `loss_sys = ℓ × A, if an event occurs (prob p_s) and hits this insured (prob s)`. A correlated failure at the insured's model provider (bad update, new jailbreak, adverse ruling).
- **Expected loss**: `EL = mean over simulated years of min(idiosyncratic + systemic, A)`. Two-layer Monte Carlo: n_outer parameter draws (what we don't know) × n_inner years each (luck).
- **Parameter-uncertainty load**: `U = max(0, P_k(EL by parameter draw) − EL)`. Charges for not knowing the parameters: the gap between the k-th percentile of expected loss across parameter draws and its mean.
- **Risk capital**: `C = (TVaR(idio) − mean(idio)) / √n_pool + c × (TVaR(sys) − mean(sys))`. Independent losses diversify across the pool; systemic losses get no diversification credit.
- **Provider concentration**: `c = provider share of portfolio / average provider share`. Policies on heavily used model providers carry more systemic capital.
- **Capital load**: `K = cost of capital × C`. The cost of holding the risk capital for a year.
- **Gross premium**: `G = (EL + U + K + F) / (1 − v − π)`. Technical premium (losses, loads, fixed expense F) grossed up for variable expenses v and profit π.
- **Rate on line**: `ROL = G / A`. Premium as a share of the aggregate limit.

Full method: `docs/PRICING_METHOD.md`.
