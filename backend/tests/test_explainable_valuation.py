from __future__ import annotations

import json
import sys
import threading
from copy import deepcopy
from dataclasses import asdict
from http.server import HTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.src import server as server_module
from backend.src.local_valuation import local_fallback_valuation
from backend.src.market_evidence import canonicalize_confirmed_device
from backend.src.phone_catalogue import match_catalogue_candidate
from backend.src.valuation import value_from_market_observations
from backend.src.valuation_explainability import with_valuation_explainability


DEVICE = {"brand": "Samsung", "model": "Galaxy S24 Ultra", "storage": "256GB"}


def observations() -> list[dict]:
    return [{
        "brand": DEVICE["brand"], "model": DEVICE["model"], "storage_gb": 256,
        "country": "MY", "currency": "MYR", "price": price,
        "price_type": "asking_price", "condition_group": "clean" if i < 5 else "normal_used",
        "source": "Fixture", "source_url": f"https://example.invalid/listing/{i}",
        "observed_at": "2026-09-08", "freshness_status": "stale", "grounding_verified": True,
    } for i, price in enumerate([2000, 2050, 2100, 2150, 2200, 1800, 1900])]


def market_result(rows: list[dict] | None = None, **kwargs) -> dict:
    return asdict(value_from_market_observations(
        observations=observations() if rows is None else rows, brand=DEVICE["brand"],
        model=DEVICE["model"], storage_gb=256, condition_group="clean", **kwargs,
    ))


def assert_additive(original: dict, decorated: dict) -> None:
    assert {k: v for k, v in decorated.items() if k != "explainability"} == original
    for key in ["value_low", "value_high", "midpoint", "status", "exact_model_count", "condition_matched_count"]:
        assert decorated[key] == original[key], key
    json.dumps(decorated, allow_nan=False)


def test_normal_market_evidence_and_full_invariance() -> None:
    original = market_result()
    before = deepcopy(original)
    result = with_valuation_explainability(original, confirmed_device=DEVICE, exterior_grade="A", condition_group="clean")
    assert_additive(before, result)
    assert original == before
    assert (result["value_low"], result["value_high"], result["midpoint"]) == (2050, 2150, 2100)
    x = result["explainability"]
    assert x["device"] == DEVICE
    assert x["valuation"]["value_low"] == result["value_low"]
    assert x["factors"][1]["value"] == "Grade A"
    assert x["factors"][1]["condition_group"] == "clean"
    assert x["evidence"]["exact_model_count"] == 7
    assert x["evidence"]["condition_matched_count"] == 5
    assert x["evidence"]["selected_comparable_count"] == 5
    assert x["evidence"]["strength_label"] == "Strong local evidence"
    assert x["evidence"]["sources"] == original["sources"]
    assert x["evidence"]["statistics"]["q1"] == 2050
    assert "stale" in " ".join(x["caveats"])
    x["evidence"]["sources"][0]["source"] = "Edited explanation"
    assert original == before


def test_moderate_and_limited_evidence_explain_actual_selection() -> None:
    for count, expected in [(7, "Moderate local evidence"), (3, "Limited local evidence")]:
        rows = observations()[:count]
        for row in rows:
            row["condition_group"] = "normal_used"
        original = market_result(rows)
        result = with_valuation_explainability(original, confirmed_device=DEVICE, exterior_grade="A")
        assert_additive(original, result)
        x = result["explainability"]
        assert x["evidence"]["strength_label"] == expected
        assert x["evidence"]["condition_matched_count"] == 0
        assert "condition-matched evidence is limited" in x["method"]["summary"]


def test_local_fallbacks_preserve_prices_and_method_provenance() -> None:
    for brand, model in [("Apple", "iPhone 13 Pro"), ("Samsung", "Galaxy S23 Ultra"), ("Samsung", "Galaxy S24 Ultra")]:
        device = {"brand": brand, "model": model, "storage": "256GB"}
        for grade in ["A", "B", "C", "C-"]:
            original = local_fallback_valuation(confirmed_device=device, exterior_grade=grade)
            result = with_valuation_explainability(original, confirmed_device=device, exterior_grade=grade)
            assert_additive(original, result)
            x = result["explainability"]
            assert x["device"] == device
            assert x["evidence"]["origin"].startswith("local_")
            assert "not refreshed" in " ".join(x["caveats"])
            if original["fallback_source"] == "local_malaysia_calibration_v0.1":
                for key in ["range_method", "main_value_drivers", "explanation"]:
                    assert x["method"][key] == original["distribution"][key]
                assert x["evidence"]["selected_observation_ids"] == original["distribution"]["selected_observation_ids"]
                assert x["evidence"]["selected_comparable_count"] == len(original["distribution"]["selected_observation_ids"])
                assert "grade-derived defect flags" in x["method"]["summary"]


