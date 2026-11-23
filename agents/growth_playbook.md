# Growth playbook (Loop 3, run through Egbe)

The growth agent rewrites this file after each cycle. Humans approve outreach templates.
Conversion data may change targeting and messaging only, never price.

## Current hypothesis (cycle 1)
- Segment: lead with B2B AI SaaS companies (`b2b-ai-saas`) that ship customer-facing chatbots or agents. In cycle 1 this segment had the best conversion at every funnel step and the lowest cost per purchase ($4.17 vs $12.50 for the other two). Keep CX deployers and companion-chatbot builders as smaller secondary tests.
- Offer: unchanged. Free AI risk score (intake + red-team sample + indicative premium); paid full report via Egbe Stripe checkout. No pricing changes.
- Channels: LinkedIn ads is the primary channel (best result in `b2b-ai-saas`). Communities stays as a small test, since it was tied for the weakest cost per purchase and was only tested with one segment, so the channel and the segment are confounded. Founder outreach continues for companies with public AI products, using human-approved templates only.
- Budget shift (within guardrail caps): about 60% to `b2b-ai-saas` on LinkedIn, about 20% to `cx-deployers` on LinkedIn, about 20% to a communities test. Companion-chatbots on communities drops to a minimal or paused test unless the next test below changes that.
- Success metric: risk-score signups per $ spent, then report purchases and cost per purchase.
- Messaging changes to test:
  - For `b2b-ai-saas`, lead with the indicative premium and red-team findings, since this segment moves most readily from score to checkout.
  - For `companion-chatbots`, the score-to-checkout rate (14%) is the weakest step, so test copy that explains what the paid report contains before the score is delivered.
  - For `cx-deployers`, the weak step is checkout-to-purchase (33%), so test checkout-page messaging (what is in the report) rather than more traffic.
- Next tests:
  1. Run `b2b-ai-saas` on communities and `companion-chatbots` on LinkedIn to separate channel effects from segment effects.
  2. Scale `b2b-ai-saas` spend in steps, watching whether cost per purchase holds.
  3. Compare two message variants per segment on the weakest funnel step.
- Caveat: sample sizes are very small (2 to 6 purchases per segment, $25 each). Differences are directional, not statistically reliable. Treat the budget shift as a bet to be confirmed, not a conclusion.

## Limits (from params/guardrails.yaml)
- Daily ad spend, total spend, and daily outreach volume caps apply.
- Every outreach template needs human approval before first send, including any new message variants above.
- Growth data may change targeting and messaging only, never price.

## Cycle log
| Cycle | Segment | Channel | Spend | Signups | Purchases | Change for next cycle |
|---|---|---|---|---|---|---|
| 1 (SIMULATED data) | cx-deployers (`iacta-c1-cx-deployers`) | linkedin_ads | $25.00 | 30 | 2 | Reduce to about 20% of budget; test checkout-page messaging (checkout-to-purchase 33%). Cost per purchase $12.50. |
| 1 (SIMULATED data) | b2b-ai-saas (`iacta-c1-b2b-ai-saas`) | linkedin_ads | $25.00 | 41 | 6 | Lead segment; raise to about 60% of budget and scale stepwise. Cost per purchase $4.17, $294 revenue. Also test on communities. |
| 1 (SIMULATED data) | companion-chatbots (`iacta-c1-companion-chatbots`) | communities | $25.00 | 35 | 2 | Cut to a minimal test; try the segment on LinkedIn to separate channel from segment, and test report-explainer copy (score-to-checkout 14%). Cost per purchase $12.50. |

Note: all cycle 1 figures above come from SIMULATED conversion data, not real customers or real spend.
