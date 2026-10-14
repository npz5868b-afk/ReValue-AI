from __future__ import annotations

import copy
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from backend.src.decision_engine import (
    DecisionInputError,
    REPAIR_COST_MAX_RATIO,
    REPAIR_COST_TO_MARKET_MAX_RATIO,
    TRADE_IN_COMPETITIVE_RATIO,
    TRADE_IN_MIN_VIABLE_RATIO,
    normalize_market_range,
    normalize_repair_evidence,
    normalize_trade_in_evidence,
    recommend_next_action,
)
from backend.src.local_valuation import local_fallback_valuation
from backend.src.server import ReValueHandler


def assert_equal(actual, expected, message: str) -> None:
    if actual != expected:
        raise AssertionError(f"{message}: expected {expected!r}, got {actual!r}")


def assert_decision_invariants(result: dict, message: str = "decision invariants") -> None:
    action = result.get("recommended_action")
    strength = result.get("decision_strength")
    status = result.get("status")
    if action is None:
        assert_equal(strength, "review_required", f"{message} strength without action")
        assert_equal(status, "review_required", f"{message} status without action")
    else:
        assert action in {"repair", "resell", "trade_in", "keep"}, f"{message}: unsupported action {action!r}"
        assert strength in {"clear", "moderate"}, f"{message}: invalid action strength {strength!r}"
        assert_equal(status, "ok", f"{message} status with action")


def base_payload(**overrides) -> dict:
    payload = {
        "confirmed_device": {
            "brand": "Samsung",
            "model": "Galaxy S24 Ultra",
            "storage": "256GB",
        },
        "exterior_grade": "A",
        "detected_damage": [],
        "valuation": {
            "status": "estimated_market_value",
            "currency": "MYR",
            "value_low": 2000,
            "value_high": 2400,
            "midpoint": 2200,
            "exact_model_count": 11,
            "condition_matched_count": 7,
            "evidence_status": "local_fixture",
            "trade_in_benchmark": [],
            "repair_references": [],
        },
        "user_priority": "balanced",
    }
    payload.update(overrides)
    return payload


def test_clean_device_balanced_recommends_resell() -> None:
    result = recommend_next_action(base_payload())
    assert_equal(result["recommended_action"], "resell", "clean balanced action")
    assert result["decision_strength"] in {"clear", "moderate"}
    assert "resell_market_value_advantage" in result["rules_triggered"]
    assert not any("internal" in reason["code"] for reason in result["reasons"])
    assert "No verified trade-in benchmark is currently available for comparison." in json.dumps(result)


def test_competitive_trade_in_recommended() -> None:
    payload = base_payload(
        valuation={
            **base_payload()["valuation"],
            "value_low": 2000,
            "value_high": 2500,
            "trade_in_benchmark": [{"price": 1850, "price_type": "trade_in_offer"}],
        }
    )
    result = recommend_next_action(payload)
    assert_equal(result["recommended_action"], "trade_in", "competitive trade-in action")
    assert "trade_in_competitive_ratio_met" in result["rules_triggered"]


def test_maximize_value_prefers_resell_when_resale_stronger() -> None:
    payload = base_payload(
        user_priority="maximize_value",
        valuation={
            **base_payload()["valuation"],
            "value_low": 2000,
            "value_high": 2500,
            "trade_in_benchmark": [{"price": 1400, "price_type": "trade_in_offer"}],
        },
    )
    result = recommend_next_action(payload)
    assert_equal(result["recommended_action"], "resell", "maximize value action")
    assert "resell_market_value_advantage" in result["rules_triggered"]


def test_repair_relevant_damage_with_economical_repair() -> None:
    payload = base_payload(
        exterior_grade="C",
        detected_damage=[{"damage_type": "screen_crack", "verification": "confirmed", "grade_affecting": True}],
        valuation={
            **base_payload()["valuation"],
            "value_low": 1000,
            "value_high": 1000,
            "midpoint": 1000,
            "repair_references": [{"low": 100, "high": 300}],
        },
    )
    result = recommend_next_action(payload)
    assert_equal(result["recommended_action"], "repair", "economical repair action")
    assert_equal(result["decision_strength"], "moderate", "repair strength")
    assert "repair_economics_within_threshold" in result["rules_triggered"]
    assert "profit" not in json.dumps(result).lower()


def test_expensive_repair_does_not_blindly_recommend_repair() -> None:
    payload = base_payload(
        exterior_grade="C-",
        detected_damage=[{"damage_type": "back_glass_crack", "verification": "confirmed", "grade_affecting": True}],
        valuation={
            **base_payload()["valuation"],
            "value_low": 1000,
            "value_high": 1200,
            "midpoint": 1100,
            "repair_references": [{"low": 450, "high": 650}],
        },
    )
    result = recommend_next_action(payload)
    assert result["recommended_action"] in {"resell", "trade_in", None}
    assert result["recommended_action"] != "repair"


def test_extend_device_life_recommends_keep_for_healthy_device() -> None:
    result = recommend_next_action(base_payload(user_priority="extend_device_life", exterior_grade="B"))
    assert_equal(result["recommended_action"], "keep", "extend life action")
    assert "extend_device_life_healthy_keep" in result["rules_triggered"]


def test_minimize_spend_recommends_keep_for_healthy_device() -> None:
    result = recommend_next_action(base_payload(user_priority="minimize_spend", exterior_grade="A"))
    assert_equal(result["recommended_action"], "keep", "minimize spend action")
    assert "minimize_spend_healthy_keep" in result["rules_triggered"]


def test_insufficient_market_evidence_returns_review_required() -> None:
    payload = base_payload(valuation={"status": "insufficient_market_evidence", "value_low": None, "value_high": None})
    result = recommend_next_action(payload)
    assert_equal(result["recommended_action"], None, "insufficient action")
    assert_equal(result["decision_strength"], "review_required", "insufficient strength")
    assert "reliable_market_range" in result["evidence_missing"]


