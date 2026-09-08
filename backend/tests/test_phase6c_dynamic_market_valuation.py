from __future__ import annotations

import json
import sys
import threading
import urllib.request
from dataclasses import asdict
from http.server import HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from backend.src.gemini_client import GeminiTemporaryUnavailable
from backend.src.market_evidence import (
    clear_market_cache,
    condition_group_from_exterior_grade,
    grounded_sources_from_payload,
    retrieve_current_market_evidence,
    validate_market_observations,
)
from backend.src.phone_catalogue import match_catalogue_candidate
from backend.src.server import ReValueHandler
from backend.src.valuation import value_from_market_observations


def assert_equal(actual, expected, message: str) -> None:
    if actual != expected:
        raise AssertionError(f"{message}: expected {expected!r}, got {actual!r}")


def confirmed_s24_ultra() -> dict:
    match = match_catalogue_candidate("Samsung", "Galaxy S24 Ultra")["catalogue_match"]
    return {
        "catalogue_id": match["catalogue_id"],
        "brand": "Samsung",
        "model": "Galaxy S24 Ultra",
        "storage": "256GB",
    }


def fixture_rows() -> list[dict]:
    base = "https://example.invalid/market/s24u"
    rows = [
        {
            "source": "Example Marketplace MY",
            "source_url": f"{base}/1",
            "source_type": "marketplace",
            "observed_at": "2026-09-07",
            "country": "Malaysia",
            "currency": "RM",
            "brand": "Samsung",
            "model": "Galaxy S24 Ultra",
            "storage": "256GB",
            "condition_text": "clean used phone, all visible exterior clean",
            "condition_group": "clean",
            "price": 1100,
            "price_type": "asking_price",
            "listing_title": "Samsung Galaxy S24 Ultra 256GB",
        },
        {
            "source": "Example Marketplace MY",
            "source_url": f"{base}/2",
            "source_type": "marketplace",
            "observed_at": "2026-09-07",
            "country": "MY",
            "currency": "MYR",
            "brand": "Samsung",
            "model": "Galaxy S24 Ultra",
            "storage": "256GB",
            "condition_text": "clean",
            "condition_group": "clean",
            "price": 1137,
            "price_type": "asking_price",
            "listing_title": "S24 Ultra 256GB used",
        },
        {
            "source": "Example Used Retailer",
            "source_url": f"{base}/3",
            "source_type": "used_retailer",
            "observed_at": "",
            "country": "Malaysia",
            "currency": "MYR",
            "brand": "Samsung",
            "model": "Galaxy S24 Ultra",
            "storage": "256GB",
            "condition_text": "A-like clean used",
            "condition_group": "clean",
            "price": 1300,
            "price_type": "used_retail_price",
            "listing_title": "Samsung S24 Ultra 256GB used retail",
        },
        {
            "source": "Example Refurbished MY",
            "source_url": f"{base}/4",
            "source_type": "refurbished_retailer",
            "observed_at": "2026-09-07",
            "country": "MY",
            "currency": "MYR",
            "brand": "Samsung",
            "model": "Galaxy S24 Ultra",
            "storage": "256GB",
            "condition_text": "clean refurbished",
            "condition_group": "clean",
            "price": 1463,
            "price_type": "refurbished_retail_price",
            "listing_title": "Galaxy S24 Ultra 256GB refurbished",
        },
        {
            "source": "Example Marketplace MY",
            "source_url": f"{base}/5",
            "source_type": "marketplace",
            "observed_at": "2026-09-07",
            "country": "Malaysia",
            "currency": "MYR",
            "brand": "Samsung",
            "model": "Galaxy S24 Ultra",
            "storage": "256GB",
            "condition_text": "clean",
            "condition_group": "clean",
            "price": 1600,
            "price_type": "asking_price",
            "listing_title": "Galaxy S24 Ultra 256GB",
        },
        {
            "source": "Example Marketplace MY",
            "source_url": f"{base}/5",
            "source_type": "marketplace",
            "observed_at": "2026-09-07",
            "country": "Malaysia",
            "currency": "MYR",
            "brand": "Samsung",
            "model": "Galaxy S24 Ultra",
            "storage": "256GB",
            "condition_text": "clean",
            "condition_group": "clean",
            "price": 1600,
            "price_type": "asking_price",
            "listing_title": "Duplicate Galaxy S24 Ultra 256GB",
        },
        {
            "source": "Example Trade In",
            "source_url": f"{base}/trade",
            "source_type": "trade_in_provider",
            "observed_at": "2026-09-07",
            "country": "MY",
            "currency": "MYR",
            "brand": "Samsung",
            "model": "Galaxy S24 Ultra",
            "storage": "256GB",
            "condition_text": "trade-in estimate",
            "condition_group": "clean",
            "price": 900,
            "price_type": "trade_in_offer",
            "listing_title": "S24 Ultra trade-in",
        },
        {
            "source": "Example Repair",
            "source_url": f"{base}/repair",
            "source_type": "repair_provider",
            "observed_at": "2026-09-07",
            "country": "MY",
            "currency": "MYR",
            "brand": "Samsung",
            "model": "Galaxy S24 Ultra",
            "storage": "",
            "condition_text": "back glass repair reference",
            "condition_group": "repair",
            "price": 600,
            "price_type": "repair_cost",
            "listing_title": "Galaxy S24 Ultra repair cost",
        },
        {
            "source": "Example Marketplace MY",
            "source_url": f"{base}/wrong-model",
            "source_type": "marketplace",
            "observed_at": "2026-09-07",
            "country": "MY",
            "currency": "MYR",
            "brand": "Samsung",
            "model": "Galaxy S24",
            "storage": "256GB",
            "condition_text": "clean",
            "condition_group": "clean",
            "price": 800,
            "price_type": "asking_price",
            "listing_title": "Samsung Galaxy S24 256GB",
        },
        {
            "source": "Example Marketplace MY",
            "source_url": f"{base}/wrong-storage",
            "source_type": "marketplace",
            "observed_at": "2026-09-07",
            "country": "MY",
            "currency": "MYR",
            "brand": "Samsung",
            "model": "Galaxy S24 Ultra",
            "storage": "512GB",
            "condition_text": "clean",
            "condition_group": "clean",
            "price": 1700,
            "price_type": "asking_price",
            "listing_title": "Samsung Galaxy S24 Ultra 512GB",
        },
        {
            "source": "Example Shop",
            "source_url": f"{base}/case",
            "source_type": "retailer",
            "observed_at": "2026-09-07",
            "country": "MY",
            "currency": "MYR",
            "brand": "Samsung",
            "model": "Galaxy S24 Ultra",
            "storage": "256GB",
            "condition_text": "phone case only",
            "condition_group": "clean",
            "price": 59,
            "price_type": "asking_price",
            "listing_title": "Galaxy S24 Ultra case",
        },
        {
            "source": "Example Shop",
            "source_url": f"{base}/monthly",
            "source_type": "retailer",
            "observed_at": "2026-09-07",
            "country": "MY",
            "currency": "MYR",
            "brand": "Samsung",
            "model": "Galaxy S24 Ultra",
            "storage": "256GB",
            "condition_text": "monthly instalment",
            "condition_group": "clean",
            "price": 120,
            "price_type": "asking_price",
            "listing_title": "S24 Ultra monthly installment",
        },
    ]
    return rows