def test_insufficient_evidence_has_no_invented_price() -> None:
    unsupported = {"brand": "Samsung", "model": "Galaxy S24", "storage": "512GB"}
    cases = [(market_result(observations()[:2]), DEVICE), (local_fallback_valuation(
        confirmed_device=unsupported, exterior_grade="A"), unsupported)]
    for original, device in cases:
        result = with_valuation_explainability(original, confirmed_device=device, exterior_grade="A")
        assert_additive(original, result)
        x = result["explainability"]
        assert result["value_low"] is result["value_high"] is result["midpoint"] is None
        assert x["valuation"]["value_low"] is None
        assert x["method"]["type"] == "unavailable"
        assert "insufficient reliable" in x["method"]["summary"]
        assert x["evidence"]["strength_label"] == "Insufficient market evidence"
        assert x["device"] == device
        if original.get("fallback_source") == "none_available":
            assert x["evidence"]["origin"] == "no_local_evidence"
            assert not any("Local evidence was used" in note for note in x["caveats"])


def test_internal_grade_and_damage_are_not_numeric_price_drivers() -> None:
    x = with_valuation_explainability(market_result(), confirmed_device=DEVICE, exterior_grade="A")["explainability"]
    assert {f["key"] for f in x["factors"]} == {"device_configuration", "exterior_condition", "market_evidence", "evidence_strength"}
    assert not any("adjustment" in f or "delta" in f or "internal" in f["key"] for f in x["factors"])
    assert "not used to calculate" in " ".join(x["caveats"])
    assert "no separate per-damage RM deduction" in " ".join(x["caveats"])


def test_trade_in_and_repair_remain_separate_from_distribution() -> None:
    baseline = market_result()
    original = market_result(trade_in_benchmark=[{"price": 999}], repair_references=[{"price": 888}])
    result = with_valuation_explainability(original, confirmed_device=DEVICE, exterior_grade="A")
    assert_additive(original, result)
    assert original["distribution"] == baseline["distribution"]
    assert result["value_low"] == baseline["value_low"]
    assert result["explainability"]["separate_context"] == {
        "trade_in_benchmark_count": 1, "repair_reference_count": 1,
        "summary": "Shown separately and not included in the resale market range.",
    }


def post(port: int, payload: dict) -> dict:
    req = Request(f"http://127.0.0.1:{port}/live-valuation", data=json.dumps(payload).encode(),
                  headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=10) as response:
        return json.load(response)


def test_http_contract_for_market_fallback_insufficient_and_invalid_requests() -> None:
    server = HTTPServer(("127.0.0.1", 0), server_module.ReValueHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    confirmed = {**DEVICE, "catalogue_id": match_catalogue_candidate(DEVICE["brand"], DEVICE["model"])["catalogue_match"]["catalogue_id"]}
    request = {"confirmed_device": confirmed, "exterior_grade": "A"}
    try:
        evidence = {"status": "ok", "query_device": DEVICE, "condition_group": "clean",
                    "market_observations": observations(), "grounded_sources": []}
        with patch.object(server_module, "retrieve_current_market_evidence", return_value=evidence):
            body = post(port, request)
            assert_additive({**market_result(), "grounded_sources": []}, body)
        evidence["market_observations"] = observations()[:2]
        with patch.object(server_module, "retrieve_current_market_evidence", return_value=evidence):
            body = post(port, request)
            assert body["status"] == "insufficient_market_evidence"
            assert body["explainability"]["valuation"]["value_low"] is None
        for device in [confirmed, {"brand": "Samsung", "model": "Galaxy S24", "storage": "512GB"}]:
            blocked = {"status": "blocked", "query_device": device, "error": "credential unavailable"}
            with patch.object(server_module, "retrieve_current_market_evidence", return_value=blocked):
                body = post(port, {**request, "confirmed_device": device})
                original = local_fallback_valuation(confirmed_device=device, exterior_grade="A", fallback_reason="blocked")
                original.update(live_retrieval_status="blocked", live_retrieval_error="credential unavailable")
                assert_additive(original, body)
                assert post(port, {**request, "confirmed_device": device, "internal_grade": "F"}) == body
        assert post(port, {"exterior_grade": "D"}) == {"status": "error", "error": "valid_exterior_grade_required"}
        assert post(port, {"exterior_grade": "A"})["error"] == "confirmed_device_required"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_canonical_identity_when_request_omits_brand_model() -> None:
    confirmed = {"catalogue_id": match_catalogue_candidate("Samsung", "Galaxy S24 Ultra")["catalogue_match"]["catalogue_id"], "storage": "256 GB"}
    ok, _, canonical = canonicalize_confirmed_device(confirmed)
    assert ok and canonical
    body = with_valuation_explainability(local_fallback_valuation(confirmed_device=canonical, exterior_grade="A"),
                                       confirmed_device=canonical, exterior_grade="A")
    assert body["explainability"]["device"] == DEVICE


def main() -> int:
    tests = [value for key, value in globals().items() if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
    print(f"explainable valuation tests passed: {len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