def test_unverified_internal_grade_does_not_change_material_result() -> None:
    with_b_plus = recommend_next_action(base_payload(internal_grade="B+"))
    with_c = recommend_next_action(base_payload(internal_grade="C"))
    for key in ["recommended_action", "decision_strength", "headline", "rules_triggered"]:
        assert_equal(with_b_plus[key], with_c[key], f"internal grade must not alter {key}")
    assert_equal(with_b_plus["evidence_used"]["internal_grade_used"], False, "internal grade ignored")


def test_trade_in_shape_normalisation() -> None:
    valuation = {"trade_in_benchmark": [{"price": 1900}, {"trade_in_price_rm": 1800}, {"price": "bad"}, {}]}
    normalized = normalize_trade_in_evidence(valuation)
    assert_equal(normalized["best"], 1900.0, "trade-in best")
    assert_equal(normalized["count"], 2, "trade-in count")


def test_repair_shape_normalisation() -> None:
    valuation = {"repair_references": [{"price": 450}, {"low": 400, "high": 600}, {"repair_cost_rm": 500}, {}]}
    normalized = normalize_repair_evidence(valuation)
    assert_equal(normalized["low"], 400.0, "repair low")
    assert_equal(normalized["high"], 600.0, "repair high")
    assert_equal(normalized["midpoint"], 500.0, "repair midpoint")


def test_input_immutability() -> None:
    payload = base_payload(internal_grade="B+")
    original = copy.deepcopy(payload)
    recommend_next_action(payload)
    assert_equal(payload, original, "decision engine must not mutate input")


def test_determinism() -> None:
    payload = base_payload()
    first = recommend_next_action(payload)
    for _ in range(5):
        assert_equal(recommend_next_action(payload), first, "identical input must return identical output")


def test_trade_in_threshold_boundaries() -> None:
    low = 1000
    exact = low * TRADE_IN_COMPETITIVE_RATIO
    below = exact - 1
    above = exact + 1
    base_valuation = {**base_payload()["valuation"], "value_low": low, "value_high": 1200, "midpoint": 1100}

    below_result = recommend_next_action(base_payload(valuation={**base_valuation, "trade_in_benchmark": [{"price": below}]}))
    exact_result = recommend_next_action(base_payload(valuation={**base_valuation, "trade_in_benchmark": [{"price": exact}]}))
    above_result = recommend_next_action(base_payload(valuation={**base_valuation, "trade_in_benchmark": [{"price": above}]}))

    assert below_result["recommended_action"] != "trade_in"
    assert_equal(exact_result["recommended_action"], "trade_in", "trade-in exact boundary")
    assert_equal(above_result["recommended_action"], "trade_in", "trade-in above boundary")


def test_repair_threshold_boundaries() -> None:
    midpoint = 1000
    assert_equal(REPAIR_COST_MAX_RATIO, REPAIR_COST_TO_MARKET_MAX_RATIO, "legacy repair threshold alias")
    threshold_cost = midpoint * REPAIR_COST_TO_MARKET_MAX_RATIO
    base_valuation = {**base_payload()["valuation"], "value_low": midpoint, "value_high": midpoint, "midpoint": midpoint}
    common = {
        "exterior_grade": "C",
        "detected_damage": [{"damage_type": "screen_crack", "verification": "confirmed", "grade_affecting": True}],
    }

    below = recommend_next_action(
        base_payload(**common, valuation={**base_valuation, "repair_references": [{"price": threshold_cost - 1}]})
    )
    exact = recommend_next_action(
        base_payload(**common, valuation={**base_valuation, "repair_references": [{"price": threshold_cost}]})
    )
    above = recommend_next_action(
        base_payload(**common, valuation={**base_valuation, "repair_references": [{"price": threshold_cost + 1}]})
    )

    assert_equal(below["recommended_action"], "repair", "repair below boundary")
    assert_equal(exact["recommended_action"], "repair", "repair exact boundary")
    assert above["recommended_action"] != "repair"