def fixture_grounded_sources() -> list[dict]:
    return [
        {"title": row["listing_title"], "url": row["source_url"], "source_kind": "google_search_grounding"}
        for row in fixture_rows()
    ]


class FixtureProvider:
    def __init__(self) -> None:
        self.calls = 0

    def retrieve(self, confirmed_device: dict, condition_group: str) -> dict:
        self.calls += 1
        return {"market_observations": fixture_rows(), "grounded_sources": fixture_grounded_sources()}


class RetryableFailureProvider:
    def retrieve(self, confirmed_device: dict, condition_group: str) -> dict:
        raise GeminiTemporaryUnavailable("503 UNAVAILABLE")


def test_confirmed_device_requirement() -> None:
    result = retrieve_current_market_evidence(None, "A")
    assert_equal(result["status"], "ready", "status endpoint readiness")
    bad = retrieve_current_market_evidence({"brand": "Samsung"}, "A")
    assert_equal(bad["status"], "error", "bad request status")
    assert_equal(bad["error"], "catalogue_id_required", "confirmed catalogue id required")


def test_condition_mapping_and_no_hidden_functional_defect() -> None:
    assert_equal(condition_group_from_exterior_grade("A"), "clean", "A condition mapping")
    assert_equal(condition_group_from_exterior_grade("B"), "normal_used", "B condition mapping")
    assert_equal(condition_group_from_exterior_grade("C"), "damaged_working", "C condition mapping")
    result = retrieve_current_market_evidence(confirmed_s24_ultra(), "C", provider=FixtureProvider(), use_cache=False)
    assert_equal(result["condition_group"], "damaged_working", "C retrieval condition")
    assert_equal(result["internal_assessment_status"], "not_assessed", "internal check must not be fabricated")
    assert result["condition_group"] != "functional_defect"


