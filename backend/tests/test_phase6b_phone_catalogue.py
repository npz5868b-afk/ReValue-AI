from __future__ import annotations

import json
import sys
import threading
import urllib.request
from http.server import HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from backend.src.device_identification import identify_device
from backend.src.phone_catalogue import (
    confirm_device,
    load_catalogue,
    match_catalogue_candidate,
    search_catalogue,
)
from backend.src.schemas import ImageInput
from backend.src.server import ReValueHandler


def assert_equal(actual, expected, message: str) -> None:
    if actual != expected:
        raise AssertionError(f"{message}: expected {expected!r}, got {actual!r}")


def exact_match(brand: str, model: str) -> dict:
    result = match_catalogue_candidate(brand, model)
    assert_equal(result["status"], "exact", f"{brand} {model} should exact match")
    return result["catalogue_match"]


def test_catalogue_load() -> None:
    catalogue = load_catalogue()
    assert len(catalogue) == 3751
    assert any(item.brand == "Samsung" and item.model == "Galaxy S24 Ultra" for item in catalogue)


def test_known_device_exact_matches() -> None:
    s24u = exact_match("Samsung", "Galaxy S24 Ultra")
    assert_equal(s24u["model"], "Galaxy S24 Ultra", "S24 Ultra canonical model")
    assert_equal(s24u["storage_options"], ["256GB", "512GB", "1TB"], "S24 Ultra observed storage")
    assert_equal(s24u["storage_options_complete"], False, "storage options are observed, not exhaustive")
    assert_equal(s24u["requires_user_confirmation"], True, "model confirmation required")

    s23u = exact_match("Samsung", "Galaxy S23 Ultra")
    assert_equal(s23u["storage_options"], ["256GB", "512GB", "1TB"], "S23 Ultra observed storage")

    iphone = exact_match("Apple", "iPhone 13 Pro")
    assert_equal(iphone["model"], "iPhone 13 Pro", "iPhone 13 Pro canonical model")
    assert_equal(iphone["storage_options"], ["128GB", "256GB"], "iPhone 13 Pro observed storage")


def test_variant_safety() -> None:
    s24 = exact_match("Samsung", "Galaxy S24")
    assert_equal(s24["model"], "Galaxy S24", "Galaxy S24 must not become Ultra")

    s24_plus = exact_match("Samsung", "Galaxy S24+")
    assert_equal(s24_plus["model"], "Galaxy S24 Plus", "Galaxy S24+ should normalize to Plus row")

    iphone_pro = exact_match("Apple", "iPhone 13 Pro")
    iphone_pro_max = exact_match("Apple", "iPhone 13 Pro Max")
    assert iphone_pro["catalogue_id"] != iphone_pro_max["catalogue_id"]
    assert_equal(iphone_pro["model"], "iPhone 13 Pro", "iPhone 13 Pro must not become Pro Max")


def test_unknown_device_not_fabricated() -> None:
    result = match_catalogue_candidate("MadeUp", "Imaginary Phone Ultra 9000")
    assert result["status"] in {"not_found", "ambiguous"}
    assert "catalogue_match" not in result


def test_storage_confirmation_policy() -> None:
    s24u = exact_match("Samsung", "Galaxy S24 Ultra")
    missing = confirm_device(s24u["catalogue_id"], None)
    assert_equal(missing["status"], "error", "storage must be explicitly confirmed")
    assert_equal(missing["error"], "storage_confirmation_required", "missing storage error")

    manual = confirm_device(s24u["catalogue_id"], "2TB")
    assert_equal(manual["status"], "ok", "manual storage entry should be allowed")
    assert_equal(manual["confirmed_device"]["storage"], "2TB", "manual storage preserved")
    assert_equal(manual["confirmed_device"]["storage_in_catalogue_options"], False, "manual option not in observed catalogue")
    assert_equal(manual["confirmed_device"]["allows_manual_storage_entry"], True, "manual entry allowed")


class FakeGeminiClient:
    model = "gemini-3.7-flash"

    def generate_structured(self, **kwargs):
        return {
            "brand": "Samsung",
            "top_candidates": [
                {
                    "brand": "Samsung",
                    "model": "Galaxy S24 Ultra",
                    "confidence_label": "likely",
                    "visual_evidence": ["rear camera layout"],
                },
                {
                    "brand": "Samsung",
                    "model": "Galaxy S23 Ultra",
                    "confidence_label": "possible",
                    "visual_evidence": ["similar Ultra camera arrangement"],
                },
                {
                    "brand": "Samsung",
                    "model": "Galaxy S24",
                    "confidence_label": "possible",
                    "visual_evidence": ["flat frame"],
                },
            ],
        }


def test_gemini_top3_catalogue_normalization() -> None:
    result = identify_device(
        [ImageInput(view="back", filename="back.jpg", mime_type="image/jpeg", bytes_b64=None)],
        client=FakeGeminiClient(),
    )
    assert_equal(result.status, "ok", "fake Gemini response should parse")
    assert_equal(result.requires_user_confirmation, True, "Gemini must not finalize identity")
    assert_equal(result.candidates[0]["catalogue_match"]["status"], "exact", "Top-1 should exact catalogue match")
    assert_equal(
        result.candidates[0]["catalogue_match"]["catalogue_match"]["model"],
        "Galaxy S24 Ultra",
        "Top-1 canonical model",
    )


def test_catalogue_endpoints() -> None:
    server = HTTPServer(("127.0.0.1", 0), ReValueHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        search_data = json.loads(
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/catalogue/search?q=Samsung%20Galaxy%20S24%20Ultra",
                timeout=10,
            ).read().decode("utf-8")
        )
        assert search_data["results"]
        catalogue_id = search_data["results"][0]["catalogue_id"]
        payload = json.dumps({"catalogue_id": catalogue_id, "storage": "512GB"}).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/confirm-device",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        confirmed = json.loads(urllib.request.urlopen(request, timeout=10).read().decode("utf-8"))
        assert_equal(confirmed["status"], "ok", "confirm-device endpoint")
        assert_equal(confirmed["confirmed_device"]["storage"], "512GB", "confirmed storage")
    finally:
        server.shutdown()


def main() -> int:
    test_catalogue_load()
    test_known_device_exact_matches()
    test_variant_safety()
    test_unknown_device_not_fabricated()
    test_storage_confirmation_policy()
    test_gemini_top3_catalogue_normalization()
    test_catalogue_endpoints()
    print("phase6b phone catalogue tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
