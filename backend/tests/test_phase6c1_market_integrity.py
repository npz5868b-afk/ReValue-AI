from __future__ import annotations

import json
import sys
import threading
import urllib.request
from http.server import HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from backend.src.gemini_client import extract_grounded_sources
from backend.src.market_evidence import (
    canonicalize_confirmed_device,
    classify_freshness,
    grounded_sources_from_payload,
    retrieve_current_market_evidence,
    validate_market_observations,
)
from backend.src.phone_catalogue import match_catalogue_candidate
from backend.src.server import ReValueHandler


def assert_equal(actual, expected, message: str) -> None:
    if actual != expected:
        raise AssertionError(f"{message}: expected {expected!r}, got {actual!r}")


def confirmed_s24_ultra(**overrides) -> dict:
    match = match_catalogue_candidate("Samsung", "Galaxy S24 Ultra")["catalogue_match"]
    payload = {
        "catalogue_id": match["catalogue_id"],
        "brand": "Samsung",
        "model": "Galaxy S24 Ultra",
        "storage": "256GB",
    }
    payload.update(overrides)
    return payload


def evidence_row(url: str, price_type: str = "asking_price") -> dict:
    return {
        "source": "Grounded Source",
        "source_url": url,
        "source_type": "marketplace",
        "observed_at": "2026-09-01",
        "country": "Malaysia",
        "currency": "MYR",
        "brand": "Samsung",
        "model": "Galaxy S24 Ultra",
        "storage": "256GB",
        "condition_text": "clean used device",
        "condition_group": "clean",
        "price": 1300,
        "price_type": price_type,
        "listing_title": "Samsung Galaxy S24 Ultra 256GB",
    }


def test_catalogue_identity_consistency_rejects_mismatch() -> None:
    invalid = confirmed_s24_ultra(brand="Apple", model="iPhone 13 Pro")
    ok, error, canonical = canonicalize_confirmed_device(invalid)
    assert_equal(ok, False, "mismatched identity should fail")
    assert_equal(error, "confirmed_device_identity_mismatch", "mismatch error")
    assert_equal(canonical, None, "mismatch should not produce canonical device")


def test_canonical_identity_source_uses_catalogue_entry() -> None:
    requested = {"catalogue_id": confirmed_s24_ultra()["catalogue_id"], "storage": "256GB"}
    captured = {}

    class CaptureProvider:
        def retrieve(self, confirmed_device: dict, condition_group: str) -> dict:
            captured.update(confirmed_device)
            return {
                "market_observations": [evidence_row("https://example.invalid/listing/1")],
                "grounded_sources": [{"title": "Listing 1", "url": "https://example.invalid/listing/1"}],
            }

    result = retrieve_current_market_evidence(requested, "A", provider=CaptureProvider(), use_cache=False)
    assert_equal(result["query_device"]["brand"], "Samsung", "query brand should come from catalogue")
    assert_equal(result["query_device"]["model"], "Galaxy S24 Ultra", "query model should come from catalogue")
    assert_equal(captured["brand"], "Samsung", "provider brand should come from catalogue")
    assert_equal(captured["model"], "Galaxy S24 Ultra", "provider model should come from catalogue")


