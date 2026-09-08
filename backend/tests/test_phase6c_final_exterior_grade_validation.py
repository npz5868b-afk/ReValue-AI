from __future__ import annotations

import json
import sys
import threading
import urllib.request
from http.server import HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from backend.src.market_evidence import clear_market_cache, condition_group_from_exterior_grade, retrieve_current_market_evidence
from backend.src.phone_catalogue import match_catalogue_candidate
from backend.src.server import ReValueHandler


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


class CountingProvider:
    def __init__(self) -> None:
        self.calls = 0

    def retrieve(self, confirmed_device: dict, condition_group: str) -> dict:
        self.calls += 1
        return {"market_observations": [], "grounded_sources": []}


def post_json(port: int, path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return json.loads(urllib.request.urlopen(request, timeout=10).read().decode("utf-8"))


def test_condition_mapping_valid_grades() -> None:
    assert_equal(condition_group_from_exterior_grade("A"), "clean", "A condition")
    assert_equal(condition_group_from_exterior_grade("B"), "normal_used", "B condition")
    assert_equal(condition_group_from_exterior_grade("C"), "damaged_working", "C condition")
    assert_equal(condition_group_from_exterior_grade("C-"), "severe_cosmetic_damage", "C- condition")


def test_retrieve_rejects_missing_or_invalid_grade_without_provider_call() -> None:
    clear_market_cache()
    provider = CountingProvider()
    missing = retrieve_current_market_evidence(confirmed_s24_ultra(), None, provider=provider)
    invalid = retrieve_current_market_evidence(confirmed_s24_ultra(), "D", provider=provider)
    assert_equal(missing["status"], "error", "missing grade status")
    assert_equal(missing["error"], "valid_exterior_grade_required", "missing grade error")
    assert_equal(invalid["status"], "error", "invalid grade status")
    assert_equal(invalid["error"], "valid_exterior_grade_required", "invalid grade error")
    assert_equal(provider.calls, 0, "invalid grade must not call provider")


def test_retrieve_accepts_each_valid_grade() -> None:
    expectations = {
        "A": "clean",
        "B": "normal_used",
        "C": "damaged_working",
        "C-": "severe_cosmetic_damage",
    }
    for grade, expected_condition in expectations.items():
        provider = CountingProvider()
        result = retrieve_current_market_evidence(confirmed_s24_ultra(), grade, provider=provider, use_cache=False)
        assert_equal(result["condition_group"], expected_condition, f"{grade} condition group")
        assert_equal(provider.calls, 1, f"{grade} valid grade provider call")


def test_endpoints_reject_missing_or_invalid_grade() -> None:
    server = HTTPServer(("127.0.0.1", 0), ReValueHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        confirmed = confirmed_s24_ultra()
        cases = [
            ("/market-evidence/retrieve", {"confirmed_device": confirmed}),
            ("/market-evidence/retrieve", {"confirmed_device": confirmed, "exterior_grade": "D"}),
            ("/live-valuation", {"confirmed_device": confirmed}),
            ("/live-valuation", {"confirmed_device": confirmed, "exterior_grade": "D"}),
        ]
        for path, payload in cases:
            body = post_json(port, path, payload)
            assert_equal(body["status"], "error", f"{path} invalid grade status")
            assert_equal(body["error"], "valid_exterior_grade_required", f"{path} invalid grade error")
    finally:
        server.shutdown()


def main() -> int:
    test_condition_mapping_valid_grades()
    test_retrieve_rejects_missing_or_invalid_grade_without_provider_call()
    test_retrieve_accepts_each_valid_grade()
    test_endpoints_reject_missing_or_invalid_grade()
    print("phase6c final exterior grade validation tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
