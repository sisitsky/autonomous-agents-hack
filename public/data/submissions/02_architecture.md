# ShopBot architecture and controls (FICTIONAL)

## Model
ShopBot runs on Acme AI's hosted LLM API (the "Acme Large" model), called from
our backend. We do not fine-tune or self-host. Our contract with Acme AI has no
IP indemnity clause.

## Data
The bot's context includes the customer's messages and, after an order lookup,
the order record: name, email, shipping address, items, and the last four digits
of the payment card. No full card numbers or health data. Conversations are
logged for 90 days.

## Human review
Responses go straight to customers. Our support team reviews a random 5% sample
of conversations each week and every conversation a customer flags. Refunds and
exceptions are always processed by a human agent.

## Governance
- Weekly quality review of sampled conversations.
- Incident response runbook for chatbot errors, owned by the Head of Support.
- No formal alignment with NIST AI RMF or ISO 42001 yet.
- Prompt and model changes are deployed by the web team without a formal review step.
