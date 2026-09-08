from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .gemini_client import GEMINI_MODEL, GeminiAuthorizationRequired, GeminiClient
from .schemas import GeminiDamageVerification, ImageInput, YoloDetection


DAMAGE_VERIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verifications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "damage_type": {"type": "string"},
                    "view": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["supports", "does_not_support", "uncertain"],
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["minor", "moderate", "severe", "unknown"],
                    },
                    "visual_evidence": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["damage_type", "view", "status", "severity", "visual_evidence"],
            },
        }
    },
    "required": ["verifications"],
}


VERIFICATION_PROMPT = """Verify visible exterior damage from the provided photos
and YOLO candidate boxes. Return only observable evidence. Do not invent damage,
do not infer internal function, and do not produce A/B/C grades."""


def verify_damage_with_gemini(
    *,
    images: list[ImageInput],
    yolo_detections: list[YoloDetection],
    client: GeminiClient | None = None,
) -> tuple[str, list[GeminiDamageVerification]]:
    client = client or GeminiClient(model=GEMINI_MODEL)
    prompt = VERIFICATION_PROMPT + "\nYOLO candidates:\n" + str([asdict(d) for d in yolo_detections])
    try:
        response = client.generate_structured(
            prompt=prompt,
            images=[asdict(image) for image in images],
            response_schema=DAMAGE_VERIFICATION_SCHEMA,
            use_search_grounding=False,
        )
    except GeminiAuthorizationRequired as exc:
        return str(exc), []

    verifications = [
        GeminiDamageVerification(
            damage_type=item.get("damage_type", "other_visible_damage"),
            view=item.get("view", "unknown"),
            status=item.get("status", "uncertain"),
            severity=item.get("severity", "unknown"),
            visual_evidence=item.get("visual_evidence", []),
        )
        for item in response.get("verifications", [])
    ]
    return "ok", verifications
