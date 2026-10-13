"""Describe completed valuations without selecting evidence or calculating prices."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .local_valuation import _condition_from_grade
from .market_evidence import condition_group_from_exterior_grade, normalize_exterior_grade


EVIDENCE_LABELS = {
    "strong_local_evidence": "Strong local evidence",
    "moderate_local_evidence": "Moderate local evidence",
    "limited_local_evidence": "Limited local evidence",
    "insufficient_market_evidence": "Insufficient market evidence",
    "unsupported_local_fallback_device": "Insufficient market evidence",
    "exact_defect_comparable": "Exact-defect comparables (local calibration)",
    "limited_exact_defect_comparable": "Limited exact-defect comparables (local calibration)",
    "condition_group_comparable": "Condition-group comparables (local calibration)",
    "exact_device_fallback": "Exact-device fallback (local calibration)",
}


def build_valuation_explainability(
    valuation: dict[str, Any],
    *,
    confirmed_device: dict[str, Any],
    exterior_grade: str,
    condition_group: str | None = None,
) -> dict[str, Any]:
    """Consume existing output only; keep every original field and nested value intact."""
    grade = normalize_exterior_grade(exterior_grade)
    device = {key: confirmed_device.get(key, "") for key in ("brand", "model", "storage")}
    distribution = valuation.get("distribution") or {}
    fallback = valuation.get("fallback_source")
    calibration = fallback == "local_malaysia_calibration_v0.1"
    available = valuation.get("value_low") is not None and valuation.get("value_high") is not None
    condition = (
        _condition_from_grade(grade).get("condition_group", "unknown")
        if calibration else condition_group or condition_group_from_exterior_grade(grade)
    )
    origin = "local_calibration" if calibration else "local_public_seed" if fallback else "live_market_retrieval"
    if fallback == "none_available":
        origin = "no_local_evidence"
    evidence_status = valuation.get("evidence_status", "")
    strength = EVIDENCE_LABELS.get(evidence_status, "Evidence strength unavailable")
    exact = valuation.get("exact_model_count", 0)
    matched = valuation.get("condition_matched_count", 0)
    selected_ids = distribution.get("selected_observation_ids", [])
    selected_count = len(selected_ids) if calibration else valuation.get("comparable_count", 0)
    range_method = distribution.get("range_method", "") if calibration else "Q1–Q3 resale range, rounded to RM50"
    if not available:
        summary = "There is insufficient reliable Malaysian exact model/storage evidence to produce a market range for this device."
        range_method = "No market range produced"
    elif calibration:
        summary = "Local Malaysian calibration comparables are selected by device, exterior condition and applicable grade-derived defect flags. " + range_method + "; rounded to RM50."
    else:
        selection = (
            "condition-matched exact model/storage resale comparables"
            if evidence_status == "strong_local_evidence"
            else "exact model/storage resale comparables because condition-matched evidence is limited"
        )
        summary = "The range uses the 25th–75th percentiles of Malaysian " + selection + ", rounded to RM50."

    caveats = [
        "Final merchant offer may differ after physical inspection.",
        "The current internal diagnostic grade is not used to calculate this range.",
        "Exterior grade guides comparable matching; no separate per-damage RM deduction is calculated.",
        "Trade-in benchmarks and repair references are separate context, not included in the resale range.",
    ]
    if fallback == "none_available":
        caveats.append("No supported local evidence set is available for this device and storage.")
    elif fallback:
        caveats.append("Local evidence was used; listings were not refreshed through live market retrieval for this result.")
    observations = distribution.get("observations") or []
    if any(row.get("freshness_status") == "stale" for row in observations):
        caveats.append("Some selected market evidence has stale observable dates.")
    if any(row.get("freshness_status", "unknown") == "unknown" for row in observations):
        caveats.append("Some selected market evidence has unknown observable listing dates.")
    if available:
        caveats.append("Listing and retail prices are evidence of advertised prices, not a guaranteed sale or shop offer.")
    else:
        caveats.append(summary)

    return {
        "version": "why_this_value_v1",
        "device": device,
        "valuation": {key: valuation.get(key) for key in ("currency", "value_low", "value_high", "midpoint", "status")},
        "factors": [
            {"key": "device_configuration", "label": "Device & Configuration", "value": " · ".join(filter(None, [" ".join(filter(None, [device["brand"], device["model"]])), device["storage"]])), "role": "market_match"},
            {"key": "exterior_condition", "label": "Exterior Condition", "value": f"Grade {grade}" if grade else "Not assessed", "role": "condition_match", "condition_group": condition},
            {"key": "market_evidence", "label": "Malaysia Market Evidence", "value": f"{exact} exact-model/storage comparables · {matched} condition-matched", "role": "evidence"},
            {"key": "evidence_strength", "label": "Evidence Strength", "value": strength, "role": "evidence_quality"},
        ],
        "evidence": {
            "origin": origin,
            "origin_label": {"local_calibration": "Local Malaysian calibration evidence", "local_public_seed": "Local Malaysian public evidence seed", "live_market_retrieval": "Retrieved Malaysian market evidence", "no_local_evidence": "No local evidence available"}[origin],
            "exact_model_count": exact,
            "condition_matched_count": matched,
            "comparable_count": valuation.get("comparable_count", 0),
            "selected_comparable_count": selected_count,
            "evidence_status": evidence_status,
            "strength_label": strength,
            "sources": deepcopy(valuation.get("sources") or []),
            "observed_dates": sorted({row["observed_at"] for row in observations if row.get("observed_at")}),
            "selected_observation_ids": deepcopy(selected_ids),
            "statistics": {key: deepcopy(distribution[key]) for key in ("count", "min", "q1", "median", "q3", "max", "rounded_rm50") if key in distribution},
        },
        "method": {
            "type": "unavailable" if not available else "local_calibrated_comparables" if calibration else "resale_comparable_distribution",
            "range_method": range_method,
            "summary": summary,
            "main_value_drivers": deepcopy(distribution.get("main_value_drivers") or []),
            "explanation": distribution.get("explanation", ""),
        },
        "separate_context": {
            "trade_in_benchmark_count": len(valuation.get("trade_in_benchmark") or []),
            "repair_reference_count": len(valuation.get("repair_references") or []),
            "summary": "Shown separately and not included in the resale market range.",
        },
        "caveats": caveats,
    }


def with_valuation_explainability(valuation: dict[str, Any], **context: Any) -> dict[str, Any]:
    """Attach an additive contract to a copy, leaving the original result unchanged."""
    return {**valuation, "explainability": build_valuation_explainability(valuation, **context)}
