from __future__ import annotations

import json
import sys
import threading
import urllib.request
from http.server import HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from backend.src import device_identification
from backend.src.gemini_client import GeminiTemporaryUnavailable, is_retryable_gemini_error
from backend.src.phone_catalogue import CatalogueEntry, load_catalogue, match_catalogue_candidate
from backend.src.schemas import ImageInput
from backend.src.server import ReValueHandler


def assert_equal(actual, expected, message: str) -> None:
    if actual != expected:
        raise AssertionError(f"{message}: expected {expected!r}, got {actual!r}")


def test_s22_ultra_connectivity_suffix_match() -> None:
    result = match_catalogue_candidate("Samsung", "Galaxy S22 Ultra")
    assert_equal(result["status"], "strong", "S22 Ultra without 5G should be a strong compatible match")
    assert_equal(result["match_reason"], "connectivity_suffix_compatible", "S22 match reason")
    match = result["catalogue_match"]
    assert_equal(match["model"], "Galaxy S22 Ultra 5G", "S22 Ultra canonical compatible model")
    assert "S23" not in match["model"]
    assert "S24" not in match["model"]
    assert "S25" not in match["model"]


def test_s22_ultra_5g_exact_match() -> None:
    result = match_catalogue_candidate("Samsung", "Galaxy S22 Ultra 5G")
    assert_equal(result["status"], "exact", "S22 Ultra 5G should exact match")
    assert_equal(result["catalogue_match"]["model"], "Galaxy S22 Ultra 5G", "S22 Ultra 5G canonical model")


def test_memory_configuration_suffix_merges_into_canonical_family() -> None:
    result = match_catalogue_candidate("Samsung", "Galaxy S22 Ultra 5G")
    match = result["catalogue_match"]
    assert_equal(match["source_row_count"], 2, "S22 Ultra 5G source rows should merge")
    assert_equal(match["storage_options"], ["128GB", "256GB"], "S22 Ultra 5G observed storage")
    assert_equal(match["ram"], ["8GB", "12GB"], "S22 Ultra 5G RAM evidence")
    assert not any(item.model == "Galaxy S22 Ultra 5G (12GB/256GB)" for item in load_catalogue())


def test_4g_5g_ambiguity_safety() -> None:
    entries = [
        CatalogueEntry(
            catalogue_id="phone_example_4g",
            brand="Example",
            model="Example Phone 4G",
            normalized_brand="example",
            normalized_model="example phone 4g",
            storage_options=["128GB"],
            storage_options_source="catalogue_observed",
            storage_options_complete=False,
            requires_user_confirmation=True,
            allows_manual_storage_entry=True,
            ram=["8GB"],
            source_row_count=1,
        ),
        CatalogueEntry(
            catalogue_id="phone_example_5g",
            brand="Example",
            model="Example Phone 5G",
            normalized_brand="example",
            normalized_model="example phone 5g",
            storage_options=["128GB"],
            storage_options_source="catalogue_observed",
            storage_options_complete=False,
            requires_user_confirmation=True,
            allows_manual_storage_entry=True,
            ram=["8GB"],
            source_row_count=1,
        ),
    ]
    result = match_catalogue_candidate("Example", "Example Phone", entries=entries)
    assert_equal(result["status"], "ambiguous", "missing connectivity must not silently choose 4G or 5G")
    assert "catalogue_match" not in result


def test_variant_safety_still_holds() -> None:
    s24 = match_catalogue_candidate("Samsung", "Galaxy S24")
    s24u = match_catalogue_candidate("Samsung", "Galaxy S24 Ultra")
    iphone_pro = match_catalogue_candidate("Apple", "iPhone 13 Pro")
    iphone_pro_max = match_catalogue_candidate("Apple", "iPhone 13 Pro Max")
    assert_equal(s24["catalogue_match"]["model"], "Galaxy S24", "S24 must not become Ultra")
    assert_equal(s24u["catalogue_match"]["model"], "Galaxy S24 Ultra", "S24 Ultra exact match")
    assert iphone_pro["catalogue_match"]["catalogue_id"] != iphone_pro_max["catalogue_match"]["catalogue_id"]


class FakeGemini503Client:
    model = "gemini-3.7-flash"

    def generate_structured(self, **kwargs):
        raise GeminiTemporaryUnavailable("AI identification is temporarily unavailable. Please try again.")


def test_gemini_503_direct_response_is_structured() -> None:
    result = device_identification.identify_device(
        [ImageInput(view="back", filename="back.jpg", mime_type="image/jpeg", bytes_b64=None)],
        client=FakeGemini503Client(),
    )
    assert_equal(result.status, "temporarily_unavailable", "retryable Gemini failure status")
    assert_equal(result.retryable, True, "retryable flag")
    assert_equal(result.stage, "device_identification", "failure stage")
    assert_equal(result.candidates, [], "no fabricated catalogue candidates")
    assert_equal(result.top_candidates, [], "no fabricated Gemini candidates")


def test_gemini_503_http_connection_returns_json(monkeypatch=None) -> None:
    original_client = device_identification.GeminiClient

    class PatchedClient(FakeGemini503Client):
        def __init__(self, *args, **kwargs):
            pass

    device_identification.GeminiClient = PatchedClient
    server = HTTPServer(("127.0.0.1", 0), ReValueHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload = json.dumps({"images": [{"view": "back", "filename": "back.jpg", "mime_type": "image/jpeg"}]}).encode(
            "utf-8"
        )
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/identify-device",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        response = urllib.request.urlopen(request, timeout=10)
        body = json.loads(response.read().decode("utf-8"))
        assert_equal(response.status, 200, "HTTP connection should remain valid")
        assert_equal(body["status"], "temporarily_unavailable", "HTTP retryable status")
        assert_equal(body["retryable"], True, "HTTP retryable flag")
        assert_equal(body["stage"], "device_identification", "HTTP failure stage")
        assert_equal(body["candidates"], [], "HTTP response must not fabricate candidates")
    finally:
        server.shutdown()
        device_identification.GeminiClient = original_client


def test_retryable_error_classifier() -> None:
    class Http503Error(Exception):
        status_code = 503

    assert_equal(is_retryable_gemini_error(Http503Error("UNAVAILABLE")), True, "503 should be retryable")
    assert_equal(is_retryable_gemini_error(TimeoutError("timed out")), True, "timeout should be retryable")
    assert_equal(is_retryable_gemini_error(RuntimeError("bad request")), False, "non-retryable error")


def main() -> int:
    test_s22_ultra_connectivity_suffix_match()
    test_s22_ultra_5g_exact_match()
    test_memory_configuration_suffix_merges_into_canonical_family()
    test_4g_5g_ambiguity_safety()
    test_variant_safety_still_holds()
    test_gemini_503_direct_response_is_structured()
    test_gemini_503_http_connection_returns_json()
    test_retryable_error_classifier()
    print("phase6b1 catalogue normalization tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