def post_json(port: int, path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return json.loads(urllib.request.urlopen(request, timeout=10).read().decode("utf-8"))


def post_raw(port: int, path: str, raw: str) -> tuple[int, dict]:
    return post_bytes(port, path, raw.encode("utf-8"))


def post_bytes(port: int, path: str, raw: bytes) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=raw,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def test_api_endpoint() -> None:
    server = HTTPServer(("127.0.0.1", 0), ReValueHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = post_json(port, "/decision-support", base_payload())
        assert_equal(body["status"], "ok", "API status")
        assert_equal(body["recommended_action"], "resell", "API recommendation")
        assert_equal(body["engine_version"], "decision_support_v1", "API engine version")
        health = urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=10).status
        assert_equal(health, 200, "existing endpoint health")
    finally:
        server.shutdown()


def test_no_synthetic_dealer_band_dependency() -> None:
    source = (ROOT / "src" / "decision_engine.py").read_text(encoding="utf-8")
    forbidden = ["dealerBands", "dealerPayoutFromAssessment", "shopOfferData"]
    for token in forbidden:
        assert token not in source


def test_absurd_trade_in_cannot_win_convenience() -> None:
    payload = base_payload(
        user_priority="convenience",
        valuation={**base_payload()["valuation"], "value_low": 2000, "value_high": 2400, "midpoint": 2200, "trade_in_benchmark": [{"price": 100}]},
    )
    result = recommend_next_action(payload)
    assert result["recommended_action"] != "trade_in"
    assert_equal(result["alternatives"]["trade_in"]["status"], "available_but_weak", "weak trade-in status")
    assert "materially below" in json.dumps(result).lower()
    assert_decision_invariants(result, "absurd convenience trade-in")


def test_trade_in_minimum_viability_boundaries() -> None:
    low = 2000
    exact = low * TRADE_IN_MIN_VIABLE_RATIO
    below = exact - 1
    above = exact + 1
    valuation = {**base_payload()["valuation"], "value_low": low, "value_high": 2400, "midpoint": 2200}

    below_result = recommend_next_action(base_payload(user_priority="convenience", valuation={**valuation, "trade_in_benchmark": [{"price": below}]}))
    exact_result = recommend_next_action(base_payload(user_priority="convenience", valuation={**valuation, "trade_in_benchmark": [{"price": exact}]}))
    above_result = recommend_next_action(base_payload(user_priority="convenience", valuation={**valuation, "trade_in_benchmark": [{"price": above}]}))

    assert below_result["recommended_action"] != "trade_in"
    assert_equal(exact_result["recommended_action"], "trade_in", "trade-in min viability exact boundary")
    assert_equal(above_result["recommended_action"], "trade_in", "trade-in min viability above boundary")


def test_damaged_without_repair_evidence_requires_review() -> None:
    for grade in ["C", "C-"]:
        payload = base_payload(
            exterior_grade=grade,
            detected_damage=[{"damage_type": "generic_crack", "verification": "confirmed", "grade_affecting": True}],
            valuation={**base_payload()["valuation"], "repair_references": [], "repair_cost_references": []},
        )
        result = recommend_next_action(payload)
        assert_equal(result["recommended_action"], None, f"{grade} damaged action without repair evidence")
        assert_equal(result["decision_strength"], "review_required", f"{grade} damaged strength without repair evidence")
        assert "repair_cost_reference" in result["evidence_missing"]
        assert "repair-cost evidence is required" in json.dumps(result).lower()
        assert "strongest evidence-backed value opportunity" not in json.dumps(result).lower()


def test_market_midpoint_normalization_cases() -> None:
    cases = [
        ({}, 1100.0, True),
        ({"midpoint": 1050}, 1050.0, False),
        ({"midpoint": 999}, 1100.0, True),
        ({"midpoint": 1201}, 1100.0, True),
        ({"midpoint": -1}, 1100.0, True),
        ({"midpoint": 0}, 1100.0, True),
        ({"midpoint": 1000}, 1000.0, False),
        ({"midpoint": 1200}, 1200.0, False),
    ]
    for extra, expected_midpoint, normalized in cases:
        result = normalize_market_range({"value_low": 1000, "value_high": 1200, **extra})
        assert_equal(result["available"], True, f"market available for {extra}")
        assert_equal(result["midpoint"], expected_midpoint, f"midpoint for {extra}")
        assert_equal(result["midpoint_normalized"], normalized, f"normalization flag for {extra}")


def test_malicious_midpoint_does_not_corrupt_repair_decision() -> None:
    common = {
        "exterior_grade": "C",
        "detected_damage": [{"damage_type": "screen_crack", "verification": "confirmed", "grade_affecting": True}],
    }
    safe = recommend_next_action(
        base_payload(**common, valuation={**base_payload()["valuation"], "value_low": 1000, "value_high": 1200, "midpoint": 1100, "repair_references": [{"price": 300}]})
    )
    malicious = recommend_next_action(
        base_payload(**common, valuation={**base_payload()["valuation"], "value_low": 1000, "value_high": 1200, "midpoint": 100000, "repair_references": [{"price": 300}]})
    )
    assert_equal(safe["recommended_action"], malicious["recommended_action"], "malicious midpoint must not alter repair action")
    assert_equal(safe["decision_strength"], malicious["decision_strength"], "malicious midpoint must not alter repair strength")
    assert_equal(malicious["evidence_used"]["market_range"]["midpoint"], 1100.0, "malicious midpoint recomputed")


def test_missing_or_unknown_exterior_grade_requires_review() -> None:
    for grade in [None, "", "Z"]:
        result = recommend_next_action(base_payload(exterior_grade=grade))
        assert_equal(result["recommended_action"], None, f"missing/unknown grade action {grade!r}")
        assert_equal(result["decision_strength"], "review_required", f"missing/unknown grade strength {grade!r}")
        assert "exterior_condition_assessment" in result["evidence_missing"]
    normalized = recommend_next_action(base_payload(exterior_grade=" a "))
    assert_equal(normalized["recommended_action"], "resell", "case/whitespace grade normalization")


def test_user_priority_validation() -> None:
    assert_equal(recommend_next_action(base_payload(user_priority=None))["evidence_used"]["user_priority"], "balanced", "missing priority default")
    for priority in ["balanced", "maximize_value", "convenience", "minimize_spend", "extend_device_life", "BALANCED"]:
        result = recommend_next_action(base_payload(user_priority=priority))
        assert result["evidence_used"]["user_priority"] in {"balanced", priority.lower()}
    try:
        recommend_next_action(base_payload(user_priority="hello"))
    except DecisionInputError as error:
        assert_equal(error.error, "invalid_user_priority", "invalid priority error code")
        assert "balanced" in error.extra["supported_priorities"]
    else:
        raise AssertionError("invalid user priority must be rejected")


def test_endpoint_rejects_non_object_json() -> None:
    server = HTTPServer(("127.0.0.1", 0), ReValueHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for raw in ['"hello"', "123", "[]", "null"]:
            status, body = post_raw(port, "/decision-support", raw)
            assert_equal(status, 400, f"non-object status for {raw}")
            assert_equal(body["status"], "error", f"non-object body status for {raw}")
            assert_equal(body["error"], "invalid_request_body", f"non-object body error for {raw}")
        status, body = post_raw(port, "/decision-support", "{}")
        assert_equal(status, 200, "empty object remains valid JSON object")
        assert_equal(body["status"], "review_required", "empty object review required")
    finally:
        server.shutdown()


def test_endpoint_rejects_invalid_priority() -> None:
    server = HTTPServer(("127.0.0.1", 0), ReValueHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = post_raw(port, "/decision-support", json.dumps(base_payload(user_priority="make_me_rich")))
        assert_equal(status, 400, "invalid priority HTTP status")
        assert_equal(body["error"], "invalid_user_priority", "invalid priority API error")
        assert "supported_priorities" in body
    finally:
        server.shutdown()


def test_trade_in_wording_distinguishes_absent_below_and_competitive() -> None:
    absent = recommend_next_action(base_payload())
    absent_text = json.dumps(absent).lower()
    assert "no verified trade-in benchmark is currently available for comparison" in absent_text
    assert "beats the evidence-backed resale opportunity" not in absent_text

    below = recommend_next_action(base_payload(valuation={**base_payload()["valuation"], "trade_in_benchmark": [{"price": 1000}]}))
    assert "below the current evidence-backed resale range" in json.dumps(below).lower()

    competitive = recommend_next_action(base_payload(valuation={**base_payload()["valuation"], "trade_in_benchmark": [{"price": 1900}]}))
    assert_equal(competitive["recommended_action"], "trade_in", "competitive wording action")
    assert "competitive with the lower end" in json.dumps(competitive).lower()


def test_status_invariants_across_representative_cases() -> None:
    cases = [
        base_payload(),
        base_payload(user_priority="maximize_value", valuation={**base_payload()["valuation"], "trade_in_benchmark": [{"price": 1000}]}),
        base_payload(user_priority="convenience", valuation={**base_payload()["valuation"], "trade_in_benchmark": [{"price": 1500}]}),
        base_payload(user_priority="extend_device_life"),
        base_payload(exterior_grade=None),
        base_payload(valuation={"status": "insufficient_market_evidence", "value_low": None, "value_high": None}),
        base_payload(
            exterior_grade="C",
            detected_damage=[{"damage_type": "screen_crack", "verification": "confirmed", "grade_affecting": True}],
            valuation={**base_payload()["valuation"], "repair_references": [{"price": 150}]},
        ),
    ]
    for index, payload in enumerate(cases):
        assert_decision_invariants(recommend_next_action(payload), f"case {index}")


def test_adversarial_numeric_inputs_do_not_crash_or_corrupt() -> None:
    invalid_markets = [
        {"value_low": -1, "value_high": 1200},
        {"value_low": 0, "value_high": 1200},
        {"value_low": 1200, "value_high": 1000},
        {"value_low": "bad", "value_high": 1200},
        {"value_low": 1000, "value_high": "NaN"},
        {"value_low": 1000, "value_high": float("inf")},
    ]
    for valuation in invalid_markets:
        result = recommend_next_action(base_payload(valuation={"status": "estimated_market_value", **valuation}))
        assert_equal(result["recommended_action"], None, f"invalid market action {valuation}")
        assert "reliable_market_range" in result["evidence_missing"]

    optional_bad = recommend_next_action(
        base_payload(
            valuation={
                **base_payload()["valuation"],
                "trade_in_benchmark": [{"price": -1}, {"price": 0}, {"price": "bad"}, {"price": None}],
                "repair_references": [{"price": -5}, {"price": 0}, {"price": "bad"}, {"price": None}],
            }
        )
    )
    assert_equal(optional_bad["recommended_action"], "resell", "malformed optional evidence ignored safely")
    assert_equal(optional_bad["evidence_used"]["trade_in_benchmark"]["available"], False, "bad trade evidence unavailable")
    assert_equal(optional_bad["evidence_used"]["repair_evidence"]["available"], False, "bad repair evidence unavailable")


def test_allowed_actions_and_no_confidence_percentage() -> None:
    result = recommend_next_action(base_payload())
    assert_decision_invariants(result, "allowed action")
    response_text = json.dumps(result).lower()
    assert "confidence" not in response_text
    assert "dealerbands" not in response_text


def test_s24_clean_regression_gate() -> None:
    result = recommend_next_action(
        base_payload(
            valuation={
                **base_payload()["valuation"],
                "value_low": 2250,
                "value_high": 2600,
                "midpoint": 2425,
                "trade_in_benchmark": [],
                "repair_references": [],
            },
            internal_grade="B+",
            user_priority="balanced",
        )
    )
    assert_equal(result["recommended_action"], "resell", "S24 clean action")
    assert_equal(result["decision_strength"], "clear", "S24 clean strength")
    result_text = json.dumps(result).lower()
    assert "no verified trade-in benchmark is currently available for comparison" in result_text
    assert "fake" not in result_text
    assert "repair cost" not in result_text.replace("no repair-relevant evidence is currently available.", "")
    assert_equal(result["evidence_used"]["internal_grade_used"], False, "S24 clean ignores unverified internal B+")


def test_maximize_value_superior_trade_in_beats_resell() -> None:
    result = recommend_next_action(
        base_payload(
            user_priority="maximize_value",
            valuation={**base_payload()["valuation"], "value_low": 2000, "value_high": 2400, "midpoint": 2200, "trade_in_benchmark": [{"price": 3000}]},
        )
    )
    assert_equal(result["recommended_action"], "trade_in", "superior trade-in action")
    assert_equal(result["decision_strength"], "clear", "superior trade-in strength")
    assert "trade_in_exceeds_resale_range" in result["rules_triggered"]
    assert "exceeds the current evidence-backed resale range" in json.dumps(result).lower()


def test_maximize_value_damaged_superior_trade_in_does_not_claim_repair_optimal() -> None:
    result = recommend_next_action(
        base_payload(
            user_priority="maximize_value",
            exterior_grade="C",
            detected_damage=[{"damage_type": "screen_crack", "verification": "confirmed", "grade_affecting": True}],
            valuation={**base_payload()["valuation"], "value_low": 2000, "value_high": 2400, "midpoint": 2200, "trade_in_benchmark": [{"price": 3000}], "repair_references": [{"price": 100}]},
        )
    )
    assert_equal(result["recommended_action"], "trade_in", "damaged superior trade-in action")
    assert_equal(result["decision_strength"], "moderate", "damaged superior trade-in strength")
    assert "repair_upside_unevaluated" in result["rules_triggered"]
    assert "post-repair market value evidence is not available" in json.dumps(result).lower()
    assert "maximizes" not in json.dumps(result).lower()


def test_maximize_value_low_trade_in_still_resells_for_healthy_device() -> None:
    result = recommend_next_action(
        base_payload(
            user_priority="maximize_value",
            valuation={**base_payload()["valuation"], "value_low": 2000, "value_high": 2400, "midpoint": 2200, "trade_in_benchmark": [{"price": 1000}]},
        )
    )
    assert_equal(result["recommended_action"], "resell", "low trade-in maximize action")


def test_contradictory_exterior_evidence_requires_review() -> None:
    contradictory_cases = [
        ("A", {"damage_type": "screen_crack", "verification": "confirmed", "grade_affecting": True}),
        ("A", {"damage_type": "back_glass_crack", "verification": "confirmed", "grade_affecting": True}),
        ("A", {"damage_type": "scratch", "verification": "confirmed", "grade_affecting": True}),
        ("B", {"damage_type": "screen_crack", "verification": "confirmed", "grade_affecting": True}),
    ]
    for grade, damage in contradictory_cases:
        result = recommend_next_action(
            base_payload(exterior_grade=grade, detected_damage=[damage], valuation={**base_payload()["valuation"], "repair_references": [{"price": 100}]})
        )
        assert_equal(result["recommended_action"], None, f"contradictory action {grade}/{damage['damage_type']}")
        assert_equal(result["decision_strength"], "review_required", f"contradictory strength {grade}/{damage['damage_type']}")
        assert "inconsistent_exterior_evidence" in result["rules_triggered"]

    grade_b_scratch = recommend_next_action(
        base_payload(
            exterior_grade="B",
            detected_damage=[{"damage_type": "scratch", "verification": "confirmed", "grade_affecting": True}],
        )
    )
    assert grade_b_scratch["recommended_action"] is not None
    assert "inconsistent_exterior_evidence" not in grade_b_scratch["rules_triggered"]

    grade_c_without_raw_damage = recommend_next_action(base_payload(exterior_grade="C", detected_damage=[]))
    assert "inconsistent_exterior_evidence" not in grade_c_without_raw_damage["rules_triggered"]


def test_boolean_values_are_not_money() -> None:
    bool_market = recommend_next_action(base_payload(valuation={"status": "estimated_market_value", "value_low": True, "value_high": True, "midpoint": True}))
    assert_equal(bool_market["recommended_action"], None, "boolean market action")
    assert "reliable_market_range" in bool_market["evidence_missing"]

    bool_optional = recommend_next_action(
        base_payload(
            valuation={
                **base_payload()["valuation"],
                "trade_in_benchmark": [{"price": True}, {"price": False}],
                "repair_references": [{"price": True}, {"price": False}],
            }
        )
    )
    assert_equal(bool_optional["recommended_action"], "resell", "boolean optional evidence ignored")
    assert_equal(bool_optional["evidence_used"]["trade_in_benchmark"]["available"], False, "boolean trade-in ignored")
    assert_equal(bool_optional["evidence_used"]["repair_evidence"]["available"], False, "boolean repair ignored")

    bool_repair_damaged = recommend_next_action(
        base_payload(
            exterior_grade="C",
            detected_damage=[{"damage_type": "screen_crack", "verification": "confirmed", "grade_affecting": True}],
            valuation={**base_payload()["valuation"], "repair_references": [True, False]},
        )
    )
    assert_equal(bool_repair_damaged["recommended_action"], None, "boolean repair damaged action")
    assert "repair_cost_reference" in bool_repair_damaged["evidence_missing"]


def test_endpoint_rejects_malformed_json() -> None:
    server = HTTPServer(("127.0.0.1", 0), ReValueHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for raw in [b"{bad", b'{"a":', b"\xff\xfe"]:
            status, body = post_bytes(port, "/decision-support", raw)
            assert_equal(status, 400, f"malformed JSON status for {raw!r}")
            assert_equal(body["status"], "error", f"malformed JSON body status for {raw!r}")
            assert_equal(body["error"], "invalid_json", f"malformed JSON body error for {raw!r}")
        body = post_json(port, "/decision-support", base_payload())
        assert_equal(body["status"], "ok", "normal POST still works after malformed JSON")
    finally:
        server.shutdown()


def test_overflow_safe_midpoint_and_money_sanity_limit() -> None:
    huge = recommend_next_action(base_payload(valuation={"status": "estimated_market_value", "value_low": 1e308, "value_high": 1e308}))
    assert_equal(huge["recommended_action"], None, "huge market action")
    assert "reliable_market_range" in huge["evidence_missing"]

    normalized = normalize_market_range({"status": "estimated_market_value", "value_low": 2000, "value_high": 2400, "midpoint": 1e308})
    assert_equal(normalized["available"], True, "huge midpoint ignored for normal market")
    assert_equal(normalized["midpoint"], 2200.0, "huge midpoint recomputed")

    optional_huge = recommend_next_action(
        base_payload(
            valuation={
                **base_payload()["valuation"],
                "trade_in_benchmark": [{"price": 2_000_000}],
                "repair_references": [{"price": 2_000_000}],
            }
        )
    )
    assert_equal(optional_huge["evidence_used"]["trade_in_benchmark"]["available"], False, "huge trade-in ignored")
    assert_equal(optional_huge["evidence_used"]["repair_evidence"]["available"], False, "huge repair ignored")


def test_trade_in_wording_below_near_inside_and_above_range() -> None:
    below = recommend_next_action(base_payload(valuation={**base_payload()["valuation"], "trade_in_benchmark": [{"price": 1000}]}))
    assert "below the current evidence-backed resale range" in json.dumps(below).lower()

    near = recommend_next_action(base_payload(valuation={**base_payload()["valuation"], "trade_in_benchmark": [{"price": 1900}]}))
    near_text = json.dumps(near).lower()
    assert "competitive with the lower end" in near_text
    assert "exceeds the current evidence-backed resale range" not in near_text

    inside = recommend_next_action(base_payload(valuation={**base_payload()["valuation"], "trade_in_benchmark": [{"price": 2200}]}))
    assert "within the current evidence-backed resale range" in json.dumps(inside).lower()

    above = recommend_next_action(base_payload(valuation={**base_payload()["valuation"], "trade_in_benchmark": [{"price": 3000}]}))
    above_text = json.dumps(above).lower()
    assert_equal(above["recommended_action"], "trade_in", "above-range trade-in action")
    assert "exceeds the current evidence-backed resale range" in above_text
    assert "close to the market floor" not in above_text


def test_confirmed_device_identity_required() -> None:
    cases = [
        {},
        {"confirmed_device": {}},
        {"confirmed_device": {"brand": "Samsung"}},
        {"confirmed_device": {"brand": "Samsung", "model": "Galaxy S24 Ultra"}},
    ]
    for override in cases:
        payload = base_payload()
        if "confirmed_device" in override:
            payload["confirmed_device"] = override["confirmed_device"]
        else:
            payload.pop("confirmed_device", None)
        result = recommend_next_action(payload)
        assert_equal(result["recommended_action"], None, f"confirmed device required for {override}")
        assert "confirmed_device_identity" in result["evidence_missing"]
        assert "confirmed_device_required" in result["rules_triggered"]

    assert_equal(recommend_next_action(base_payload())["recommended_action"], "resell", "valid confirmed device continues")


def test_structured_damage_verification_must_be_confirmed() -> None:
    confirmed = recommend_next_action(
        base_payload(
            exterior_grade="C",
            detected_damage=[{"damage_type": "screen_crack", "verification": "confirmed", "grade_affecting": True}],
        )
    )
    assert_equal(confirmed["evidence_used"]["detected_damage_considered"], True, "confirmed damage considered")
    assert_equal(confirmed["evidence_used"]["repair_relevant_damage_considered"], True, "confirmed repair damage considered")

    for verification in ["unverified", "rejected", None]:
        item = {"damage_type": "screen_crack", "grade_affecting": True}
        if verification is not None:
            item["verification"] = verification
        result = recommend_next_action(base_payload(exterior_grade="A", detected_damage=[item]))
        assert_equal(result["recommended_action"], "resell", f"{verification} structured damage not authoritative")
        assert_equal(result["evidence_used"]["detected_damage_considered"], False, f"{verification} structured damage ignored")
        assert "inconsistent_exterior_evidence" not in result["rules_triggered"]

    grade_c_only = recommend_next_action(base_payload(exterior_grade="C", detected_damage=[]))
    assert_equal(grade_c_only["evidence_used"]["repair_relevant_damage_considered"], True, "grade C remains damage evidence")


def test_real_s24_local_fallback_regression_gate() -> None:
    confirmed_device = {"brand": "Samsung", "model": "Galaxy S24 Ultra", "storage": "256GB"}
    valuation = local_fallback_valuation(confirmed_device=confirmed_device, exterior_grade="A")
    result = recommend_next_action(
        base_payload(
            confirmed_device=confirmed_device,
            exterior_grade="A",
            detected_damage=[],
            internal_grade="B+",
            valuation=valuation,
            user_priority="balanced",
        )
    )
    assert_equal(result["recommended_action"], "resell", "real S24 fallback action")
    assert_equal(result["decision_strength"], "clear", "real S24 fallback strength")
    assert result["evidence_used"]["market_range"]["value_low"] is not None
    assert result["evidence_used"]["market_range"]["value_high"] is not None
    assert "no verified trade-in benchmark is currently available for comparison" in json.dumps(result).lower()
    assert_equal(result["evidence_used"]["internal_grade_used"], False, "real S24 ignores unverified internal B+")


def test_minimize_spend_damaged_device_does_not_fall_through_to_resell() -> None:
    for grade in ["C", "C-"]:
        for repair_price in [100, 600]:
            result = recommend_next_action(
                base_payload(
                    user_priority="minimize_spend",
                    exterior_grade=grade,
                    detected_damage=[{"damage_type": "screen_crack", "verification": "confirmed", "grade_affecting": True}],
                    valuation={**base_payload()["valuation"], "repair_references": [{"price": repair_price}]},
                )
            )
            assert_equal(result["recommended_action"], None, f"{grade} repair {repair_price} minimize_spend action")
            assert_equal(result["decision_strength"], "review_required", f"{grade} repair {repair_price} minimize_spend strength")
            assert "minimize_spend_review_required_for_damaged_device" in result["rules_triggered"]
            assert "resell_market_value_advantage" not in result["rules_triggered"]

    assert_equal(recommend_next_action(base_payload(user_priority="minimize_spend", exterior_grade="A"))["recommended_action"], "keep", "healthy A minimize_spend keep")
    assert_equal(recommend_next_action(base_payload(user_priority="minimize_spend", exterior_grade="B"))["recommended_action"], "keep", "healthy B minimize_spend keep")


def test_priority_matrix_respects_selected_objective() -> None:
    healthy_expected = {
        "balanced": ("resell", "resell_market_value_advantage"),
        "maximize_value": ("resell", "resell_market_value_advantage"),
        "convenience": (None, "convenience_review_required_without_viable_trade_in"),
        "minimize_spend": ("keep", "minimize_spend_healthy_keep"),
        "extend_device_life": ("keep", "extend_device_life_healthy_keep"),
    }
    for priority, (action, rule) in healthy_expected.items():
        result = recommend_next_action(base_payload(user_priority=priority, exterior_grade="A", detected_damage=[]))
        assert_equal(result["recommended_action"], action, f"healthy priority {priority}")
        assert rule in result["rules_triggered"]
        if priority == "convenience":
            assert "strongest evidence-backed value opportunity" not in json.dumps(result).lower()

    damaged_common = {
        "exterior_grade": "C",
        "detected_damage": [{"damage_type": "screen_crack", "verification": "confirmed", "grade_affecting": True}],
    }
    economical_expected = {
        "balanced": ("repair", "repair_economics_within_threshold"),
        "maximize_value": (None, "maximize_value_requires_post_repair_value_for_repair_choice"),
        "convenience": (None, "convenience_review_required_without_viable_trade_in"),
        "minimize_spend": (None, "minimize_spend_review_required_for_damaged_device"),
        "extend_device_life": ("repair", "repair_economics_within_threshold"),
    }
    for priority, (action, rule) in economical_expected.items():
        result = recommend_next_action(
            base_payload(
                user_priority=priority,
                **damaged_common,
                valuation={**base_payload()["valuation"], "repair_references": [{"price": 100}]},
            )
        )
        assert_equal(result["recommended_action"], action, f"damaged economical priority {priority}")
        assert rule in result["rules_triggered"]

    expensive_expected = {
        "balanced": ("resell", "resell_market_value_advantage"),
        "maximize_value": (None, "maximize_value_requires_post_repair_value_for_repair_choice"),
        "convenience": (None, "convenience_review_required_without_viable_trade_in"),
        "minimize_spend": (None, "minimize_spend_review_required_for_damaged_device"),
        "extend_device_life": (None, "extend_device_life_review_required_without_supported_repair"),
    }
    for priority, (action, rule) in expensive_expected.items():
        result = recommend_next_action(
            base_payload(
                user_priority=priority,
                **damaged_common,
                valuation={**base_payload()["valuation"], "repair_references": [{"price": 600}]},
            )
        )
        assert_equal(result["recommended_action"], action, f"damaged expensive priority {priority}")
        assert rule in result["rules_triggered"]


def test_confirmed_device_identity_requires_valid_strings() -> None:
    valid = recommend_next_action(base_payload(confirmed_device={"brand": " Samsung ", "model": " Galaxy S24 Ultra ", "storage": " 256GB "}))
    assert_equal(valid["recommended_action"], "resell", "valid string confirmed device")

    invalid_devices = [
        {"brand": True, "model": True, "storage": True},
        {"brand": 123, "model": 456, "storage": 789},
        {"brand": "Samsung", "model": "Galaxy S24 Ultra", "storage": {"gb": 256}},
        {"brand": "Samsung", "model": "Galaxy S24 Ultra", "storage": ["256GB"]},
        {"brand": "", "model": "Galaxy S24 Ultra", "storage": "256GB"},
        {"brand": "Samsung", "model": "   ", "storage": "256GB"},
        {"brand": "Samsung", "model": "Galaxy S24 Ultra", "storage": None},
    ]
    for device in invalid_devices:
        result = recommend_next_action(base_payload(confirmed_device=device))
        assert_equal(result["recommended_action"], None, f"invalid confirmed device {device}")
        assert "confirmed_device_identity" in result["evidence_missing"]
        assert "confirmed_device_required" in result["rules_triggered"]


def test_verified_internal_grade_is_not_a_decision_driver() -> None:
    grade_a = recommend_next_action(base_payload(internal_assessment_status="verified", internal_grade="A"))
    grade_c = recommend_next_action(base_payload(internal_assessment_status="verified", internal_grade="C"))
    for key in ["recommended_action", "decision_strength", "headline", "rules_triggered"]:
        assert_equal(grade_a[key], grade_c[key], f"verified internal grade must not alter {key}")
    assert_equal(grade_a["evidence_used"]["internal_assessment_verified"], True, "verified internal status exposed")
    assert_equal(grade_c["evidence_used"]["internal_assessment_verified"], True, "verified internal status exposed C")
    assert_equal(grade_a["evidence_used"]["internal_grade_used"], False, "verified internal A not used")
    assert_equal(grade_c["evidence_used"]["internal_grade_used"], False, "verified internal C not used")

    unverified = recommend_next_action(base_payload(internal_grade="B+"))
    assert_equal(unverified["evidence_used"]["internal_assessment_verified"], False, "unverified internal status false")
    assert_equal(unverified["evidence_used"]["internal_grade_used"], False, "unverified internal grade ignored")


def test_legacy_severe_damage_text_consistency() -> None:
    for text in ["camera glass damage", "camera island impact", "screen crack"]:
        result = recommend_next_action(
            base_payload(
                exterior_grade="B",
                detected_damage=[text],
                valuation={**base_payload()["valuation"], "repair_references": [{"price": 100}]},
            )
        )
        assert_equal(result["recommended_action"], None, f"legacy severe text action {text}")
        assert "inconsistent_exterior_evidence" in result["rules_triggered"]

    deep_scratch = recommend_next_action(
        base_payload(
            exterior_grade="B",
            detected_damage=["deep scratch"],
            valuation={**base_payload()["valuation"], "repair_references": [{"price": 100}]},
        )
    )
    assert "inconsistent_exterior_evidence" not in deep_scratch["rules_triggered"]
    assert deep_scratch["recommended_action"] is not None


def test_damage_evidence_aggregates_all_supported_fields() -> None:
    confirmed_crack = {"damage_type": "screen_crack", "verification": "confirmed", "grade_affecting": True}
    unverified_crack = {"damage_type": "screen_crack", "verification": "unverified", "grade_affecting": True}
    cases = [
        base_payload(exterior_grade="A", detected_damage=[], merged_damage=[confirmed_crack]),
        base_payload(exterior_grade="A", detected_damage="None confirmed", merged_damage=[confirmed_crack]),
        base_payload(exterior_grade="A", detected_damage=[unverified_crack], merged_damage=[confirmed_crack]),
        base_payload(exterior_grade="A", detected_damage=[confirmed_crack]),
        base_payload(exterior_grade="A", merged_damage=[confirmed_crack]),
        base_payload(exterior_grade="A", damage=[confirmed_crack]),
    ]
    for payload in cases:
        result = recommend_next_action(payload)
        assert_equal(result["recommended_action"], None, "confirmed damage from any supported field must require review")
        assert_equal(result["decision_strength"], "review_required", "aggregated damage strength")
        assert "inconsistent_exterior_evidence" in result["rules_triggered"]
        assert_equal(result["evidence_used"]["detected_damage_considered"], True, "aggregated damage considered")

    duplicate = recommend_next_action(
        base_payload(exterior_grade="A", detected_damage=[confirmed_crack], merged_damage=[confirmed_crack], damage=[confirmed_crack])
    )
    assert_equal(duplicate["rules_triggered"].count("inconsistent_exterior_evidence"), 1, "duplicate damage rule count")
    assert_equal(len(duplicate["reasons"]), 1, "duplicate damage reason count")


def test_legacy_machine_damage_tokens_normalized_for_consistency() -> None:
    severe_tokens = [
        "screen_crack",
        "screen-crack",
        "back_glass_crack",
        "camera_glass_damage",
        "camera_island_impact",
    ]
    for token in severe_tokens:
        result = recommend_next_action(base_payload(exterior_grade="B", detected_damage=[token]))
        assert_equal(result["recommended_action"], None, f"machine severe token action {token}")
        assert_equal(result["decision_strength"], "review_required", f"machine severe token strength {token}")
        assert "inconsistent_exterior_evidence" in result["rules_triggered"]

    for token in ["deep_scratch", "deep-scratch"]:
        result = recommend_next_action(base_payload(exterior_grade="B", detected_damage=[token]))
        assert result["recommended_action"] is not None, f"deep scratch token remains valid flow {token}"
        assert "inconsistent_exterior_evidence" not in result["rules_triggered"]

    grade_a_machine_damage = recommend_next_action(base_payload(exterior_grade="A", detected_damage=["screen_crack"]))
    assert_equal(grade_a_machine_damage["recommended_action"], None, "Grade A machine-style damage action")
    assert "inconsistent_exterior_evidence" in grade_a_machine_damage["rules_triggered"]


def test_currency_contract_accepts_myr_and_missing() -> None:
    for currency in ["MYR", "myr", " MYR ", "", None]:
        valuation = {**base_payload()["valuation"]}
        if currency is None:
            valuation.pop("currency", None)
        else:
            valuation["currency"] = currency
        result = recommend_next_action(base_payload(valuation=valuation))
        assert_equal(result["recommended_action"], "resell", f"accepted currency action {currency!r}")
        assert_equal(result["evidence_used"]["currency"], "MYR", f"accepted evidence currency {currency!r}")
        assert_equal(result["evidence_used"]["market_range"]["currency"], "MYR", f"accepted market currency {currency!r}")


def test_currency_contract_rejects_unsupported_values() -> None:
    for currency in ["USD", "SGD", " eur ", True, 123]:
        valuation = {**base_payload()["valuation"], "currency": currency}
        try:
            recommend_next_action(base_payload(valuation=valuation))
        except DecisionInputError as error:
            assert_equal(error.error, "unsupported_currency", f"unsupported currency error {currency!r}")
            assert_equal(error.extra["supported_currencies"], ["MYR"], f"supported currencies {currency!r}")
        else:
            raise AssertionError(f"unsupported currency must be rejected: {currency!r}")


def test_api_endpoint_rejects_unsupported_currency() -> None:
    server = HTTPServer(("127.0.0.1", 0), ReValueHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = post_raw(
            port,
            "/decision-support",
            json.dumps(base_payload(valuation={**base_payload()["valuation"], "currency": "USD"})),
        )
        assert_equal(status, 400, "unsupported currency HTTP status")
        assert_equal(body["status"], "error", "unsupported currency API status")
        assert_equal(body["error"], "unsupported_currency", "unsupported currency API error")
        assert_equal(body["supported_currencies"], ["MYR"], "unsupported currency API supported list")

        status, body = post_raw(
            port,
            "/decision-support",
            json.dumps(base_payload(valuation={**base_payload()["valuation"], "currency": "MYR"})),
        )
        assert_equal(status, 200, "MYR currency HTTP status")
        assert_equal(body["status"], "ok", "MYR currency API status")
        assert_equal(body["evidence_used"]["currency"], "MYR", "MYR currency API evidence")
    finally:
        server.shutdown()


def main() -> int:
    tests = [
        test_clean_device_balanced_recommends_resell,
        test_competitive_trade_in_recommended,
        test_maximize_value_prefers_resell_when_resale_stronger,
        test_repair_relevant_damage_with_economical_repair,
        test_expensive_repair_does_not_blindly_recommend_repair,
        test_extend_device_life_recommends_keep_for_healthy_device,
        test_minimize_spend_recommends_keep_for_healthy_device,
        test_insufficient_market_evidence_returns_review_required,
        test_unverified_internal_grade_does_not_change_material_result,
        test_trade_in_shape_normalisation,
        test_repair_shape_normalisation,
        test_input_immutability,
        test_determinism,
        test_trade_in_threshold_boundaries,
        test_repair_threshold_boundaries,
        test_api_endpoint,
        test_no_synthetic_dealer_band_dependency,
        test_absurd_trade_in_cannot_win_convenience,
        test_trade_in_minimum_viability_boundaries,
        test_damaged_without_repair_evidence_requires_review,
        test_market_midpoint_normalization_cases,
        test_malicious_midpoint_does_not_corrupt_repair_decision,
        test_missing_or_unknown_exterior_grade_requires_review,
        test_user_priority_validation,
        test_endpoint_rejects_non_object_json,
        test_endpoint_rejects_invalid_priority,
        test_trade_in_wording_distinguishes_absent_below_and_competitive,
        test_status_invariants_across_representative_cases,
        test_adversarial_numeric_inputs_do_not_crash_or_corrupt,
        test_allowed_actions_and_no_confidence_percentage,
        test_s24_clean_regression_gate,
        test_maximize_value_superior_trade_in_beats_resell,
        test_maximize_value_damaged_superior_trade_in_does_not_claim_repair_optimal,
        test_maximize_value_low_trade_in_still_resells_for_healthy_device,
        test_contradictory_exterior_evidence_requires_review,
        test_boolean_values_are_not_money,
        test_endpoint_rejects_malformed_json,
        test_overflow_safe_midpoint_and_money_sanity_limit,
        test_trade_in_wording_below_near_inside_and_above_range,
        test_confirmed_device_identity_required,
        test_structured_damage_verification_must_be_confirmed,
        test_real_s24_local_fallback_regression_gate,
        test_minimize_spend_damaged_device_does_not_fall_through_to_resell,
        test_priority_matrix_respects_selected_objective,
        test_confirmed_device_identity_requires_valid_strings,
        test_verified_internal_grade_is_not_a_decision_driver,
        test_legacy_severe_damage_text_consistency,
        test_damage_evidence_aggregates_all_supported_fields,
        test_legacy_machine_damage_tokens_normalized_for_consistency,
        test_currency_contract_accepts_myr_and_missing,
        test_currency_contract_rejects_unsupported_values,
        test_api_endpoint_rejects_unsupported_currency,
    ]
    for test in tests:
        test()
    print(f"decision engine tests passed: {len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
