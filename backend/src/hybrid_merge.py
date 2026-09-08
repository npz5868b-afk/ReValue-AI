from __future__ import annotations

from .schemas import GeminiDamageVerification, MergedDamage, YoloDetection


DEFAULT_THRESHOLDS = {
    "high_confidence": 0.70,
    "review_confidence": 0.40,
}


def merge_damage_signals(
    yolo_detections: list[YoloDetection],
    gemini_verifications: list[GeminiDamageVerification],
    thresholds: dict[str, float] | None = None,
) -> list[MergedDamage]:
    """Merge YOLO detections with Gemini verification.

    This function does not fabricate detections. It only merges signals supplied
    by real upstream detectors or explicit test fixtures.
    """

    _ = thresholds or DEFAULT_THRESHOLDS
    merged: list[MergedDamage] = []
    used_gemini: set[int] = set()

    for detection in yolo_detections:
        if detection.damage_type is None:
            continue
        matching_index = next(
            (
                idx
                for idx, verification in enumerate(gemini_verifications)
                if verification.damage_type == detection.damage_type and verification.view == detection.view
            ),
            None,
        )
        verification = gemini_verifications[matching_index] if matching_index is not None else None
        if matching_index is not None:
            used_gemini.add(matching_index)

        if verification and verification.status == "supports":
            support = "hybrid_supported"
            verification_state = "confirmed"
            grade_affecting = True
            manual_review = False
            severity = verification.severity
        elif verification and verification.status == "does_not_support":
            support = "needs_review"
            verification_state = "rejected"
            grade_affecting = False
            manual_review = True
            severity = "unknown"
        elif verification and verification.status == "uncertain":
            support = "needs_review"
            verification_state = "unverified"
            grade_affecting = False
            manual_review = True
            severity = "unknown"
        else:
            support = "yolo_only"
            verification_state = "confirmed"
            grade_affecting = True
            manual_review = False
            severity = "unknown"

        notes = []
        if verification and verification.status == "does_not_support":
            notes.append("Gemini did not support YOLO candidate; manual review required.")
        elif verification and verification.status == "uncertain":
            notes.append("Gemini verification uncertain; manual review required.")

        merged.append(
            MergedDamage(
                damage_type=detection.damage_type,
                view=detection.view,
                severity=severity,
                support=support,
                verification=verification_state,
                grade_affecting=grade_affecting,
                manual_review=manual_review,
                confidence=detection.confidence,
                bounding_box_xyxy=detection.bounding_box_xyxy,
                notes=notes,
            )
        )

    for idx, verification in enumerate(gemini_verifications):
        if idx in used_gemini or verification.status != "supports":
            continue
        merged.append(
            MergedDamage(
                damage_type=verification.damage_type,
                view=verification.view,
                severity=verification.severity,
                support="gemini_only",
                verification="unverified",
                grade_affecting=False,
                manual_review=True,
                notes=["Gemini-only observation; not used for automatic grading."],
            )
        )

    return merged
