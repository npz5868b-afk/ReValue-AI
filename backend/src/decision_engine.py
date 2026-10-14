from __future__ import annotations

import copy
import math
from typing import Any

from .grading_rules import SEVERE_DAMAGE_TYPES


ENGINE_VERSION = "decision_support_v1"

SUPPORTED_PRIORITIES = {
    "balanced",
    "maximize_value",
    "convenience",
    "minimize_spend",
    "extend_device_life",
}

VALID_ACTIONS = {"repair", "resell", "trade_in", "keep"}
VALID_DECISION_STRENGTHS_WITH_ACTION = {"clear", "moderate"}
VALID_REVIEW_STRENGTH = "review_required"
MARKET_RANGE_STATUSES = {
    "estimated_market_value",
    "prototype_market_estimate",
    "limited_local_market_evidence",
}
SUPPORTED_CURRENCIES = {"MYR"}

# Transparent decision heuristics. These are product decision thresholds, not
# valuation weights, ML parameters, or calibrated probabilities.
TRADE_IN_COMPETITIVE_RATIO = 0.90
TRADE_IN_MIN_VIABLE_RATIO = 0.65
REPAIR_COST_TO_MARKET_MAX_RATIO = 0.20
REPAIR_COST_MAX_RATIO = REPAIR_COST_TO_MARKET_MAX_RATIO
RESELL_ADVANTAGE_MIN_RM = 150.0
MAX_REASONABLE_DEVICE_VALUE_RM = 1_000_000.0
HEALTHY_EXTERIOR_GRADES = {"A", "B"}
DAMAGED_EXTERIOR_GRADES = {"C", "C-"}
SUPPORTED_EXTERIOR_GRADES = HEALTHY_EXTERIOR_GRADES | DAMAGED_EXTERIOR_GRADES
REPAIR_RELEVANT_DAMAGE_TYPES = set(SEVERE_DAMAGE_TYPES) | {"generic_crack", "crack"}


class DecisionInputError(ValueError):
    def __init__(self, error: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.error = error
        self.message = message
        self.extra = extra

    def to_response(self) -> dict[str, Any]:
        return {
            "status": "error",
            "error": self.error,
            "message": self.message,
            **copy.deepcopy(self.extra),
        }


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) and abs(number) <= MAX_REASONABLE_DEVICE_VALUE_RM else None
    text = str(value).replace("RM", "").replace("MYR", "").replace(",", "").strip()
    try:
        number = float(text)
        return number if math.isfinite(number) and abs(number) <= MAX_REASONABLE_DEVICE_VALUE_RM else None
    except ValueError:
        return None


def _money(value: float | None) -> str:
    if value is None:
        return "unknown"
    return f"RM{int(round(value)):,}"