def test_live_valuation_rejects_fabricated_observation_injection() -> None:
    server = HTTPServer(("127.0.0.1", 0), ReValueHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        fake_rows = [
            {
                "source": "Fake",
                "source_url": f"https://fake.invalid/{idx}",
                "country": "MY",
                "currency": "MYR",
                "brand": "Samsung",
                "model": "Galaxy S24 Ultra",
                "storage_gb": 256,
                "price": 10000,
                "price_type": "asking_price",
                "usable_for_market_range": True,
            }
            for idx in range(5)
        ]
        payload = json.dumps(
            {
                "confirmed_device": confirmed_s24_ultra(),
                "exterior_grade": "A",
                "market_observations": fake_rows,
                "market_evidence": {"status": "ok", "market_observations": fake_rows},
                "brand": "Samsung",
                "model": "Galaxy S24 Ultra",
                "storage_gb": 256,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/live-valuation",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        body = json.loads(urllib.request.urlopen(request, timeout=10).read().decode("utf-8"))
        assert body.get("value_low") != 10000
        assert body.get("value_high") != 10000
        assert body["status"] in {
            "estimated_market_value",
            "prototype_market_estimate",
            "blocked",
            "temporarily_unavailable",
            "insufficient_market_evidence",
            "error",
        }
        if body["status"] in {"estimated_market_value", "prototype_market_estimate"}:
            assert body.get("fallback_source") in {
                "local_s24_ultra_public_market_seed_v0.1",
                "local_malaysia_calibration_v0.1",
            }
    finally:
        server.shutdown()


def test_live_valuation_requires_confirmed_device() -> None:
    server = HTTPServer(("127.0.0.1", 0), ReValueHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload = json.dumps({"exterior_grade": "A"}).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/live-valuation",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        body = json.loads(urllib.request.urlopen(request, timeout=10).read().decode("utf-8"))
        assert_equal(body["status"], "error", "missing confirmed device should be rejected")
        assert_equal(body["error"], "confirmed_device_required", "missing confirmed device error")
    finally:
        server.shutdown()


def test_grounding_verified_matching_url() -> None:
    rows, excluded = validate_market_observations(
        [evidence_row("https://example.invalid/listing/1/?utm_source=test")],
        confirmed_device=confirmed_s24_ultra(),
        condition_group="clean",
        retrieved_at="2026-09-07T00:00:00+00:00",
        grounded_sources=grounded_sources_from_payload(
            {"grounded_sources": [{"title": "Grounded Listing", "url": "https://example.invalid/listing/1"}]}
        ),
    )
    assert_equal(len(rows), 1, "matching grounded URL should be accepted")
    assert_equal(excluded, [], "matching grounded URL should not be excluded")
    assert_equal(rows[0].grounding_verified, True, "grounding flag")
    assert_equal(rows[0].grounded_source_title, "Grounded Listing", "grounded title")


def test_model_url_missing_from_grounding_is_not_usable() -> None:
    rows, excluded = validate_market_observations(
        [evidence_row("https://example.invalid/listing/not-grounded")],
        confirmed_device=confirmed_s24_ultra(),
        condition_group="clean",
        retrieved_at="2026-09-07T00:00:00+00:00",
        grounded_sources=grounded_sources_from_payload(
            {"grounded_sources": [{"title": "Different Listing", "url": "https://example.invalid/listing/grounded"}]}
        ),
    )
    assert_equal(rows, [], "ungrounded model URL should not be accepted")
    assert_equal(excluded[0].exclusion_reason, "unverified_grounding_source", "ungrounded exclusion reason")
    assert_equal(excluded[0].usable_for_market_range, False, "ungrounded row not usable")


def test_same_domain_invented_path_is_not_grounding_verified() -> None:
    rows, excluded = validate_market_observations(
        [evidence_row("https://example.invalid/invented/path")],
        confirmed_device=confirmed_s24_ultra(),
        condition_group="clean",
        retrieved_at="2026-09-07T00:00:00+00:00",
        grounded_sources=grounded_sources_from_payload(
            {"grounded_sources": [{"title": "Home", "url": "https://example.invalid/real/path"}]}
        ),
    )
    assert_equal(rows, [], "same domain alone must not verify grounding")
    assert_equal(excluded[0].grounding_verified, False, "same domain invented path not verified")


def test_trade_in_and_repair_require_grounding() -> None:
    trade = evidence_row("https://example.invalid/trade", "trade_in_offer")
    repair = evidence_row("https://example.invalid/repair", "repair_cost")
    ungrounded_repair = evidence_row("https://example.invalid/repair/invented", "repair_cost")
    rows, excluded = validate_market_observations(
        [trade, repair, ungrounded_repair],
        confirmed_device=confirmed_s24_ultra(),
        condition_group="clean",
        retrieved_at="2026-09-07T00:00:00+00:00",
        grounded_sources=grounded_sources_from_payload(
            {
                "grounded_sources": [
                    {"title": "Trade", "url": "https://example.invalid/trade"},
                    {"title": "Repair", "url": "https://example.invalid/repair"},
                ]
            }
        ),
    )
    assert_equal([row.price_type for row in rows], ["trade_in_offer", "repair_cost"], "grounded benchmark rows")
    assert_equal(all(row.grounding_verified for row in rows), True, "benchmarks must be grounded")
    assert_equal(excluded[0].exclusion_reason, "unverified_grounding_source", "ungrounded repair excluded")


def test_freshness_classification() -> None:
    reference = "2026-09-07T00:00:00+00:00"
    assert_equal(classify_freshness("2026-09-01", reference), "fresh", "recent date")
    assert_equal(classify_freshness("2026-01-01", reference), "stale", "old date")
    assert_equal(classify_freshness("", reference), "unknown", "missing date")
    assert_equal(classify_freshness("not a date", reference), "unknown", "unparseable date")


class Web:
    def __init__(self, uri: str, title: str) -> None:
        self.uri = uri
        self.title = title


class Chunk:
    def __init__(self, web: Web) -> None:
        self.web = web


class Metadata:
    def __init__(self, chunks: list[Chunk]) -> None:
        self.grounding_chunks = chunks


class Candidate:
    def __init__(self, metadata: Metadata) -> None:
        self.grounding_metadata = metadata


class GeminiResponse:
    def __init__(self, candidates: list[Candidate]) -> None:
        self.candidates = candidates


def test_gemini_grounding_metadata_extraction() -> None:
    response = GeminiResponse([Candidate(Metadata([Chunk(Web("https://example.invalid/source", "Source Title"))]))])
    sources = extract_grounded_sources(response)
    assert_equal(sources[0]["url"], "https://example.invalid/source", "grounded source URL")
    assert_equal(sources[0]["title"], "Source Title", "grounded source title")


def main() -> int:
    test_catalogue_identity_consistency_rejects_mismatch()
    test_canonical_identity_source_uses_catalogue_entry()
    test_live_valuation_rejects_fabricated_observation_injection()
    test_live_valuation_requires_confirmed_device()
    test_grounding_verified_matching_url()
    test_model_url_missing_from_grounding_is_not_usable()
    test_same_domain_invented_path_is_not_grounding_verified()
    test_trade_in_and_repair_require_grounding()
    test_freshness_classification()
    test_gemini_grounding_metadata_extraction()
    print("phase6c1 market integrity tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