def test_evidence_validation_filtering_and_separation() -> None:
    rows, excluded = validate_market_observations(
        fixture_rows(),
        confirmed_device=confirmed_s24_ultra(),
        condition_group="clean",
        retrieved_at="2026-09-07T00:00:00+00:00",
        grounded_sources=grounded_sources_from_payload({"grounded_sources": fixture_grounded_sources()}),
    )
    market_rows = [row for row in rows if row.usable_for_market_range]
    trade_in = [row for row in rows if row.price_type == "trade_in_offer"]
    repair = [row for row in rows if row.price_type == "repair_cost"]
    assert_equal(len(market_rows), 5, "usable exact model/storage resale rows")
    assert_equal(len(trade_in), 1, "trade-in separated")
    assert_equal(len(repair), 1, "repair separated")
    reasons = {row.exclusion_reason for row in excluded}
    for reason in {"duplicate_listing", "wrong_model_or_variant", "wrong_or_missing_storage", "accessory_only", "instalment_only"}:
        assert reason in reasons


def test_market_retrieval_cache_behavior() -> None:
    clear_market_cache()
    provider = FixtureProvider()
    first = retrieve_current_market_evidence(confirmed_s24_ultra(), "A", provider=provider)
    second = retrieve_current_market_evidence(confirmed_s24_ultra(), "A", provider=provider)
    assert_equal(first["cache_hit"], False, "first retrieval should not be cache hit")
    assert_equal(second["cache_hit"], True, "second retrieval should use cache")
    assert_equal(provider.calls, 1, "provider should be called once")
    clear_market_cache()


def test_q1_median_q3_and_rm50_rounding() -> None:
    retrieval = retrieve_current_market_evidence(confirmed_s24_ultra(), "A", provider=FixtureProvider(), use_cache=False)
    valuation = value_from_market_observations(
        observations=retrieval["market_observations"],
        brand="Samsung",
        model="Galaxy S24 Ultra",
        storage_gb=256,
        condition_group=retrieval["condition_group"],
        trade_in_benchmark=retrieval["trade_in_benchmarks"],
        repair_references=retrieval["repair_references"],
    )
    assert_equal(valuation.status, "estimated_market_value", "valuation status")
    assert_equal(valuation.evidence_status, "strong_local_evidence", "evidence strength")
    assert_equal(valuation.distribution["q1"], 1137.0, "Q1 calculation")
    assert_equal(valuation.distribution["median"], 1300.0, "median calculation")
    assert_equal(valuation.distribution["q3"], 1463.0, "Q3 calculation")
    assert_equal(valuation.value_low, 1150.0, "RM50 rounded low")
    assert_equal(valuation.value_high, 1450.0, "RM50 rounded high")
    assert_equal(valuation.midpoint, 1300.0, "RM50 rounded midpoint")
    assert_equal(len(valuation.trade_in_benchmark), 1, "trade-in excluded from Q1-Q3")
    assert_equal(len(valuation.repair_references), 1, "repair excluded from Q1-Q3")


def test_insufficient_evidence_behavior() -> None:
    retrieval = retrieve_current_market_evidence(confirmed_s24_ultra(), "A", provider=FixtureProvider(), use_cache=False)
    only_two = retrieval["market_observations"][:2]
    valuation = value_from_market_observations(
        observations=only_two,
        brand="Samsung",
        model="Galaxy S24 Ultra",
        storage_gb=256,
        condition_group="clean",
    )
    assert_equal(valuation.status, "insufficient_market_evidence", "less than three exact rows should be insufficient")
    assert_equal(valuation.value_low, None, "no invented low value")
    assert_equal(valuation.value_high, None, "no invented high value")