def _normalize_priority(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return "balanced"
    priority = str(value).strip().lower()
    if priority not in SUPPORTED_PRIORITIES:
        raise DecisionInputError(
            "invalid_user_priority",
            "user_priority must be one of the supported decision priorities.",
            supported_priorities=sorted(SUPPORTED_PRIORITIES),
        )
    return priority


def _normalize_currency(value: Any) -> str:
    if value is None:
        return "MYR"
    if not isinstance(value, str):
        raise DecisionInputError(
            "unsupported_currency",
            "Decision support currently supports MYR valuation evidence only.",
            supported_currencies=sorted(SUPPORTED_CURRENCIES),
        )
    currency = value.strip().upper()
    if currency == "":
        return "MYR"
    if currency not in SUPPORTED_CURRENCIES:
        raise DecisionInputError(
            "unsupported_currency",
            "Decision support currently supports MYR valuation evidence only.",
            supported_currencies=sorted(SUPPORTED_CURRENCIES),
        )
    return currency


def _normalize_grade(value: Any) -> str:
    return str(value or "").strip().upper()


def _valuation_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    valuation = payload.get("valuation")
    return valuation if isinstance(valuation, dict) else {}


def normalize_market_range(valuation: dict[str, Any]) -> dict[str, Any]:
    currency = _normalize_currency(valuation.get("currency"))
    low = _number(valuation.get("value_low"))
    high = _number(valuation.get("value_high"))
    midpoint = _number(valuation.get("midpoint"))
    status = str(valuation.get("status") or "")
    valid = (
        low is not None
        and high is not None
        and low > 0
        and high >= low
        and (not status or status in MARKET_RANGE_STATUSES)
    )
    midpoint_normalized = False
    if valid and (midpoint is None or midpoint <= 0 or midpoint < low or midpoint > high):
        midpoint = low + (high - low) / 2
        midpoint_normalized = True
    if valid and (midpoint is None or not math.isfinite(midpoint) or midpoint > MAX_REASONABLE_DEVICE_VALUE_RM):
        valid = False
        midpoint = None
    return {
        "available": bool(valid),
        "value_low": low,
        "value_high": high,
        "midpoint": midpoint,
        "midpoint_normalized": midpoint_normalized,
        "currency": currency,
        "status": status,
        "evidence_status": valuation.get("evidence_status"),
        "comparable_count": valuation.get("comparable_count"),
        "exact_model_count": valuation.get("exact_model_count"),
        "condition_matched_count": valuation.get("condition_matched_count"),
        "fallback_source": valuation.get("fallback_source"),
    }


def _walk_numeric_values(item: Any, keys: set[str]) -> list[float]:
    values: list[float] = []
    if isinstance(item, dict):
        for key, value in item.items():
            lowered = str(key).lower()
            if lowered in keys:
                number = _number(value)
                if number is not None and number > 0:
                    values.append(number)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    values.extend(_walk_numeric_values(child, keys))
    return values


def _coerce_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def normalize_trade_in_evidence(valuation: dict[str, Any]) -> dict[str, Any]:
    items = _coerce_list(valuation.get("trade_in_benchmark")) + _coerce_list(valuation.get("trade_in_benchmarks"))
    prices: list[float] = []
    for item in items:
        if isinstance(item, (int, float, str)):
            number = _number(item)
            if number is not None and number > 0:
                prices.append(number)
            continue
        prices.extend(
            _walk_numeric_values(
                item,
                {
                    "price",
                    "trade_in_price_rm",
                    "trade_in_offer_rm",
                    "offer_rm",
                    "amount_rm",
                    "value",
                },
            )
        )
    prices = sorted({float(price) for price in prices if price > 0})
    return {
        "available": bool(prices),
        "prices": prices,
        "best": max(prices) if prices else None,
        "count": len(prices),
    }


def normalize_repair_evidence(valuation: dict[str, Any]) -> dict[str, Any]:
    items = _coerce_list(valuation.get("repair_references")) + _coerce_list(valuation.get("repair_cost_references"))
    prices: list[float] = []
    for item in items:
        if isinstance(item, (int, float, str)):
            number = _number(item)
            if number is not None and number > 0:
                prices.append(number)
            continue
        if isinstance(item, dict):
            low = _number(item.get("low"))
            high = _number(item.get("high"))
            if low is not None and low > 0:
                prices.append(low)
            if high is not None and high > 0:
                prices.append(high)
            prices.extend(
                _walk_numeric_values(
                    item,
                    {
                        "price",
                        "repair_cost_rm",
                        "cost_rm",
                        "repair_price_rm",
                    },
                )
            )
    prices = sorted({float(price) for price in prices if price > 0})
    low_price = min(prices) if prices else None
    high_price = max(prices) if prices else None
    midpoint = (low_price + high_price) / 2 if low_price is not None and high_price is not None else None
    return {
        "available": bool(prices),
        "prices": prices,
        "low": low_price,
        "high": high_price,
        "midpoint": midpoint,
        "count": len(prices),
    }


def _damage_items(payload: dict[str, Any]) -> list[Any]:
    items: list[Any] = []
    seen: set[tuple[Any, ...]] = set()
    for key in ("detected_damage", "merged_damage", "damage"):
        value = payload.get(key)
        for item in _coerce_list(value):
            marker = _damage_item_marker(item)
            if marker in seen:
                continue
            seen.add(marker)
            items.append(item)
    return items


def _damage_item_marker(item: Any) -> tuple[Any, ...]:
    if isinstance(item, dict):
        return (
            "dict",
            tuple(sorted((str(key), str(value)) for key, value in item.items())),
        )
    if isinstance(item, str):
        return ("str", _normalize_legacy_damage_text(item))
    return (type(item).__name__, str(item))


def _damage_type(item: dict[str, Any]) -> str:
    return str(item.get("damage_type") or item.get("type") or "").strip().lower()


def _is_confirmed_structured_damage(item: dict[str, Any]) -> bool:
    if str(item.get("verification") or "").strip().lower() != "confirmed":
        return False
    if item.get("grade_affecting") is False:
        return False
    return bool(_damage_type(item))


def _confirmed_structured_damage(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in _damage_items(payload) if isinstance(item, dict) and _is_confirmed_structured_damage(item)]


def _normalize_legacy_damage_text(text: Any) -> str:
    return " ".join(str(text).replace("_", " ").replace("-", " ").strip().lower().split())


def _legacy_damage_texts(payload: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for item in _damage_items(payload):
        if isinstance(item, str):
            text = _normalize_legacy_damage_text(item)
            if text and text not in {"none", "none confirmed", "no confirmed visible damage"}:
                texts.append(text)
    return texts


def _legacy_text_indicates_severe_damage(text: str) -> bool:
    severe_phrases = (
        "screen crack",
        "screen cracked",
        "back glass crack",
        "back glass cracked",
        "camera glass damage",
        "camera glass cracked",
        "camera island impact",
        "shatter",
        "shattered",
        "broken primary surface",
    )
    return any(phrase in text for phrase in severe_phrases)


def _has_confirmed_damage(payload: dict[str, Any]) -> bool:
    return bool(_confirmed_structured_damage(payload) or _legacy_damage_texts(payload))


def _has_repair_relevant_damage(payload: dict[str, Any], grade: str) -> bool:
    if grade in DAMAGED_EXTERIOR_GRADES:
        return True
    for item in _confirmed_structured_damage(payload):
        if _damage_type(item) in REPAIR_RELEVANT_DAMAGE_TYPES:
            return True
    return any(_legacy_text_indicates_severe_damage(text) for text in _legacy_damage_texts(payload))


def _exterior_consistency_issue(payload: dict[str, Any], grade: str) -> dict[str, str] | None:
    confirmed_items = _confirmed_structured_damage(payload)
    legacy_texts = _legacy_damage_texts(payload)
    if grade == "A" and (confirmed_items or legacy_texts):
        return _reason(
            "inconsistent_exterior_evidence",
            "Inconsistent exterior evidence",
            "Exterior Grade A cannot be reconciled with confirmed grade-affecting visible damage.",
        )
    if grade == "B":
        for item in confirmed_items:
            if _damage_type(item) in REPAIR_RELEVANT_DAMAGE_TYPES:
                return _reason(
                    "inconsistent_exterior_evidence",
                    "Inconsistent exterior evidence",
                    "Exterior Grade B cannot be reconciled with confirmed major crack or primary-surface damage.",
                )
        if any(_legacy_text_indicates_severe_damage(text) for text in legacy_texts):
            return _reason(
                "inconsistent_exterior_evidence",
                "Inconsistent exterior evidence",
                "Exterior Grade B cannot be reconciled with confirmed major crack or primary-surface damage.",
            )
    return None


def _internal_assessment_verified(payload: dict[str, Any]) -> bool:
    status = str(
        payload.get("internal_assessment_status")
        or payload.get("internal_status")
        or payload.get("internal_verification_status")
        or ""
    ).strip().lower()
    return status in {"verified", "completed_verified", "hardware_verified"}


def _confirmed_device_complete(payload: dict[str, Any]) -> bool:
    device = payload.get("confirmed_device")
    if not isinstance(device, dict):
        return False
    for key in ("brand", "model", "storage"):
        value = device.get(key)
        if not isinstance(value, str) or not value.strip():
            return False
    return True


def _base_alternatives() -> dict[str, dict[str, Any]]:
    return {
        "repair": {"status": "unknown", "reason": "Repair evidence has not been evaluated yet."},
        "resell": {"status": "unknown", "reason": "Resale market evidence has not been evaluated yet."},
        "trade_in": {"status": "unknown", "reason": "Trade-in benchmark evidence has not been evaluated yet."},
        "keep": {"status": "unknown", "reason": "Keeping remains available but depends on user priority."},
    }


def _reason(code: str, label: str, detail: str) -> dict[str, str]:
    return {"code": code, "label": label, "detail": detail}


def _trade_in_state(trade_best: float | None, market_low: float, market_high: float) -> str:
    if trade_best is None:
        return "absent"
    if trade_best > market_high:
        return "above_resale_range"
    if trade_best >= market_low:
        return "inside_resale_range"
    if trade_best >= market_low * TRADE_IN_COMPETITIVE_RATIO:
        return "competitive_with_market_floor"
    if trade_best >= market_low * TRADE_IN_MIN_VIABLE_RATIO:
        return "viable_below_resale_range"
    return "below_minimum_viability"


def _trade_in_alternative_status(state: str) -> str:
    if state == "absent":
        return "unknown"
    if state == "below_minimum_viability":
        return "available_but_weak"
    if state == "above_resale_range":
        return "exceeds_resale_range"
    if state in {"inside_resale_range", "competitive_with_market_floor"}:
        return "competitive"
    return "available"


def _trade_in_reason_detail(state: str, trade_best: float | None, market_low: float, market_high: float) -> str:
    if state == "absent":
        return "No verified trade-in benchmark is currently available for comparison."
    if state == "above_resale_range":
        return "The verified trade-in benchmark exceeds the current evidence-backed resale range."
    if state == "inside_resale_range":
        return "The verified trade-in benchmark sits within the current evidence-backed resale range."
    if state == "competitive_with_market_floor":
        return (
            f"The verified trade-in benchmark is {_money(trade_best)}, "
            f"competitive with the lower end of the current resale range at {_money(market_low)}."
        )
    if state == "viable_below_resale_range":
        return "The verified trade-in benchmark is below the current evidence-backed resale range, but clears the minimum viability threshold."
    return "A verified trade-in option exists, but it is materially below the current resale market range."


def _trade_in_reason_for_recommendation(state: str, trade_best: float | None, market_low: float, market_high: float) -> dict[str, str]:
    if state == "above_resale_range":
        return _reason(
            "trade_in_exceeds_resale_range",
            "Trade-in exceeds resale range",
            _trade_in_reason_detail(state, trade_best, market_low, market_high),
        )
    if state == "inside_resale_range":
        return _reason(
            "trade_in_inside_resale_range",
            "Trade-in within resale range",
            _trade_in_reason_detail(state, trade_best, market_low, market_high),
        )
    return _reason(
        "trade_in_competitive_with_market_floor",
        "Competitive trade-in benchmark",
        _trade_in_reason_detail(state, trade_best, market_low, market_high),
    )


def _finish(
    *,
    payload: dict[str, Any],
    action: str | None,
    strength: str,
    headline: str,
    reasons: list[dict[str, str]],
    alternatives: dict[str, dict[str, Any]],
    evidence_used: dict[str, Any],
    evidence_missing: list[str],
    rules_triggered: list[str],
    caveats: list[str],
) -> dict[str, Any]:
    if action is not None and action not in VALID_ACTIONS:
        raise ValueError(f"Unsupported decision action: {action}")
    if action is None and strength != VALID_REVIEW_STRENGTH:
        raise ValueError("review_required strength is required when no action is recommended")
    if action is not None and strength not in VALID_DECISION_STRENGTHS_WITH_ACTION:
        raise ValueError("recommended actions must use clear or moderate decision strength")
    return {
        "status": "ok" if action else "review_required",
        "engine_version": ENGINE_VERSION,
        "recommended_action": action,
        "decision_strength": strength,
        "headline": headline,
        "reasons": reasons,
        "alternatives": alternatives,
        "evidence_used": evidence_used,
        "evidence_missing": evidence_missing,
        "rules_triggered": rules_triggered,
        "caveats": caveats,
    }


def recommend_next_action(request_payload: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic next-action guidance from an existing assessment.

    This engine consumes already-produced evidence. It does not retrieve market
    rows, does not recompute resale value, and does not treat frontend shop
    offer estimates as real merchant quotes.
    """

    if request_payload is not None and not isinstance(request_payload, dict):
        raise DecisionInputError(
            "invalid_request_body",
            "Request body must be a JSON object.",
        )
    payload = copy.deepcopy(request_payload or {})
    valuation = _valuation_from_payload(payload)
    market = normalize_market_range(valuation)
    trade_in = normalize_trade_in_evidence(valuation)
    repair = normalize_repair_evidence(valuation)
    priority = _normalize_priority(payload.get("user_priority"))
    grade = _normalize_grade(payload.get("exterior_grade"))
    has_confirmed_damage = _has_confirmed_damage(payload)
    has_repair_relevant_damage = _has_repair_relevant_damage(payload, grade)
    verified_internal = _internal_assessment_verified(payload)
    internal_grade = payload.get("internal_grade")
    confirmed_device_complete = _confirmed_device_complete(payload)

    evidence_missing: list[str] = []
    if not confirmed_device_complete:
        evidence_missing.append("confirmed_device_identity")
    if not verified_internal and internal_grade:
        evidence_missing.append("verified_internal_assessment")
    if not trade_in["available"]:
        evidence_missing.append("real_trade_in_benchmark")
    if has_repair_relevant_damage and not repair["available"]:
        evidence_missing.append("repair_cost_reference")

    evidence_used = {
        "confirmed_device": copy.deepcopy(payload.get("confirmed_device") or {}),
        "exterior_grade": grade or None,
        "detected_damage_considered": bool(has_confirmed_damage or grade in DAMAGED_EXTERIOR_GRADES),
        "repair_relevant_damage_considered": bool(has_repair_relevant_damage),
        "market_range": market,
        "trade_in_benchmark": trade_in,
        "repair_evidence": repair,
        "currency": market["currency"],
        "user_priority": priority,
        "internal_assessment_verified": bool(verified_internal),
        "internal_grade_used": False,
        "ignored_internal_grade": None if internal_grade is None else internal_grade,
    }

    caveats = [
        "Final merchant quote requires physical inspection.",
        "Recommendation reflects the selected user priority and available evidence.",
    ]
    if not verified_internal:
        caveats.append("Internal hardware status is not treated as verified in the current live MVP.")
    if repair["available"]:
        caveats.append("Repair-cost references are separate from the resale market range.")

    alternatives = _base_alternatives()

    if not market["available"]:
        alternatives["resell"] = {
            "status": "not_recommended",
            "reason": "No reliable market range is available for comparison.",
        }
        alternatives["trade_in"] = {
            "status": "available_or_unknown" if trade_in["available"] else "unknown",
            "reason": "Trade-in cannot be compared without a reliable resale range.",
        }
        alternatives["repair"] = {
            "status": "available_or_unknown" if repair["available"] else "unknown",
            "reason": "Repair cannot be evaluated without a reliable resale range.",
        }
        alternatives["keep"] = {
            "status": "viable",
            "reason": "Keeping remains viable while evidence is incomplete.",
        }
        return _finish(
            payload=payload,
            action=None,
            strength="review_required",
            headline="There is not enough reliable market evidence to recommend a next action.",
            reasons=[
                _reason(
                    "insufficient_market_evidence",
                    "Insufficient market evidence",
                    "A valid resale market range is required before repair, resale, trade-in and keep options can be compared.",
                )
            ],
            alternatives=alternatives,
            evidence_used=evidence_used,
            evidence_missing=sorted(set(evidence_missing + ["reliable_market_range"])),
            rules_triggered=["insufficient_market_evidence"],
            caveats=caveats,
        )

    market_low = float(market["value_low"])
    market_high = float(market["value_high"])
    market_midpoint = float(market["midpoint"] or (market_low + market_high) / 2)
    trade_best = trade_in["best"]
    repair_midpoint = repair["midpoint"]
    if not confirmed_device_complete:
        alternatives["resell"] = {
            "status": "not_recommended",
            "reason": "Confirmed device identity is required before resale, repair, trade-in and keep options can be compared.",
        }
        alternatives["trade_in"] = {
            "status": "available_or_unknown" if trade_in["available"] else "unknown",
            "reason": "Trade-in cannot be evaluated as the primary action without confirmed brand, model and storage.",
        }
        alternatives["repair"] = {
            "status": "available_or_unknown" if repair["available"] else "unknown",
            "reason": "Repair cannot be evaluated as the primary action without confirmed brand, model and storage.",
        }
        alternatives["keep"] = {
            "status": "viable",
            "reason": "Keeping remains viable while confirmed device identity is incomplete.",
        }
        return _finish(
            payload=payload,
            action=None,
            strength="review_required",
            headline="Confirmed device identity is required before ReValue AI can provide a reliable next-action recommendation.",
            reasons=[
                _reason(
                    "confirmed_device_required",
                    "Confirmed device required",
                    "Confirmed brand, model and storage are required before ReValue AI can provide a reliable next-action recommendation.",
                )
            ],
            alternatives=alternatives,
            evidence_used=evidence_used,
            evidence_missing=sorted(set(evidence_missing + ["confirmed_device_identity"])),
            rules_triggered=["confirmed_device_required"],
            caveats=caveats,
        )

    grade_supported = grade in SUPPORTED_EXTERIOR_GRADES
    if not grade_supported:
        alternatives["resell"] = {
            "status": "not_recommended",
            "reason": "Exterior condition is required before resale, repair, trade-in and keep options can be compared.",
        }
        alternatives["trade_in"] = {
            "status": "available_or_unknown" if trade_in["available"] else "unknown",
            "reason": "Trade-in cannot be evaluated as the primary action without exterior condition evidence.",
        }
        alternatives["repair"] = {
            "status": "available_or_unknown" if repair["available"] else "unknown",
            "reason": "Repair cannot be evaluated without exterior condition evidence.",
        }
        alternatives["keep"] = {
            "status": "viable",
            "reason": "Keeping remains viable while condition evidence is incomplete.",
        }
        return _finish(
            payload=payload,
            action=None,
            strength="review_required",
            headline="Exterior condition assessment is required before ReValue AI can provide a reliable next-action recommendation.",
            reasons=[
                _reason(
                    "exterior_condition_required",
                    "Exterior condition required",
                    "Exterior condition assessment is required before ReValue AI can provide a reliable next-action recommendation.",
                )
            ],
            alternatives=alternatives,
            evidence_used=evidence_used,
            evidence_missing=sorted(set(evidence_missing + ["exterior_condition_assessment"])),
            rules_triggered=["exterior_condition_required"],
            caveats=caveats,
        )

    consistency_issue = _exterior_consistency_issue(payload, grade)
    if consistency_issue is not None:
        alternatives["resell"] = {
            "status": "not_recommended",
            "reason": "Exterior evidence is inconsistent and requires review before resale can be recommended.",
        }
        alternatives["trade_in"] = {
            "status": "available_or_unknown" if trade_in["available"] else "unknown",
            "reason": "Trade-in cannot be selected as the primary action while exterior evidence is inconsistent.",
        }
        alternatives["repair"] = {
            "status": "review_required",
            "reason": "Repair cannot be selected automatically while exterior evidence is inconsistent.",
        }
        alternatives["keep"] = {
            "status": "review_required",
            "reason": "Keeping remains possible, but the exterior assessment must be reviewed first.",
        }
        return _finish(
            payload=payload,
            action=None,
            strength="review_required",
            headline="Exterior assessment evidence is inconsistent and requires review.",
            reasons=[consistency_issue],
            alternatives=alternatives,
            evidence_used=evidence_used,
            evidence_missing=sorted(set(evidence_missing + ["exterior_evidence_consistency_review"])),
            rules_triggered=["inconsistent_exterior_evidence"],
            caveats=caveats,
        )

    trade_state = _trade_in_state(trade_best, market_low, market_high)
    trade_viable = trade_state not in {"absent", "below_minimum_viability"}
    trade_competitive = trade_state in {"competitive_with_market_floor", "inside_resale_range", "above_resale_range"}
    trade_exceeds_resale_range = trade_state == "above_resale_range"
    repair_ratio = repair_midpoint / market_midpoint if repair_midpoint is not None and market_midpoint > 0 else None
    repair_economical = bool(repair_ratio is not None and repair_ratio <= REPAIR_COST_TO_MARKET_MAX_RATIO)

    alternatives["resell"] = {
        "status": "available",
        "reason": f"Resale range is {_money(market_low)} to {_money(market_high)}.",
    }
    alternatives["trade_in"] = {
        "status": _trade_in_alternative_status(trade_state),
        "reason": _trade_in_reason_detail(trade_state, trade_best, market_low, market_high),
    }
    alternatives["repair"] = {
        "status": "worth_considering" if repair_economical and has_repair_relevant_damage else ("available" if repair["available"] else "not_needed"),
        "reason": (
            f"Repair evidence range is {_money(repair['low'])} to {_money(repair['high'])}."
            if repair["available"]
            else "No repair-relevant evidence is currently available."
        ),
    }
    alternatives["keep"] = {
        "status": "viable",
        "reason": "Keeping remains viable, especially when the user wants to minimise spending or extend device life.",
    }

    if priority in {"extend_device_life", "minimize_spend"} and grade in HEALTHY_EXTERIOR_GRADES and not has_repair_relevant_damage:
        alternatives["keep"]["status"] = "recommended"
        return _finish(
            payload=payload,
            action="keep",
            strength="moderate",
            headline="Keeping the phone is reasonable for your selected priority.",
            reasons=[
                _reason(
                    f"{priority}_priority",
                    "Priority favours keeping",
                    "The selected priority favours continued use instead of maximising cash today.",
                ),
                _reason(
                    "healthy_exterior_condition",
                    "No repair-relevant exterior damage",
                    f"Exterior Grade {grade} does not indicate confirmed repair-relevant cosmetic damage.",
                ),
            ],
            alternatives=alternatives,
            evidence_used=evidence_used,
            evidence_missing=sorted(set(evidence_missing)),
            rules_triggered=[f"{priority}_healthy_keep"],
            caveats=caveats,
        )

    if trade_exceeds_resale_range and priority in {"balanced", "maximize_value", "convenience"}:
        alternatives["trade_in"]["status"] = "recommended"
        reasons = [
            _trade_in_reason_for_recommendation(trade_state, trade_best, market_low, market_high)
        ]
        rules = ["trade_in_exceeds_resale_range"]
        if priority == "maximize_value":
            reasons.insert(
                0,
                _reason(
                    "maximize_value_priority",
                    "Priority favours highest verified immediate value",
                    "The selected priority favours the strongest verified immediate financial option.",
                ),
            )
            rules.append("maximize_value_verified_trade_in_above_resale")
        if has_repair_relevant_damage:
            reasons.append(
                _reason(
                    "post_repair_value_not_established",
                    "Post-repair value not established",
                    "Repair may change the result, but post-repair market value evidence is not available in the current decision payload.",
                )
            )
            rules.append("repair_upside_unevaluated")
        return _finish(
            payload=payload,
            action="trade_in",
            strength="moderate" if has_repair_relevant_damage else "clear",
            headline="The verified trade-in benchmark exceeds the current resale range.",
            reasons=reasons,
            alternatives=alternatives,
            evidence_used=evidence_used,
            evidence_missing=sorted(set(evidence_missing)),
            rules_triggered=rules,
            caveats=caveats,
        )

    if has_repair_relevant_damage and not repair["available"]:
        if priority == "convenience" and trade_viable:
            alternatives["trade_in"]["status"] = "recommended"
            return _finish(
                payload=payload,
                action="trade_in",
                strength="clear" if trade_competitive else "moderate",
                headline="Trade-in may be the most convenient viable next step.",
                reasons=[
                    _reason(
                        "convenience_priority",
                        "Priority favours convenience",
                        "The selected priority favours a simpler exit path, but only if the trade-in benchmark is financially viable.",
                    ),
                    _reason(
                        "trade_in_minimum_viability_met",
                        "Trade-in viability threshold met",
                        f"The best verified trade-in benchmark is {_money(trade_best)}, meeting the minimum viability threshold against the market floor of {_money(market_low)}.",
                    ),
                    _reason(
                        "repair_cost_reference_missing",
                        "Repair-cost evidence missing",
                        "Repair-cost evidence is unavailable, so repair economics are not used.",
                    ),
                ],
                alternatives=alternatives,
                evidence_used=evidence_used,
                evidence_missing=sorted(set(evidence_missing)),
                rules_triggered=["convenience_trade_in_minimum_viability_met"],
                caveats=caveats,
            )
        if priority in {"balanced", "convenience"} and trade_competitive:
            alternatives["trade_in"]["status"] = "recommended"
            return _finish(
                payload=payload,
                action="trade_in",
                strength="moderate",
                headline="Trade-in is supported by verified benchmark evidence.",
                reasons=[
                    _trade_in_reason_for_recommendation(trade_state, trade_best, market_low, market_high),
                    _reason(
                        "repair_cost_reference_missing",
                        "Repair-cost evidence missing",
                        "Repair-cost evidence is unavailable, so repair economics are not used.",
                    ),
                ],
                alternatives=alternatives,
                evidence_used=evidence_used,
                evidence_missing=sorted(set(evidence_missing)),
                rules_triggered=["trade_in_competitive_ratio_met", "repair_cost_reference_missing"],
                caveats=caveats,
            )
        alternatives["repair"]["status"] = "review_required"
        alternatives["repair"]["reason"] = "Repair-cost evidence is required before repair economics can be compared."
        if trade_in["available"] and not trade_viable:
            alternatives["trade_in"]["status"] = "available_but_weak"
        return _finish(
            payload=payload,
            action=None,
            strength="review_required",
            headline="Repair-cost evidence is required before ReValue AI can reliably compare next actions.",
            reasons=[
                _reason(
                    "repair_cost_reference_required",
                    "Repair-cost evidence required",
                    "Repair-cost evidence is required before ReValue AI can reliably compare repairing the device against selling it in its current condition.",
                )
            ],
            alternatives=alternatives,
            evidence_used=evidence_used,
            evidence_missing=sorted(set(evidence_missing + ["repair_cost_reference"])),
            rules_triggered=["repair_cost_reference_required_for_damaged_device"],
            caveats=caveats,
        )

    if priority == "maximize_value" and has_repair_relevant_damage and repair["available"]:
        alternatives["repair"]["status"] = "worth_considering" if repair_economical else "available"
        return _finish(
            payload=payload,
            action=None,
            strength="review_required",
            headline="Post-repair market value evidence is required before ReValue AI can identify the maximum-value action.",
            reasons=[
                _reason(
                    "post_repair_value_required",
                    "Post-repair value evidence required",
                    "Repair-cost evidence alone does not prove repair maximizes value because the current payload does not include post-repair resale value evidence.",
                )
            ],
            alternatives=alternatives,
            evidence_used=evidence_used,
            evidence_missing=sorted(set(evidence_missing + ["post_repair_market_value"])),
            rules_triggered=["maximize_value_requires_post_repair_value_for_repair_choice"],
            caveats=caveats,
        )

    if has_repair_relevant_damage and repair_economical and priority in {"balanced", "extend_device_life"}:
        alternatives["repair"]["status"] = "recommended"
        return _finish(
            payload=payload,
            action="repair",
            strength="moderate",
            headline="Repair is worth considering before sale.",
            reasons=[
                _reason(
                    "repair_cost_small_relative_to_market_value",
                    "Repair cost is relatively small",
                    f"Available repair-cost evidence is about {repair_ratio:.0%} of the current market midpoint.",
                ),
                _reason(
                    "repair_relevant_exterior_condition",
                    "Repair-relevant exterior condition",
                    f"Exterior Grade {grade} indicates repair-relevant visible condition.",
                ),
            ],
            alternatives=alternatives,
            evidence_used=evidence_used,
            evidence_missing=sorted(set(evidence_missing)),
            rules_triggered=["repair_economics_within_threshold"],
            caveats=caveats,
        )

    if priority == "minimize_spend" and has_repair_relevant_damage:
        alternatives["repair"]["status"] = "review_required"
        alternatives["repair"]["reason"] = "Repair evidence alone does not prove the lowest-spend path."
        alternatives["resell"]["status"] = "not_recommended"
        alternatives["resell"]["reason"] = "Resale market value does not establish the lowest-spend next action."
        return _finish(
            payload=payload,
            action=None,
            strength="review_required",
            headline="Additional usability and repair-necessity evidence is required before ReValue AI can determine the lowest-spend next action.",
            reasons=[
                _reason(
                    "minimize_spend_requires_usability_context",
                    "Lowest-spend path not established",
                    "Additional usability and repair-necessity evidence is required before ReValue AI can determine the lowest-spend next action.",
                )
            ],
            alternatives=alternatives,
            evidence_used=evidence_used,
            evidence_missing=sorted(set(evidence_missing + ["usability_evidence", "repair_necessity_evidence"])),
            rules_triggered=["minimize_spend_review_required_for_damaged_device"],
            caveats=caveats,
        )

    if priority == "extend_device_life" and has_repair_relevant_damage:
        alternatives["repair"]["status"] = "review_required"
        alternatives["repair"]["reason"] = "Repair is not supported strongly enough to recommend extending device life automatically."
        alternatives["resell"]["status"] = "not_recommended"
        alternatives["resell"]["reason"] = "Resale market value does not support a device-life-extension recommendation."
        return _finish(
            payload=payload,
            action=None,
            strength="review_required",
            headline="Additional repair evidence is required before ReValue AI can recommend a device-life-extension action.",
            reasons=[
                _reason(
                    "extend_device_life_repair_support_missing",
                    "Device-life path not established",
                    "Repair evidence does not support a device-life-extension recommendation, and resale evidence does not answer the selected priority.",
                )
            ],
            alternatives=alternatives,
            evidence_used=evidence_used,
            evidence_missing=sorted(set(evidence_missing + ["device_life_extension_evidence"])),
            rules_triggered=["extend_device_life_review_required_without_supported_repair"],
            caveats=caveats,
        )

    if priority == "convenience" and trade_viable:
        alternatives["trade_in"]["status"] = "recommended"
        return _finish(
            payload=payload,
            action="trade_in",
            strength="clear" if trade_competitive else "moderate",
            headline="Trade-in may be the most convenient next step.",
            reasons=[
                _reason(
                    "convenience_priority",
                    "Priority favours convenience",
                    "The selected priority favours a simpler exit path even if resale may have more upside.",
                ),
                _reason(
                    "real_trade_in_benchmark_available",
                    "Verified trade-in benchmark available",
                    f"The best verified trade-in benchmark found is {_money(trade_best)}.",
                ),
            ],
            alternatives=alternatives,
            evidence_used=evidence_used,
            evidence_missing=sorted(set(evidence_missing)),
            rules_triggered=["convenience_trade_in_available"],
            caveats=caveats,
        )

    if trade_competitive and priority in {"balanced", "convenience"}:
        alternatives["trade_in"]["status"] = "recommended"
        return _finish(
            payload=payload,
            action="trade_in",
            strength="moderate",
            headline="Trade-in is supported by verified benchmark evidence.",
            reasons=[
                _trade_in_reason_for_recommendation(trade_state, trade_best, market_low, market_high)
            ],
            alternatives=alternatives,
            evidence_used=evidence_used,
            evidence_missing=sorted(set(evidence_missing)),
            rules_triggered=["trade_in_competitive_ratio_met"],
            caveats=caveats,
        )

    if priority == "convenience":
        alternatives["resell"]["status"] = "not_recommended"
        alternatives["resell"]["reason"] = "Resale market value does not prove this is the most convenient path."
        return _finish(
            payload=payload,
            action=None,
            strength="review_required",
            headline="Verified convenience evidence is required before ReValue AI can recommend the most convenient next action.",
            reasons=[
                _reason(
                    "convenience_path_not_established",
                    "Convenience path not established",
                    "A viable verified trade-in or other convenience-specific path is required before ReValue AI can recommend a convenience-led next action.",
                )
            ],
            alternatives=alternatives,
            evidence_used=evidence_used,
            evidence_missing=sorted(set(evidence_missing + ["verified_convenience_path"])),
            rules_triggered=["convenience_review_required_without_viable_trade_in"],
            caveats=caveats,
        )

    resale_advantage = market_low - float(trade_best or 0)
    if priority in {"balanced", "maximize_value"} and (
        priority == "maximize_value" or grade in HEALTHY_EXTERIOR_GRADES or resale_advantage >= RESELL_ADVANTAGE_MIN_RM
    ):
        alternatives["resell"]["status"] = "recommended"
        reasons = [
            _reason(
                "valid_resale_market_range",
                "Valid resale market range",
                f"The current evidence-backed resale range is {_money(market_low)} to {_money(market_high)}.",
            )
        ]
        if grade in HEALTHY_EXTERIOR_GRADES and not has_repair_relevant_damage:
            reasons.append(
                _reason(
                    "good_exterior_condition",
                    "Good exterior condition",
                    f"Exterior Grade {grade} indicates no confirmed repair-relevant cosmetic damage.",
                )
            )
        if not trade_competitive:
            if not trade_in["available"]:
                reasons.append(
                    _reason(
                        "no_verified_trade_in_benchmark",
                        "No verified trade-in benchmark",
                        "No verified trade-in benchmark is currently available for comparison.",
                    )
                )
            else:
                reasons.append(
                    _reason(
                        "trade_in_below_resale_range",
                        "Trade-in below resale range",
                        "The verified trade-in benchmark is below the current evidence-backed resale range.",
                    )
                )
        return _finish(
            payload=payload,
            action="resell",
            strength="clear" if grade in HEALTHY_EXTERIOR_GRADES and not has_repair_relevant_damage else "moderate",
            headline="Reselling currently offers the strongest evidence-backed value opportunity.",
            reasons=reasons,
            alternatives=alternatives,
            evidence_used=evidence_used,
            evidence_missing=sorted(set(evidence_missing)),
            rules_triggered=["resell_market_value_advantage"],
            caveats=caveats,
        )

    return _finish(
        payload=payload,
        action=None,
        strength="review_required",
        headline="The available evidence does not justify one clear next action.",
        reasons=[
            _reason(
                "mixed_or_incomplete_option_evidence",
                "Mixed or incomplete evidence",
                "The current evidence supports several possible next steps but does not justify a primary recommendation.",
            )
        ],
        alternatives=alternatives,
        evidence_used=evidence_used,
        evidence_missing=sorted(set(evidence_missing)),
        rules_triggered=["review_required_mixed_evidence"],
        caveats=caveats,
    )
