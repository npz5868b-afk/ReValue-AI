from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from backend.src.device_identification import identify_device
from backend.src.grading_rules import grade_from_atomic_damage
from backend.src.hybrid_merge import merge_damage_signals
from backend.src.schemas import ImageInput, YoloDetection
from backend.src.yolo_detector import map_yolo_class_to_damage_type


def assert_equal(actual, expected, message: str) -> None:
    if actual != expected:
        raise AssertionError(f"{message}: expected {expected!r}, got {actual!r}")


def test_empty_clean_detections() -> None:
    merged = merge_damage_signals([], [])
    assert_equal(merged, [], "empty detections should merge to empty damage")
    grade = grade_from_atomic_damage(merged)
    assert_equal(grade.exterior_grade, "A", "empty real detections should not crash")


def test_view_aware_mapping() -> None:
    assert_equal(map_yolo_class_to_damage_type("generic_crack", "front"), "screen_crack", "front crack mapping")
    assert_equal(map_yolo_class_to_damage_type("generic_crack", "back"), "back_glass_crack", "back crack mapping")
    assert_equal(map_yolo_class_to_damage_type("scratch", "front"), "scratch", "scratch mapping")
    assert_equal(map_yolo_class_to_damage_type("generic_crack", "camera"), None, "camera generic crack must stay unresolved")
    assert_equal(map_yolo_class_to_damage_type("generic_crack", "side"), None, "side generic crack must stay unresolved")


def test_confidence_is_not_severity() -> None:
    high_confidence = YoloDetection(
        raw_class="scratch",
        damage_type="scratch",
        confidence=0.99,
        view="back",
        bounding_box_xyxy=[1, 2, 3, 4],
    )
    low_confidence = YoloDetection(
        raw_class="generic_crack",
        damage_type="screen_crack",
        confidence=0.31,
        view="front",
        bounding_box_xyxy=[5, 6, 7, 8],
    )
    merged = merge_damage_signals([high_confidence, low_confidence], [])
    by_type = {item.damage_type: item for item in merged}
    assert_equal(by_type["scratch"].severity, "unknown", "high confidence must not imply severe")
    assert_equal(by_type["screen_crack"].severity, "unknown", "low confidence must not imply minor")


def test_yolo_scratch_with_gemini_support_is_confirmed() -> None:
    detection = YoloDetection(
        raw_class="scratch",
        damage_type="scratch",
        confidence=0.81,
        view="back",
        bounding_box_xyxy=[10, 20, 30, 40],
    )
    from backend.src.schemas import GeminiDamageVerification

    verification = GeminiDamageVerification(
        damage_type="scratch",
        view="back",
        status="supports",
        severity="minor",
        visual_evidence=["thin visible line on rear surface"],
    )
    merged = merge_damage_signals([detection], [verification])
    assert_equal(merged[0].verification, "confirmed", "YOLO + Gemini support should be confirmed")
    assert_equal(merged[0].grade_affecting, True, "confirmed YOLO + Gemini damage should affect grade")
    assert_equal(merged[0].manual_review, False, "confirmed damage should not require manual review")


def test_yolo_front_crack_is_confirmed_without_confidence_severity() -> None:
    detection = YoloDetection(
        raw_class="generic_crack",
        damage_type="screen_crack",
        confidence=0.66,
        view="front",
        bounding_box_xyxy=[2, 4, 40, 80],
    )
    merged = merge_damage_signals([detection], [])
    assert_equal(merged[0].damage_type, "screen_crack", "front generic crack should be mapped before merge")
    assert_equal(merged[0].verification, "confirmed", "mapped YOLO crack should be confirmed")
    assert_equal(merged[0].grade_affecting, True, "mapped YOLO crack may affect grade")
    assert_equal(merged[0].severity, "unknown", "YOLO confidence must not become severity")


def test_clean_s24_ultra_gemini_only_scuff_does_not_affect_grade() -> None:
    from backend.src.schemas import GeminiDamageVerification

    verification = GeminiDamageVerification(
        damage_type="scuff",
        view="side",
        status="supports",
        severity="minor",
        visual_evidence=[
            "Light surface scuffing and small speck-like marks visible along the metallic side frame"
        ],
    )
    merged = merge_damage_signals([], [verification])
    assert_equal(len(merged), 1, "Gemini-only observation should be retained")
    assert_equal(merged[0].support, "gemini_only", "Gemini-only support source should be retained")
    assert_equal(merged[0].verification, "unverified", "Gemini-only cosmetic observation should stay unverified")
    assert_equal(merged[0].grade_affecting, False, "Gemini-only cosmetic observation should not affect grade")
    assert_equal(merged[0].manual_review, True, "Gemini-only cosmetic observation should require review")
    assert "not used for automatic grading" in " ".join(merged[0].notes)
    grade = grade_from_atomic_damage(merged)
    assert_equal(grade.exterior_grade, "A", "clean S24 Ultra should remain grade A with only unverified Gemini scuff")


def test_gemini_only_weak_scratch_does_not_affect_grade() -> None:
    from backend.src.schemas import GeminiDamageVerification

    verification = GeminiDamageVerification(
        damage_type="scratch",
        view="back",
        status="supports",
        severity="minor",
        visual_evidence=["possible faint surface mark"],
    )
    merged = merge_damage_signals([], [verification])
    assert_equal(merged[0].verification, "unverified", "Gemini-only scratch should stay unverified")
    assert_equal(merged[0].grade_affecting, False, "Gemini-only scratch should not affect grade")
    grade = grade_from_atomic_damage(merged)
    assert_equal(grade.exterior_grade, "A", "Gemini-only weak scratch should not downgrade")


def test_unresolved_generic_crack_is_not_merged_as_specific_damage() -> None:
    unresolved = YoloDetection(
        raw_class="generic_crack",
        damage_type=None,
        confidence=0.88,
        view="camera",
        bounding_box_xyxy=[1, 1, 10, 10],
        requires_gemini_resolution=True,
    )
    merged = merge_damage_signals([unresolved], [])
    assert_equal(merged, [], "camera/side generic crack must not fabricate specific damage")


def test_device_identification_endpoint_logic_remains_safe_without_key() -> None:
    result = identify_device([ImageInput(view="front", filename="front.jpg", mime_type="image/jpeg", bytes_b64=None)])
    assert_equal(result.requires_user_confirmation, True, "device ID must require confirmation")
    assert result.status in {"blocked", "ok"}
    if result.status == "blocked":
        assert "GEMINI AUTHORIZATION REQUIRED" in (result.error or "")


def main() -> int:
    test_empty_clean_detections()
    test_view_aware_mapping()
    test_confidence_is_not_severity()
    test_yolo_scratch_with_gemini_support_is_confirmed()
    test_yolo_front_crack_is_confirmed_without_confidence_severity()
    test_clean_s24_ultra_gemini_only_scuff_does_not_affect_grade()
    test_gemini_only_weak_scratch_does_not_affect_grade()
    test_unresolved_generic_crack_is_not_merged_as_specific_damage()
    test_device_identification_endpoint_logic_remains_safe_without_key()
    print("phase6a4 yolo integration tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
