from __future__ import annotations

import csv
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.market_evidence import clear_market_cache, parse_storage_gb, retrieve_current_market_evidence
from backend.src.phone_catalogue import match_catalogue_candidate
from backend.src.valuation import value_from_market_observations


EVIDENCE_FIELDS = [
    "test_device",
    "source",
    "source_url",
    "grounded_source_title",
    "grounded_source_url",
    "grounding_verified",
    "retrieved_at",
    "observed_at",
    "freshness_status",
    "country",
    "currency",
    "listing_title",
    "brand",
    "model",
    "storage_gb",
    "condition_text",
    "condition_group",
    "price",
    "price_type",
    "exact_model_match",
    "exact_storage_match",
    "usable_for_market_range",
    "exclusion_reason",
]

SUMMARY_FIELDS = [
    "test_device",
    "status",
    "retrieved_at",
    "total_retrieved",
    "accepted",
    "excluded",
    "duplicate_count",
    "usable_exact_model_storage_count",
    "condition_matched_count",
    "minimum",
    "q1",
    "median",
    "q3",
    "maximum",
    "value_low",
    "value_high",
    "midpoint",
    "evidence_status",
    "distribution_prices",
    "distribution_source_urls",
    "trade_in_count",
    "repair_reference_count",
    "notes",
]


TEST_CASES = [
    {
        "slug": "s24_ultra",
        "json_name": "live_market_test_s24_ultra_256gb.json",
        "test_device": "Samsung Galaxy S24 Ultra 256GB Grade A",
        "brand": "Samsung",
        "model": "Galaxy S24 Ultra",
        "storage": "256GB",
        "exterior_grade": "A",
    },
    {
        "slug": "iphone13pro",
        "json_name": "live_market_test_iphone13pro_256gb.json",
        "test_device": "Apple iPhone 13 Pro 256GB Grade C-",
        "brand": "Apple",
        "model": "iPhone 13 Pro",
        "storage": "256GB",
        "exterior_grade": "C-",
    },
]


def _catalogue_confirmed_device(brand: str, model: str, storage: str) -> dict[str, Any]:
    match = match_catalogue_candidate(brand, model)
    catalogue_match = match.get("catalogue_match") or {}
    if not catalogue_match.get("catalogue_id"):
        raise RuntimeError(f"Catalogue match not found for {brand} {model}")
    return {
        "catalogue_id": catalogue_match["catalogue_id"],
        "brand": catalogue_match["brand"],
        "model": catalogue_match["model"],
        "storage": storage,
    }


def _row(test_device: str, item: dict[str, Any]) -> dict[str, Any]:
    return {field: item.get(field, "") for field in EVIDENCE_FIELDS} | {"test_device": test_device}


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _valuation_for_retrieval(retrieval: dict[str, Any]) -> dict[str, Any] | None:
    if retrieval.get("status") != "ok" or not retrieval.get("market_observations"):
        return None
    query_device = retrieval.get("query_device") or {}
    valuation = value_from_market_observations(
        observations=retrieval.get("market_observations", []),
        brand=query_device.get("brand", ""),
        model=query_device.get("model", ""),
        storage_gb=parse_storage_gb(query_device.get("storage")),
        condition_group=retrieval.get("condition_group"),
        trade_in_benchmark=retrieval.get("trade_in_benchmarks", []),
        repair_references=retrieval.get("repair_references", []),
    )
    return asdict(valuation)


