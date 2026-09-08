from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .exterior_verification import verify_damage_with_gemini
from .grading_rules import grade_from_atomic_damage
from .hybrid_merge import merge_damage_signals
from .schemas import GradeResult, ImageInput
from .yolo_detector import YOLO_MODEL_NAME, YoloDetectorUnavailable, detect_exterior_damage


def analyze_exterior(images: list[ImageInput], *, run_gemini_verification: bool = True) -> dict[str, Any]:
    """Run the Phase 6A.4 real exterior analysis backend flow."""

    try:
        detections = detect_exterior_damage(images)
    except YoloDetectorUnavailable as exc:
        return {
            "status": "blocked",
            "yolo_model": YOLO_MODEL_NAME,
            "error": str(exc),
            "detections": [],
            "gemini_verifications": [],
            "merged_damage": [],
            "grade": asdict(
                GradeResult(
                    exterior_grade="pending",
                    reason_codes=["yolo_detector_unavailable"],
                    note="Exterior grade pending real YOLO inference.",
                )
            ),
        }
    except ValueError as exc:
        return {
            "status": "error",
            "yolo_model": YOLO_MODEL_NAME,
            "error": str(exc),
            "detections": [],
            "gemini_verifications": [],
            "merged_damage": [],
            "grade": asdict(
                GradeResult(
                    exterior_grade="pending",
                    reason_codes=["invalid_image_payload"],
                    note="Exterior grade pending usable exterior images.",
                )
            ),
        }

    gemini_status = "skipped"
    gemini_verifications = []
    if run_gemini_verification:
        gemini_status, gemini_verifications = verify_damage_with_gemini(
            images=images,
            yolo_detections=detections,
        )

    mapped_detections = [item for item in detections if item.damage_type is not None]
    merged_damage = merge_damage_signals(mapped_detections, gemini_verifications)
    grade = grade_from_atomic_damage(merged_damage)

    unresolved = [item for item in detections if item.requires_gemini_resolution]
    notes: list[str] = []
    if unresolved:
        notes.append("Some YOLO generic crack detections require Gemini or manual resolution because their capture view was camera/side/unknown.")
    if gemini_status != "ok":
        notes.append(gemini_status)

    return {
        "status": "ok",
        "yolo_model": YOLO_MODEL_NAME,
        "detections": [asdict(item) for item in detections],
        "gemini_verifications": [asdict(item) for item in gemini_verifications],
        "merged_damage": [asdict(item) for item in merged_damage],
        "grade": asdict(grade),
        "notes": notes,
    }

