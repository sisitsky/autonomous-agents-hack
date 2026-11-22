import copy
import json

import pytest

from agents import calibrator, validator
from engine.bayes import PRIOR_WEIGHT, STRENGTH, update_pert
from engine.cli import load_insured
from engine.model import load_params, price


def test_bayes_update_is_a_weighted_average():
    spec = {"dist": "pert", "min": 0.001, "mode": 0.004, "max": 0.01}
    new = update_pert(spec, 0.5, "strong")
    w0, we = PRIOR_WEIGHT, STRENGTH["strong"]
    assert new["mode"] == pytest.approx((w0 * 0.004 + we * 0.006) / (w0 + we))
    assert new["min"] / 0.001 == pytest.approx(new["mode"] / 0.004)       # shape kept
    assert new["evidence_weight"] == w0 + we                                # later evidence moves it less
    again = update_pert({**spec, **{k: new[k] for k in ("min", "mode", "max")}, "evidence_weight": new["evidence_weight"]}, 0.5, "strong")
    assert again["mode"] / new["mode"] < new["mode"] / spec["mode"]


def test_bayes_update_clips_and_bounds():
    spec = {"dist": "pert", "min": 0.2, "mode": 0.4, "max": 0.49}
    new = update_pert(spec, 5.0, "strong", bounds=(0.0, 0.5))               # implied change capped at +100%
    assert new["max"] <= 0.5 and new["min"] <= new["mode"] <= new["max"]


def test_higher_risk_never_lowers_price():
    """Claim-level common random numbers: a parameter increase can't reduce simulated loss."""
    p = load_params(); ins = load_insured("shopbot")
    base = price(ins, p, n_outer=400, n_inner=100)
    for path in ("prompt_injection", "data_leakage"):
        q = copy.deepcopy(p)
        q["priors"]["perils"][path]["frequency"]["p_harm_given_failure"]["mode"] *= 1.01
        r = price(ins, q, n_outer=400, n_inner=100)
        assert r["by_peril"][path]["expected_loss"] >= base["by_peril"][path]["expected_loss"]


def test_propose_applies_decisions_and_cites_source():
    p = load_params()
    items = [{"id": "manual:x", "source": "manual", "type": "court", "title": "Chatbot ruling", "url": "https://e.x", "date": "2026-11-20"}]
    d = [{"item_id": "manual:x", "relevant": True, "parameter": "perils.unauthorized_commitment.frequency.p_claim_given_harm",
          "implied_change": 0.4, "strength": "strong", "event_description": "A court held...", "reasoning": "Because..."}]
    prop = calibrator.propose(items, p, d)
    (c,) = prop["changes"]
    assert c["new"]["mode"] > c["old"]["mode"] and c["source_url"] == "https://e.x"
    irrelevant = [dict(d[0], relevant=False)]
    assert calibrator.propose(items, p, irrelevant)["changes"] == []


def test_validator_passes_small_change_and_flags_large(tmp_path):
    p = load_params()
    items = [{"id": "m:1", "source": "manual", "type": "court", "title": "t", "url": "u", "date": "d"}]
    small = calibrator.propose(items, p, [{"item_id": "m:1", "relevant": True,
                                          "parameter": "perils.unauthorized_commitment.frequency.p_claim_given_harm",
                                          "implied_change": 0.3, "strength": "moderate", "event_description": "", "reasoning": ""}])
    calibrator.write_priors(small["changes"], tmp_path / "a", "t")
    v = validator.validate(tmp_path / "a")
    assert v["passed"], v["problems"]
    assert v["accumulation"]["after"]["by_provider"]
    big = copy.deepcopy(small["changes"])
    for c in big:
        c["new"] = {k: v_ * 4 for k, v_ in c["old"].items()}
    calibrator.write_priors(big, tmp_path / "b", "t")
    assert not validator.validate(tmp_path / "b")["passed"]