def _summary_row(test_device: str, retrieval: dict[str, Any], valuation: dict[str, Any] | None) -> dict[str, Any]:
    accepted_rows = (
        list(retrieval.get("market_observations") or [])
        + list(retrieval.get("trade_in_benchmarks") or [])
        + list(retrieval.get("repair_references") or [])
        + list(retrieval.get("international_references") or [])
    )
    excluded_rows = list(retrieval.get("excluded_observations") or [])
    duplicate_count = sum(1 for row in excluded_rows if row.get("exclusion_reason") == "duplicate_listing")
    distribution = (valuation or {}).get("distribution") or {}
    distribution_rows = distribution.get("observations") or []
    prices = [str(row.get("price", "")) for row in distribution_rows]
    urls = [str(row.get("source_url", "")) for row in distribution_rows]
    return {
        "test_device": test_device,
        "status": (valuation or {}).get("status") or retrieval.get("status"),
        "retrieved_at": retrieval.get("retrieved_at", ""),
        "total_retrieved": len(accepted_rows) + len(excluded_rows),
        "accepted": len(accepted_rows),
        "excluded": len(excluded_rows),
        "duplicate_count": duplicate_count,
        "usable_exact_model_storage_count": (valuation or {}).get("exact_model_count", len(retrieval.get("market_observations") or [])),
        "condition_matched_count": (valuation or {}).get("condition_matched_count", ""),
        "minimum": distribution.get("min", ""),
        "q1": distribution.get("q1", ""),
        "median": distribution.get("median", ""),
        "q3": distribution.get("q3", ""),
        "maximum": distribution.get("max", ""),
        "value_low": (valuation or {}).get("value_low", ""),
        "value_high": (valuation or {}).get("value_high", ""),
        "midpoint": (valuation or {}).get("midpoint", ""),
        "evidence_status": (valuation or {}).get("evidence_status", retrieval.get("status", "")),
        "distribution_prices": " | ".join(prices),
        "distribution_source_urls": " | ".join(urls),
        "trade_in_count": len(retrieval.get("trade_in_benchmarks") or []),
        "repair_reference_count": len(retrieval.get("repair_references") or []),
        "notes": " | ".join(str(note) for note in retrieval.get("notes", [])),
    }


def run(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    clear_market_cache()
    api_key_visible = bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
    all_evidence_rows: list[dict[str, Any]] = []
    all_excluded_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    json_results: dict[str, Any] = {}

    for case in TEST_CASES:
        confirmed_device = _catalogue_confirmed_device(case["brand"], case["model"], case["storage"])
        retrieval = retrieve_current_market_evidence(
            confirmed_device=confirmed_device,
            exterior_grade=case["exterior_grade"],
            use_cache=False,
        )
        valuation = _valuation_for_retrieval(retrieval)
        accepted = (
            list(retrieval.get("market_observations") or [])
            + list(retrieval.get("trade_in_benchmarks") or [])
            + list(retrieval.get("repair_references") or [])
            + list(retrieval.get("international_references") or [])
        )
        excluded = list(retrieval.get("excluded_observations") or [])
        all_evidence_rows.extend(_row(case["test_device"], item) for item in accepted)
        all_excluded_rows.extend(_row(case["test_device"], item) for item in excluded)
        summary_rows.append(_summary_row(case["test_device"], retrieval, valuation))
        payload = {
            "test_device": case["test_device"],
            "api_key_visible": api_key_visible,
            "confirmed_device": confirmed_device,
            "exterior_grade": case["exterior_grade"],
            "retrieval": retrieval,
            "valuation": valuation,
            "distribution_rows_entering_q1_median_q3": ((valuation or {}).get("distribution") or {}).get("observations", []),
        }
        json_results[case["slug"]] = payload
        _write_json(output_dir / case["json_name"], payload)

    _write_csv(output_dir / "live_market_evidence_rows_v0.1.csv", EVIDENCE_FIELDS, all_evidence_rows)
    _write_csv(output_dir / "live_market_excluded_rows_v0.1.csv", EVIDENCE_FIELDS, all_excluded_rows)
    _write_csv(output_dir / "live_market_valuation_summary_v0.1.csv", SUMMARY_FIELDS, summary_rows)
    return {
        "api_key_visible": api_key_visible,
        "output_dir": str(output_dir),
        "tests": list(json_results.keys()),
        "summary": summary_rows,
    }


def main() -> int:
    output_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "validation"
    result = run(output_dir)
    print(json.dumps({k: v for k, v in result.items() if k != "summary"}, indent=2))
    for row in result["summary"]:
        print(f"{row['test_device']}: {row['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
