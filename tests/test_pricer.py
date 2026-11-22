import copy
import json
import math
from pathlib import Path

import pytest

from agents import pricer
from engine.audit import EQUATIONS, calculation
from engine.cli import load_insured
from engine.controls import control_options
from engine.model import load_params, price

FAST = dict(n_outer=300, n_inner=100)


@pytest.fixture(scope="module")
def params():
    return load_params()


@pytest.fixture(scope="module")
def tested_shopbot():
    sb = copy.deepcopy(load_insured("shopbot"))
    sb["red_team"] = {"tested": 31, "failures": 19, "suite": "adversarial",
                      "data_leak": {"tested": 9, "failures": 5}}
    sb["factors"]["governance"] = "weak"
    return sb


@pytest.fixture(scope="module")
def archived(tmp_path_factory, tested_shopbot):
    import yaml
    d = tmp_path_factory.mktemp("run")
    (d / "insured.yaml").write_text(yaml.safe_dump(tested_shopbot))
    return pricer.run(str(d))


def test_pricer_uses_no_llm():
    src = Path(pricer.__file__).read_text()
    assert "llm" not in src and "anthropic" not in src


def test_archive_reproduces_engine(archived, tested_shopbot, params):
    c = archived["calculation"]
    assert c["meta"]["reproducibility_check"].startswith("passed")
    again = price(tested_shopbot, params)
    gross = next(s for s in c["premium_build_up"] if s["id"] == "gross_premium")["value"]
    assert gross == again["gross_premium"]


def test_build_up_arithmetic(archived, params):
    s = {x["id"]: x for x in archived["calculation"]["premium_build_up"]}
    tech = s["expected_loss"]["value"] + s["uncertainty_load"]["value"] + s["capital_load"]["value"] \
        + s["fixed_expense"]["value"]
    assert tech == pytest.approx(s["technical_premium"]["value"])
    g = s["gross_premium"]["inputs"]
    assert s["gross_premium"]["value"] == pytest.approx(tech / g["divisor"])
    c = s["capital"]["inputs"]
    cap = (c["idiosyncratic_tail"] - c["idiosyncratic_mean"]) / math.sqrt(c["pool_size"]) \
        + c["concentration_multiplier"] * (c["systemic_tail"] - c["systemic_mean"])
    assert cap == pytest.approx(s["capital"]["value"])
    assert s["capital_load"]["value"] == pytest.approx(s["capital_load"]["inputs"]["cost_of_capital"] * cap)


def test_rating_factors_recorded_and_capped(archived, params):
    mis = archived["calculation"]["perils"]["misinformation"]
    raw = math.prod(f["relativity"] for f in mis["rating_factors"])
    b = mis["modifier_bounds"]
    assert mis["combined_relativity_raw"] == pytest.approx(raw)
    assert mis["modifier"] == pytest.approx(min(max(raw, b["min"]), b["max"]))
    assert all(f["source"] for f in mis["rating_factors"])
    assert mis["expected_claims_per_year"] == pytest.approx(mis["base_claims_per_year"] * mis["modifier"], rel=1e-9)


def test_every_parameter_archived_with_source(archived):
    c = archived["calculation"]
    paths = [p["path"] for peril in c["perils"].values() for p in peril["parameters"]]
    assert "perils.misinformation.frequency.p_error" in paths
    assert "perils.misinformation.frequency.adversarial_to_live" in paths
    assert "perils.privacy_regulatory.frequency.leak_rate" in paths
    for peril in c["perils"].values():
        for p in peril["parameters"]:
            assert p["source"] and "dist" in p["distribution"]


def test_equations_referenced_exist(archived):
    ids = {e["id"] for e in EQUATIONS}
    c = archived["calculation"]
    assert {s["equation"] for s in c["premium_build_up"]} <= ids
    assert {p["equation"] for p in c["perils"].values()} <= ids
    assert all(e["formula"] and e["latex"] and e["meaning"] for e in c["equations"])


def test_method_doc_covers_every_equation():
    doc = Path("docs/PRICING_METHOD.md").read_text()
    for e in EQUATIONS:
        assert f"`{e['id']}`" in doc, e["id"]


def test_provenance_hashes(archived):
    m = archived["calculation"]["meta"]
    assert set(m["params_sha256"]) >= {"params/priors.yaml", "params/rating_factors.yaml", "params/pricing.yaml"}
    assert all(len(h) == 64 for h in m["params_sha256"].values())
    assert m["params_versions"]["priors"]


def test_files_written(archived):
    out = Path(archived["out_dir"])
    calc = json.loads((out / "calculation.json").read_text())
    assert calc["equations"] and calc["premium_build_up"]
    md = (out / "calculation.md").read_text()
    assert "## Premium build-up" in md and "not an offer of insurance" in md
    assert json.loads((out / "quote.json").read_text())["gross_premium"] > 0


def test_controls_lower_price_and_are_sorted(tested_shopbot, params):
    c = control_options(tested_shopbot, params, **FAST)
    savings = [o["saving"] for o in c["options"]]
    assert savings == sorted(savings, reverse=True)
    assert all(s > 0 for s in savings), [(o["factor"], o["to_level"], o["saving"]) for o in c["options"]]
    kinds = {(o["factor"], o["to_level"]) for o in c["options"]}
    assert ("governance", "strong") in kinds
    assert any(o["kind"] == "red_team_remediation" for o in c["options"])
    # Only controllable factors are offered.
    assert not any(o["factor"] in ("jurisdiction", "use_case_criticality") for o in c["options"])


def test_trace_does_not_change_prices(params):
    # Benchmark book prices pinned when per-parameter random streams were introduced;
    # tracing and credibility (adversarial suites only) must not move them.
    from engine.cli import benchmark
    b = benchmark()["insureds"]
    assert round(b["shopbot"]["gross_premium"]) == 201854
    assert round(b["hirescreen"]["gross_premium"]) == 123436
    assert round(b["copydesk"]["gross_premium"]) == 40295
