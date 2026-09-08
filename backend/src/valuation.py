from __future__ import annotations

from dataclasses import asdict
from statistics import median
from typing import Any

from .market_evidence import RESALE_PRICE_TYPES, normalize_condition_group, normalize_price_type
from .schemas import MarketObservation, ValuationResult


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("Cannot calculate percentile for an empty list.")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _round_rm50(value: float) -> float:
    return float(int((value + 25) // 50 * 50))


def _observation_from_any(item: MarketObservation | dict[str, Any]) -> MarketObservation:
    if isinstance(item, MarketObservation):
        return item
    return MarketObservation(
        source=item.get("source", ""),
        source_url=item.get("source_url", ""),
        observed_at=item.get("observed_at", ""),
        retrieved_at=item.get("retrieved_at", ""),
        country=item.get("country", ""),
        currency=item.get("currency", ""),
        brand=item.get("brand", ""),
        model=item.get("model", ""),
        storage_gb=item.get("storage_gb"),
        condition=item.get("condition", item.get("condition_text", "")),
        condition_text=item.get("condition_text", item.get("condition", "")),
        condition_group=item.get("condition_group", ""),
        price=item.get("price"),
        price_type=item.get("price_type", ""),
        source_type=item.get("source_type", ""),
        listing_title=item.get("listing_title", ""),
        exact_model_match=bool(item.get("exact_model_match", False)),
        exact_storage_match=bool(item.get("exact_storage_match", False)),
        usable_for_market_range=bool(item.get("usable_for_market_range", False)),
        exclusion_reason=item.get("exclusion_reason", ""),
        grounding_verified=bool(item.get("grounding_verified", False)),
        grounded_source_url=item.get("grounded_source_url", ""),
        grounded_source_title=item.get("grounded_source_title", ""),
        freshness_status=item.get("freshness_status", "unknown"),
    )


def _source_list(observations: list[MarketObservation]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    sources = []
    for obs in observations:
        key = obs.source_url or f"{obs.source}:{obs.listing_title}"
        if key in seen:
            continue
        seen.add(key)
        sources.append(
            {
                "source": obs.source,
                "source_url": obs.source_url,
                "price_type": obs.price_type,
                "retrieved_at": obs.retrieved_at,
                "grounding_verified": obs.grounding_verified,
                "grounded_source_url": obs.grounded_source_url,
                "grounded_source_title": obs.grounded_source_title,
                "freshness_status": obs.freshness_status,
            }
        )
    return sources


def value_from_market_observations(
    *,
    observations: list[MarketObservation | dict[str, Any]],
    brand: str,
    model: str,
    storage_gb: int | None,
    condition_group: str | None = None,
    currency: str = "MYR",
    trade_in_benchmark: list[dict[str, Any]] | None = None,
    repair_references: list[dict[str, Any]] | None = None,
) -> ValuationResult:
    normalized_condition = normalize_condition_group(condition_group)
    typed = [_observation_from_any(item) for item in observations]
    exact = [
        obs
        for obs in typed
        if obs.brand.lower() == brand.lower()
        and obs.model.lower() == model.lower()
        and (storage_gb is None or obs.storage_gb == storage_gb)
        and obs.currency.upper() in {currency.upper(), "RM", "MYR"}
        and obs.price is not None
        and normalize_price_type(obs.price_type) in RESALE_PRICE_TYPES
        and (obs.usable_for_market_range or not obs.exclusion_reason)
    ]
    condition_matched = [
        obs
        for obs in exact
        if normalized_condition in {"", "unknown"}
        or normalize_condition_group(obs.condition_group or obs.condition) == normalized_condition
    ]

    if len(condition_matched) >= 5:
        usable = condition_matched
        status = "estimated_market_value"
        evidence_status = "strong_local_evidence"
        evidence_note = "Range uses condition-matched exact model/storage Malaysia comparables."
    elif len(exact) >= 5:
        usable = exact
        status = "prototype_market_estimate"
        evidence_status = "moderate_local_evidence"
        evidence_note = "Range uses exact model/storage Malaysia comparables because condition-matched evidence is limited."
    elif len(exact) >= 3:
        usable = exact
        status = "limited_local_market_evidence"
        evidence_status = "limited_local_evidence"
        evidence_note = "Only 3-4 exact model/storage Malaysia comparables are available."
    else:
        return ValuationResult(
            status="insufficient_market_evidence",
            currency=currency,
            value_low=None,
            value_high=None,
            midpoint=None,
            comparable_count=0,
            exact_model_count=len(exact),
            condition_matched_count=len(condition_matched),
            evidence_status="insufficient_market_evidence",
            notes=["Fewer than three reliable exact model/storage Malaysia resale comparables are available."],
            limitations=[
                "Live Internal Check is not implemented; no hidden functional defect is assumed.",
                "Trade-in and repair evidence are not included in the resale Q1-Q3 distribution.",
            ],
            trade_in_benchmark=trade_in_benchmark or [],
            repair_references=repair_references or [],
            sources=_source_list(exact),
        )

    prices = [float(obs.price) for obs in usable if obs.price is not None and obs.price > 0]
    q1 = _percentile(prices, 0.25)
    q3 = _percentile(prices, 0.75)
    med = median(prices)
    rounded_low = _round_rm50(q1)
    rounded_high = _round_rm50(q3)
    rounded_midpoint = _round_rm50((rounded_low + rounded_high) / 2)

    return ValuationResult(
        status=status,
        currency=currency,
        value_low=rounded_low,
        value_high=rounded_high,
        midpoint=rounded_midpoint,
        comparable_count=len(usable),
        exact_model_count=len(exact),
        condition_matched_count=len(condition_matched),
        evidence_status=evidence_status,
        notes=[evidence_note, "Final merchant quote still requires physical inspection."],
        limitations=[
            "Live Internal Check is not implemented; internal_assessment_status is not_assessed.",
            "Exterior cosmetic condition alone is not treated as a functional defect.",
            "Trade-in benchmarks and repair references are reported separately from resale comparables.",
        ],
        trade_in_benchmark=trade_in_benchmark or [],
        repair_references=repair_references or [],
        sources=_source_list(usable),
        distribution={
            "count": len(prices),
            "min": min(prices),
            "q1": q1,
            "median": med,
            "q3": q3,
            "max": max(prices),
            "rounded_rm50": {
                "value_low": rounded_low,
                "value_high": rounded_high,
                "midpoint": rounded_midpoint,
            },
            "observations": [asdict(obs) for obs in usable],
        },
    )