def test_retryable_market_failure() -> None:
    result = retrieve_current_market_evidence(confirmed_s24_ultra(), "A", provider=RetryableFailureProvider(), use_cache=False)
    assert_equal(result["status"], "temporarily_unavailable", "retryable market status")
    assert_equal(result["retryable"], True, "retryable flag")
    assert_equal(result["market_observations"], [], "no fabricated rows")


def test_internal_live_valuation_consumes_validated_fixture_evidence() -> None:
    retrieval = retrieve_current_market_evidence(confirmed_s24_ultra(), "A", provider=FixtureProvider(), use_cache=False)
    valuation = value_from_market_observations(
        observations=retrieval["market_observations"],
        brand=retrieval["query_device"]["brand"],
        model=retrieval["query_device"]["model"],
        storage_gb=256,
        condition_group=retrieval["condition_group"],
        trade_in_benchmark=retrieval["trade_in_benchmarks"],
        repair_references=retrieval["repair_references"],
    )
    assert_equal(valuation.status, "estimated_market_value", "internal valuation status")
    assert_equal(valuation.value_low, 1150.0, "internal valuation rounded low")
    assert_equal(valuation.value_high, 1450.0, "internal valuation rounded high")


def test_endpoint_retrieval_without_key_keeps_live_valuation_available() -> None:
    clear_market_cache()
    server = HTTPServer(("127.0.0.1", 0), ReValueHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        retrieve_payload = json.dumps({"confirmed_device": confirmed_s24_ultra(), "exterior_grade": "A"}).encode(
            "utf-8"
        )
        retrieve_request = urllib.request.Request(
            f"http://127.0.0.1:{port}/market-evidence/retrieve",
            data=retrieve_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        retrieve_body = json.loads(urllib.request.urlopen(retrieve_request, timeout=10).read().decode("utf-8"))
        assert retrieve_body["status"] in {"blocked", "ok", "insufficient_market_evidence", "temporarily_unavailable"}
        if retrieve_body["status"] == "blocked":
            assert_equal(retrieve_body["market_observations"], [], "blocked market endpoint must not fabricate rows")

        live_payload = json.dumps({"confirmed_device": confirmed_s24_ultra(), "exterior_grade": "A"}).encode("utf-8")
        live_request = urllib.request.Request(
            f"http://127.0.0.1:{port}/live-valuation",
            data=live_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        live_body = json.loads(urllib.request.urlopen(live_request, timeout=10).read().decode("utf-8"))
        if retrieve_body["status"] == "blocked":
            assert live_body["status"] in {"estimated_market_value", "prototype_market_estimate", "insufficient_market_evidence"}
            if live_body["status"] != "insufficient_market_evidence":
                assert live_body["value_low"] is not None
                assert live_body["value_high"] is not None
                assert live_body.get("fallback_source") in {
                    "local_s24_ultra_public_market_seed_v0.1",
                    "local_malaysia_calibration_v0.1",
                }
                assert_equal(live_body.get("live_retrieval_status"), "blocked", "fallback should preserve retrieval status")
    finally:
        server.shutdown()


def test_environment_blocked_live_retrieval_without_key() -> None:
    clear_market_cache()
    result = retrieve_current_market_evidence(confirmed_s24_ultra(), "A", use_cache=False)
    assert result["status"] in {"blocked", "temporarily_unavailable", "ok", "insufficient_market_evidence"}
    if result["status"] == "blocked":
        assert_equal(result["market_observations"], [], "blocked retrieval should not fabricate rows")


def main() -> int:
    test_confirmed_device_requirement()
    test_condition_mapping_and_no_hidden_functional_defect()
    test_evidence_validation_filtering_and_separation()
    test_market_retrieval_cache_behavior()
    test_q1_median_q3_and_rm50_rounding()
    test_insufficient_evidence_behavior()
    test_retryable_market_failure()
    test_internal_live_valuation_consumes_validated_fixture_evidence()
    test_endpoint_retrieval_without_key_keeps_live_valuation_available()
    test_environment_blocked_live_retrieval_without_key()
    print("phase6c dynamic market valuation tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
