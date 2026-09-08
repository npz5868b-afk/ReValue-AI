from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from .market_evidence import condition_group_from_exterior_grade, parse_storage_gb
from .phase3_valuation_engine import value_device as phase3_value_device
from .valuation import value_from_market_observations


LOCAL_CALIBRATION_PATH = Path(__file__).resolve().parents[1] / "data" / "malaysia_market_calibration_v0.1.json"


def _condition_from_grade(exterior_grade: str) -> dict[str, Any]:
    grade = (exterior_grade or "").strip().upper()
    if grade == "A":
        return {"exterior_grade": "A", "condition_group": "clean_a_like"}
    if grade == "B":
        return {"exterior_grade": "B", "condition_group": "normal_used_b_like", "screen_scratched": "yes"}
    if grade == "C":
        return {"exterior_grade": "C", "condition_group": "damaged_c_like", "screen_cracked": "yes"}
    if grade == "C-":
        return {"exterior_grade": "C-", "condition_group": "damaged_c_like", "back_glass_cracked": "yes"}
    return {"exterior_grade": grade}


def _normalize_model_key(brand: str, model: str, storage_gb: int | None) -> tuple[str, str, int | None]:
    return (brand.strip().lower(), model.strip().lower(), storage_gb)


def _phase3_result_to_live_payload(result: dict[str, Any], *, fallback_reason: str) -> dict[str, Any]:
    market_range = result.get("market_value_range_rm") or {}
    low = market_range.get("low")
    high = market_range.get("high")
    return {
        "status": "estimated_market_value",
        "currency": "MYR",
        "value_low": low,
        "value_high": high,
        "midpoint": result.get("market_value_midpoint_rm"),
        "comparable_count": result.get("comparable_count", 0),
        "exact_model_count": result.get("comparable_count", 0),
        "condition_matched_count": result.get("condition_matched_count", 0),
        "evidence_status": result.get("evidence_tier", "local_calibration_fallback"),
        "notes": [
            "Fallback valuation uses local ReValue Malaysia calibration evidence.",
            "No Gemini or LLM-generated pricing is required for this valuation.",
            f"Fallback reason: {fallback_reason}",
        ],
        "limitations": [
            "Live market retrieval is optional and may be refreshed later.",
            "Final store quote still requires physical inspection.",
        ],
        "trade_in_benchmark": [result["trade_in_benchmark_rm"]] if result.get("trade_in_benchmark_rm") else [],
        "repair_references": [result["repair_cost_range_rm"]] if result.get("repair_cost_range_rm") else [],
        "sources": [{"source_url": url} for url in result.get("evidence_sources", [])],
        "distribution": {
            "selected_observation_ids": result.get("selected_observation_ids", []),
            "range_method": result.get("range_method", ""),
            "main_value_drivers": result.get("main_value_drivers", []),
            "explanation": result.get("explanation", ""),
        },
        "fallback_source": "local_malaysia_calibration_v0.1",
    }


