# Pricing agent

Loop 1, step 3. Deterministic code with no LLM. It runs the Monte Carlo engine,
applies the rating factors, computes the sensitivity (tornado) and the changes
that would lower the price, and archives every equation and number used.
Code: `agents/pricer.py`, which calls `engine/`.

All figures are illustrative. This is a pricing and risk-assessment engine, not an
offer of insurance.

```bash
python -m agents.pricer agents/runs/northwind      # after intake and red team
python -m agents.pricer shopbot                    # any benchmark insured
```

Each quote writes `agents/runs/<id>/pricing/`:

- `calculation.json`: the full record for the web app. It holds the premium build-up
  with the inputs to each step, every parameter's distribution, its mean as drawn,
  and its source, the rating factors and cap, tornado, controls, equations
  (plain text and LaTeX), and provenance: git commit, parameter versions,
  SHA-256 hashes, and seed.
- `calculation.md`: the same, readable.
- `quote.json`: headline numbers.

**Reproducible by construction.** The agent prices twice and refuses to archive if
the results differ. Same insured + same parameter files + same seed ⇒ identical
numbers. Recording the calculation trace consumes no random numbers, so benchmark
premiums are unchanged.

The full method, every equation, and where each number lives are in
[docs/PRICING_METHOD.md](docs/PRICING_METHOD.md). A complete example record is in
[docs/examples/northwind_calculation.md](docs/examples/northwind_calculation.md).

Tests: `tests/test_pricer.py` (11 tests). They check reproducibility, the build-up
arithmetic, the rating-factor cap, that every parameter carries a source, that the
method doc covers every equation, provenance hashes, controls, and that the
pricer contains no LLM calls.
