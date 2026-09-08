from __future__ import annotations

import os
from typing import Any


GEMINI_MODEL = "gemini-3.7-flash"


class GeminiAuthorizationRequired(RuntimeError):
    """Raised when a server-side Gemini credential is required but unavailable."""


class GeminiTemporaryUnavailable(RuntimeError):
    """Raised for retryable Gemini service or network failures."""


RETRYABLE_STATUS_CODES = {429, 500, 503}
RETRYABLE_ERROR_TEXT = (
    "429",
    "500",
    "503",
    "unavailable",
    "temporarily",
    "high demand",
    "timeout",
    "timed out",
    "deadline",
    "connection reset",
    "network",
)


def is_retryable_gemini_error(exc: BaseException) -> bool:
    for attr in ("code", "status_code", "status"):
        value = getattr(exc, attr, None)
        if value in RETRYABLE_STATUS_CODES:
            return True
        if isinstance(value, str) and value.isdigit() and int(value) in RETRYABLE_STATUS_CODES:
            return True
    text = f"{type(exc).__name__} {exc}".casefold()
    return any(marker in text for marker in RETRYABLE_ERROR_TEXT)


def _value_from_object(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _object_to_public_mapping(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return {str(key): value for key, value in obj.items() if isinstance(value, (str, int, float, bool, list, dict))}
    if hasattr(obj, "to_json_dict"):
        try:
            value = obj.to_json_dict()
            if isinstance(value, dict):
                return value
        except Exception:
            pass
    if hasattr(obj, "model_dump"):
        try:
            value = obj.model_dump()
            if isinstance(value, dict):
                return value
        except Exception:
            pass
    return {}


def _iter_response_candidates(response: Any) -> list[Any]:
    candidates = _value_from_object(response, "candidates")
    if isinstance(candidates, list):
        return candidates
    return []


def extract_grounded_sources(response: Any) -> list[dict[str, Any]]:
    """Extract sources from actual Gemini grounding metadata when present."""

    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in _iter_response_candidates(response):
        metadata = _value_from_object(candidate, "grounding_metadata") or _value_from_object(candidate, "groundingMetadata")
        chunks = _value_from_object(metadata, "grounding_chunks") or _value_from_object(metadata, "groundingChunks") or []
        if not isinstance(chunks, list):
            continue
        for chunk in chunks:
            web = _value_from_object(chunk, "web")
            if not web:
                continue
            url = _value_from_object(web, "uri") or _value_from_object(web, "url")
            title = _value_from_object(web, "title") or ""
            if not url or str(url) in seen:
                continue
            seen.add(str(url))
            sources.append(
                {
                    "title": str(title),
                    "url": str(url),
                    "source_kind": "google_search_grounding",
                }
            )
    return sources


class GeminiClient:
    """Server-side Gemini interface.

    This class intentionally does not expose or store API keys in frontend code.
    A production deployment should inject GEMINI_API_KEY or GOOGLE_API_KEY into a
    private backend environment.
    """

    def __init__(self, api_key: str | None = None, model: str = GEMINI_MODEL) -> None:
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self.model = model

    @property
    def is_authorized(self) -> bool:
        return bool(self.api_key)

    def require_authorization(self) -> None:
        if not self.is_authorized:
            raise GeminiAuthorizationRequired("GEMINI AUTHORIZATION REQUIRED FOR LIVE TESTING")

    def generate_structured(
        self,
        *,
        prompt: str,
        images: list[dict[str, Any]],
        response_schema: dict[str, Any],
        use_search_grounding: bool = False,
    ) -> dict[str, Any]:
        """Call Gemini with structured output.

        The transport is deliberately isolated here so the app can be audited for
        API-key safety. The local package does not make live calls without a
        configured backend credential.
        """

        self.require_authorization()
        try:
            from google import genai  # type: ignore
        except Exception as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "google-genai is not installed in this environment. Install backend requirements before live Gemini calls."
            ) from exc

        client = genai.Client(api_key=self.api_key)
        contents: list[Any] = [{"text": prompt}]
        for image in images:
            if image.get("bytes_b64"):
                contents.append(
                    {
                        "inline_data": {
                            "mime_type": image.get("mime_type", "image/jpeg"),
                            "data": image["bytes_b64"],
                        }
                    }
                )

        config: dict[str, Any] = {
            "response_mime_type": "application/json",
            "response_schema": response_schema,
        }
        if use_search_grounding:
            config["tools"] = [{"google_search": {}}]

        try:
            response = client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )
        except Exception as exc:  # pragma: no cover - SDK-specific exception classes vary
            if is_retryable_gemini_error(exc):
                raise GeminiTemporaryUnavailable(
                    "AI identification is temporarily unavailable. Please try again."
                ) from exc
            raise RuntimeError("Gemini request failed.") from exc
        if not getattr(response, "text", None):
            raise RuntimeError("Gemini returned an empty structured response.")

        import json

        payload = json.loads(response.text)
        if use_search_grounding:
            payload["_grounded_sources"] = extract_grounded_sources(response)
            payload["_grounding_metadata_present"] = bool(payload["_grounded_sources"])
        return payload