def _s24_ultra_local_observations() -> list[dict[str, Any]]:
    rows = [
        ("Carousell Malaysia", "https://www.carousell.com.my/mobile-phones-gadgets/mobile-phones/samsung-s24-ultra-256gb/q-5251/", "Samsung Galaxy S24 Ultra 256GB", "clean", 2299),
        ("Carousell Malaysia", "https://www.carousell.com.my/mobile-phones-gadgets/mobile-phones/samsung-s24-ultra-256gb/q-5251/", "Samsung S24 Ultra 12GB+256GB", "clean", 2200),
        ("Carousell Malaysia", "https://www.carousell.com.my/mobile-phones-gadgets/mobile-phones/samsung-s24-ultra-256gb/q-5251/", "SAMSUNG S24 ULTRA GREY 256GB", "normal_used", 2200),
        ("Carousell Malaysia", "https://www.carousell.com.my/mobile-phones-gadgets/mobile-phones/samsung-s24-ultra-256gb/q-5251/", "SECOND HAND PHONE ONLY SAMSUNG S24 ULTRA 5G 12+256GB", "clean", 2499),
        ("Carousell Malaysia", "https://www.carousell.com.my/mobile-phones-gadgets/mobile-phones/samsung-s24-ultra-256gb/q-5251/", "Samsung S24 Ultra 256GB Black Titanium", "clean", 2280),
        ("Carousell Malaysia", "https://www.carousell.com.my/mobile-phones-gadgets/mobile-phones/samsung-s24-ultra-256gb/q-5251/", "Samsung Galaxy S24 Ultra Titanium Grey 256GB", "clean", 1950),
        ("Carousell Malaysia", "https://www.carousell.com.my/p/samsung-s24-ultra-12-256gh-1454275404/", "SAMSUNG S24 ULTRA 12/256GH", "normal_used", 2150),
        ("Mudah.my", "https://www.mudah.my/malaysia/electronics-for-sale?q=samsung+galaxy+s24+ultra", "Samsung Galaxy S24 Ultra 256 GB", "normal_used", 2188),
        ("Mudah.my", "https://www.mudah.my/malaysia/electronics-for-sale?q=samsung+galaxy+s24+ultra", "Samsung Galaxy S24 Ultra 256 GB", "clean", 2700),
        ("Mudah.my", "https://www.mudah.my/malaysia/electronics-for-sale?q=samsung+galaxy+s24+ultra", "Samsung Galaxy S24 Ultra 256 GB", "normal_used", 2288),
        ("3Cat Malaysia", "https://www.3cat.my/used-samsung-galaxy-s24-ultra/", "Used Samsung Galaxy S24 Ultra 256GB", "clean", 2799),
    ]
    return [
        {
            "source": source,
            "source_url": url,
            "observed_at": "2026-09-08",
            "retrieved_at": "2026-09-08",
            "country": "MY",
            "currency": "MYR",
            "brand": "Samsung",
            "model": "Galaxy S24 Ultra",
            "storage_gb": 256,
            "condition": condition,
            "condition_text": condition,
            "condition_group": condition,
            "price": price,
            "price_type": "asking_price" if source != "3Cat Malaysia" else "used_retail_price",
            "source_type": "marketplace" if source != "3Cat Malaysia" else "used_retailer",
            "listing_title": title,
            "exact_model_match": True,
            "exact_storage_match": True,
            "usable_for_market_range": True,
            "grounding_verified": False,
            "freshness_status": "fresh",
        }
        for source, url, title, condition, price in rows
    ]


def local_fallback_valuation(
    *,
    confirmed_device: dict[str, Any],
    exterior_grade: str,
    fallback_reason: str = "live_market_retrieval_unavailable",
) -> dict[str, Any]:
    brand = str(confirmed_device.get("brand") or "")
    model = str(confirmed_device.get("model") or "")
    storage_gb = parse_storage_gb(confirmed_device.get("storage"))
    key = _normalize_model_key(brand, model, storage_gb)

    if key in {
        ("apple", "iphone 13 pro", 256),
        ("samsung", "galaxy s23 ultra", 256),
    }:
        device_condition = {
            "brand": brand,
            "model": model,
            "storage_gb": storage_gb,
            "internal_grade": "B+",
            **_condition_from_grade(exterior_grade),
        }
        result = phase3_value_device(device_condition, LOCAL_CALIBRATION_PATH)
        return _phase3_result_to_live_payload(result, fallback_reason=fallback_reason)

    if key == ("samsung", "galaxy s24 ultra", 256):
        condition_group = condition_group_from_exterior_grade(exterior_grade)
        result = value_from_market_observations(
            observations=_s24_ultra_local_observations(),
            brand="Samsung",
            model="Galaxy S24 Ultra",
            storage_gb=256,
            condition_group=condition_group,
            currency="MYR",
        )
        payload = asdict(result)
        payload["fallback_source"] = "local_s24_ultra_public_market_seed_v0.1"
        payload["notes"].insert(0, "Fallback valuation uses local public Malaysia evidence seed; Gemini is optional.")
        payload["notes"].append(f"Fallback reason: {fallback_reason}")
        return payload

    return {
        "status": "insufficient_market_evidence",
        "currency": "MYR",
        "value_low": None,
        "value_high": None,
        "midpoint": None,
        "comparable_count": 0,
        "exact_model_count": 0,
        "condition_matched_count": 0,
        "evidence_status": "unsupported_local_fallback_device",
        "notes": [
            "No local fallback comparable set is available for this device yet.",
            "Gemini/live retrieval remains optional but unavailable for this request.",
        ],
        "limitations": ["Confirm model/storage or add audited local evidence before deployment for this device."],
        "trade_in_benchmark": [],
        "repair_references": [],
        "sources": [],
        "fallback_source": "none_available",
    }
