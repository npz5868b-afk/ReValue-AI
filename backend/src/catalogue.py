from __future__ import annotations

from .phone_catalogue import match_catalogue_candidate, search_catalogue


def normalize_model_name(raw_brand: str, raw_model: str) -> str | None:
    match = match_catalogue_candidate(raw_brand, raw_model)
    if match.get("status") in {"exact", "strong"}:
        catalogue_match = match.get("catalogue_match") or {}
        return f"{catalogue_match.get('brand')} {catalogue_match.get('model')}"
    return None


def storage_options_for_model(canonical_model: str) -> list[int]:
    results = search_catalogue(canonical_model, limit=1)
    if not results:
        return []
    storage_options = results[0].get("storage_options") or []
    parsed = []
    for storage in storage_options:
        if storage.endswith("GB") and storage[:-2].isdigit():
            parsed.append(int(storage[:-2]))
        elif storage.endswith("TB") and storage[:-2].isdigit():
            parsed.append(int(storage[:-2]) * 1024)
    return parsed

