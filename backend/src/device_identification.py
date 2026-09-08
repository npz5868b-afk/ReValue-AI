from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .gemini_client import GEMINI_MODEL, GeminiAuthorizationRequired, GeminiClient, GeminiTemporaryUnavailable
from .phone_catalogue import normalize_gemini_candidates
from .schemas import DeviceCandidate, DeviceIdentificationResult, ImageInput


DEVICE_IDENTIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "brand": {"type": "string"},
        "top_candidates": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "brand": {"type": "string"},
                    "model": {"type": "string"},
                    "confidence_label": {
                        "type": "string",
                        "enum": ["likely", "possible", "uncertain"],
                    },
                    "visual_evidence": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["brand", "model", "confidence_label", "visual_evidence"],
            },
        },
    },
    "required": ["brand", "top_candidates"],
}


IDENTIFICATION_PROMPT = """Identify the smartphone model from exterior photos.
Return at most three candidates. Use only short observable visual evidence such
as camera layout, frame shape, rear glass finish, and button placement. Do not
claim certainty and do not infer storage from photos."""


def identify_device(
    images: list[ImageInput],
    client: GeminiClient | None = None,
) -> DeviceIdentificationResult:
    client = client or GeminiClient(model=GEMINI_MODEL)
    payload_images = [asdict(image) for image in images]
    try:
        response = client.generate_structured(
            prompt=IDENTIFICATION_PROMPT,
            images=payload_images,
            response_schema=DEVICE_IDENTIFICATION_SCHEMA,
            use_search_grounding=False,
        )
    except GeminiAuthorizationRequired as exc:
        return DeviceIdentificationResult(
            status="blocked",
            gemini_model=client.model,
            error=str(exc),
            requires_user_confirmation=True,
        )
    except GeminiTemporaryUnavailable:
        return DeviceIdentificationResult(
            status="temporarily_unavailable",
            gemini_model=client.model,
            error="AI identification is temporarily unavailable. Please try again.",
            retryable=True,
            stage="device_identification",
            requires_user_confirmation=True,
        )

    candidates = [
        DeviceCandidate(
            brand=item.get("brand") or response.get("brand") or "Unknown",
            model=item.get("model", "Unknown"),
            confidence_label=item.get("confidence_label", "uncertain"),
            visual_evidence=item.get("visual_evidence", []),
        )
        for item in response.get("top_candidates", [])[:3]
    ]
    return DeviceIdentificationResult(
        status="ok",
        gemini_model=client.model,
        top_candidates=candidates,
        candidates=normalize_gemini_candidates(candidates),
        requires_user_confirmation=True,
    )
