from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from backend.src.decision_engine import recommend_next_action
from backend.src.server import _decision_payload_with_verified_trade_in_benchmarks


def _valuation(benchmarks: list[dict[str, object]]) -> dict[str, object]:
    return {
        "status": "estimated_market_value",
        "currency": "MYR",
        "value_low": 2000,
        "value_high": 2400,
        "midpoint": 2200,
        "trade_in_benchmark": benchmarks,
    }


def _payload(benchmarks: list[dict[str, object]]) -> dict[str, object]:
    return {
        "confirmed_device": {"brand": "Samsung", "model": "Galaxy S24 Ultra", "storage": "256GB"},
        "exterior_grade": "A",
        "valuation": _valuation(benchmarks),
        "user_priority": "convenience",
    }


def test_decision_boundary_rejects_unverified_indicative_and_wrong_currency_benchmarks() -> None:
    rejected = [
        {"price": 2300, "currency": "MYR", "grounding_verified": True, "guarantee_status": "guaranteed"},
        {"price": 2300, "currency": "MYR", "verification": "verified", "grounding_verified": True, "guarantee_status": "Indicative maximum / up-to value"},
        {"price": 2300, "currency": "USD", "verification": "verified", "grounding_verified": True, "guarantee_status": "guaranteed"},
        {"price": 2300, "currency": "MYR", "verification": "verified", "grounding_verified": False, "guarantee_status": "guaranteed"},
    ]
    for benchmark in rejected:
        original = _payload([benchmark])
        sanitized = _decision_payload_with_verified_trade_in_benchmarks(original)
        assert sanitized["valuation"]["trade_in_benchmark"] == []
        assert original == _payload([benchmark])
        result = recommend_next_action(sanitized)
        assert result["recommended_action"] != "trade_in"
        assert "No verified trade-in benchmark is currently available for comparison." in str(result)


def test_public_decision_rejects_self_declared_verified_firm_quote() -> None:
    # Even a plausible merchant name / URL / quote ID is untrusted if supplied
    # by the browser. No server-attested quote provider is integrated yet.
    offer = {
        "price": 2300,
        "currency": "MYR",
        "merchant": "Example Mobile Shop",
        "source_url": "https://example.org/quote/42",
        "quote_id": "quote-42",
        "offer_verification": "confirmed",
        "grounding_verified": True,
        "guarantee_status": "firm",
    }
    payload = _payload([offer])
    before = copy.deepcopy(payload)
    sanitized = _decision_payload_with_verified_trade_in_benchmarks(payload)
    assert payload == before, "input valuation evidence must not be mutated"
    assert sanitized["valuation"]["trade_in_benchmark"] == []
    result = recommend_next_action(sanitized)
    assert result["recommended_action"] != "trade_in"
    assert "No verified trade-in benchmark is currently available for comparison." in str(result)


def test_public_decision_rejects_aliased_trade_in_quotes() -> None:
    payload = _payload([])
    payload["valuation"]["trade_in_benchmarks"] = [2300, {"price": 2600, "verification": "verified"}]
    payload["valuation"]["trade_in_benchmark"] = [2500]
    sanitized = _decision_payload_with_verified_trade_in_benchmarks(payload)
    assert sanitized["valuation"]["trade_in_benchmark"] == []
    assert "trade_in_benchmarks" not in sanitized["valuation"]
    assert "trade_in_benchmarks" in payload["valuation"]
    assert recommend_next_action(sanitized)["recommended_action"] != "trade_in"


def main() -> int:
    test_decision_boundary_rejects_unverified_indicative_and_wrong_currency_benchmarks()
    test_public_decision_rejects_self_declared_verified_firm_quote()
    test_public_decision_rejects_aliased_trade_in_quotes()
    print("integration hardening backend tests passed: 3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
