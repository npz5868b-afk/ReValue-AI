from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


CATALOGUE_PATH = Path(__file__).resolve().parents[1] / "data" / "phone_catalogue.json"
VARIANT_TOKENS = {
    "pro",
    "max",
    "ultra",
    "plus",
    "fe",
    "mini",
    "se",
    "fold",
    "flip",
}
PUNCT_RE = re.compile(r"[\u2010-\u2015_./,:;]+")
STORAGE_RE = re.compile(r"^\s*(\d+)\s*(GB|TB)\s*$", re.IGNORECASE)
CONNECTIVITY_TOKENS = {"4g", "5g", "lte"}


@dataclass(frozen=True)
class CatalogueEntry:
    catalogue_id: str
    brand: str
    model: str
    normalized_brand: str
    normalized_model: str
    storage_options: list[str]
    storage_options_source: str
    storage_options_complete: bool
    requires_user_confirmation: bool
    allows_manual_storage_entry: bool
    ram: list[str]
    source_row_count: int
    detail_url: str = ""
    release_year: int | None = None
    source_name: str = ""
    source_license: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CatalogueEntry":
        return cls(
            catalogue_id=data["catalogue_id"],
            brand=data["brand"],
            model=data["model"],
            normalized_brand=data["normalized_brand"],
            normalized_model=data["normalized_model"],
            storage_options=list(data.get("storage_options") or []),
            storage_options_source=data.get("storage_options_source", "catalogue_observed"),
            storage_options_complete=bool(data.get("storage_options_complete", False)),
            requires_user_confirmation=bool(data.get("requires_user_confirmation", True)),
            allows_manual_storage_entry=bool(data.get("allows_manual_storage_entry", True)),
            ram=list(data.get("ram") or []),
            source_row_count=int(data.get("source_row_count") or 0),
            detail_url=data.get("detail_url", ""),
            release_year=data.get("release_year"),
            source_name=data.get("source_name", ""),
            source_license=data.get("source_license", ""),
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "catalogue_id": self.catalogue_id,
            "brand": self.brand,
            "model": self.model,
            "storage_options": self.storage_options,
            "storage_options_source": self.storage_options_source,
            "storage_options_complete": self.storage_options_complete,
            "requires_user_confirmation": True,
            "allows_manual_storage_entry": True,
            "ram": self.ram,
            "source_row_count": self.source_row_count,
            "release_year": self.release_year,
            "detail_url": self.detail_url,
        }


def normalize_text(value: str | None) -> str:
    text = " ".join((value or "").strip().split())
    text = text.replace("+", " plus ")
    text = PUNCT_RE.sub(" ", text)
    text = re.sub(r"[^0-9A-Za-z]+", " ", text)
    return " ".join(text.casefold().split())


def variant_signature(model: str | None) -> tuple[str, ...]:
    tokens = set(normalize_text(model).split())
    return tuple(sorted(tokens & VARIANT_TOKENS))


def normalize_storage(value: str | None) -> str | None:
    text = (value or "").replace(" ", "")
    match = STORAGE_RE.match(text)
    if not match:
        return None
    amount, unit = match.groups()
    return f"{int(amount)}{unit.upper()}"


@lru_cache(maxsize=1)
def load_catalogue() -> list[CatalogueEntry]:
    with CATALOGUE_PATH.open(encoding="utf-8") as handle:
        data = json.load(handle)
    return [CatalogueEntry.from_dict(item) for item in data]


@lru_cache(maxsize=1)
def catalogue_by_id() -> dict[str, CatalogueEntry]:
    return {entry.catalogue_id: entry for entry in load_catalogue()}


@lru_cache(maxsize=1)
def catalogue_by_exact_key() -> dict[tuple[str, str], CatalogueEntry]:
    return {
        (entry.normalized_brand, entry.normalized_model): entry
        for entry in load_catalogue()
    }


def _candidate_pool(brand: str | None) -> list[CatalogueEntry]:
    normalized_brand = normalize_text(brand)
    if not normalized_brand:
        return load_catalogue()
    return [entry for entry in load_catalogue() if entry.normalized_brand == normalized_brand]


def _exact_key(entries: list[CatalogueEntry]) -> dict[tuple[str, str], CatalogueEntry]:
    return {(entry.normalized_brand, entry.normalized_model): entry for entry in entries}


def _tokens_without_connectivity(model: str | None) -> tuple[str, ...]:
    return tuple(token for token in normalize_text(model).split() if token not in CONNECTIVITY_TOKENS)


def _connectivity_signature(model: str | None) -> tuple[str, ...]:
    return tuple(sorted(token for token in normalize_text(model).split() if token in CONNECTIVITY_TOKENS))


def _same_meaningful_variant(query_model: str | None, entry: CatalogueEntry) -> bool:
    return variant_signature(query_model) == variant_signature(entry.model)


def _connectivity_compatible_matches(
    entries: list[CatalogueEntry],
    brand: str | None,
    model: str | None,
) -> list[CatalogueEntry]:
    query_connectivity = _connectivity_signature(model)
    if query_connectivity:
        return []

    query_base = _tokens_without_connectivity(model)
    matches = []
    for entry in _candidate_pool_from_entries(entries, brand):
        entry_connectivity = _connectivity_signature(entry.model)
        if not entry_connectivity:
            continue
        if not _same_meaningful_variant(model, entry):
            continue
        if _tokens_without_connectivity(entry.model) == query_base:
            matches.append(entry)
    return matches


def _candidate_pool_from_entries(entries: list[CatalogueEntry], brand: str | None) -> list[CatalogueEntry]:
    normalized_brand = normalize_text(brand)
    if not normalized_brand:
        return entries
    return [entry for entry in entries if entry.normalized_brand == normalized_brand]


def match_catalogue_candidate(
    brand: str | None,
    model: str | None,
    entries: list[CatalogueEntry] | None = None,
) -> dict[str, Any]:
    catalogue_entries = entries or load_catalogue()
    normalized_brand = normalize_text(brand)
    normalized_model = normalize_text(model)
    if not normalized_model:
        return {"status": "not_found", "reason": "empty_model"}

    exact = _exact_key(catalogue_entries).get((normalized_brand, normalized_model))
    if exact:
        return {"status": "exact", "catalogue_match": exact.public_dict()}

    connectivity_matches = _connectivity_compatible_matches(catalogue_entries, brand, model)
    if connectivity_matches:
        connectivity_signatures = {_connectivity_signature(entry.model) for entry in connectivity_matches}
        if len(connectivity_matches) == 1 and len(connectivity_signatures) == 1:
            return {
                "status": "strong",
                "match_reason": "connectivity_suffix_compatible",
                "catalogue_match": connectivity_matches[0].public_dict(),
            }
        return {
            "status": "ambiguous",
            "match_reason": "connectivity_suffix_requires_user_confirmation",
            "candidates": [entry.public_dict() for entry in connectivity_matches[:5]],
        }

    query_signature = variant_signature(model)
    pool = [
        entry
        for entry in _candidate_pool_from_entries(catalogue_entries, brand)
        if variant_signature(entry.model) == query_signature
    ]
    scored: list[tuple[float, CatalogueEntry]] = []
    for entry in pool:
        ratio = difflib.SequenceMatcher(None, normalized_model, entry.normalized_model).ratio()
        if ratio >= 0.92:
            scored.append((ratio, entry))

    scored.sort(key=lambda item: (item[0], item[1].source_row_count, item[1].model), reverse=True)
    if not scored:
        return {"status": "not_found"}

    top_score = scored[0][0]
    tied = [entry for score, entry in scored if abs(score - top_score) < 0.01]
    if len(tied) > 1:
        return {
            "status": "ambiguous",
            "candidates": [entry.public_dict() for entry in tied[:5]],
            "match_score": round(top_score, 4),
        }

    return {
        "status": "strong",
        "catalogue_match": scored[0][1].public_dict(),
        "match_score": round(top_score, 4),
    }


def normalize_gemini_candidates(candidates: list[Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for rank, candidate in enumerate(candidates[:3], start=1):
        if hasattr(candidate, "brand"):
            gemini_candidate = {
                "brand": candidate.brand,
                "model": candidate.model,
                "likelihood": getattr(candidate, "confidence_label", "uncertain"),
                "visual_evidence": getattr(candidate, "visual_evidence", []),
            }
        else:
            gemini_candidate = {
                "brand": candidate.get("brand", ""),
                "model": candidate.get("model", ""),
                "likelihood": candidate.get("confidence_label", candidate.get("likelihood", "uncertain")),
                "visual_evidence": candidate.get("visual_evidence", []),
            }
        results.append(
            {
                "rank": rank,
                "gemini_candidate": gemini_candidate,
                "catalogue_match": match_catalogue_candidate(
                    gemini_candidate["brand"],
                    gemini_candidate["model"],
                ),
            }
        )
    return results


def search_catalogue(query: str, limit: int = 10) -> list[dict[str, Any]]:
    normalized_query = normalize_text(query)
    if not normalized_query:
        return []

    exact_or_strong = []
    for entry in load_catalogue():
        combined = normalize_text(f"{entry.brand} {entry.model}")
        if combined == normalized_query or entry.normalized_model == normalized_query:
            exact_or_strong.append({"status": "exact", **entry.public_dict()})
        elif normalized_query in combined:
            exact_or_strong.append({"status": "contains", **entry.public_dict()})
    if exact_or_strong:
        return exact_or_strong[:limit]

    scored = []
    for entry in load_catalogue():
        combined = normalize_text(f"{entry.brand} {entry.model}")
        score = difflib.SequenceMatcher(None, normalized_query, combined).ratio()
        if score >= 0.90:
            scored.append((score, entry))
    scored.sort(key=lambda item: (item[0], item[1].source_row_count), reverse=True)
    return [
        {"status": "fuzzy", "match_score": round(score, 4), **entry.public_dict()}
        for score, entry in scored[:limit]
    ]


def get_catalogue_device(catalogue_id: str) -> dict[str, Any] | None:
    entry = catalogue_by_id().get(catalogue_id)
    return entry.public_dict() if entry else None


def confirm_device(catalogue_id: str, storage: str | None) -> dict[str, Any]:
    entry = catalogue_by_id().get(catalogue_id)
    if not entry:
        return {"status": "error", "error": "catalogue_id_not_found"}

    normalized_storage = normalize_storage(storage)
    if not normalized_storage:
        return {
            "status": "error",
            "error": "storage_confirmation_required",
            "catalogue_match": entry.public_dict(),
        }

    return {
        "status": "ok",
        "confirmed_device": {
            "catalogue_id": entry.catalogue_id,
            "brand": entry.brand,
            "model": entry.model,
            "storage": normalized_storage,
            "storage_in_catalogue_options": normalized_storage in entry.storage_options,
            "storage_options": entry.storage_options,
            "storage_options_source": entry.storage_options_source,
            "storage_options_complete": False,
            "allows_manual_storage_entry": True,
        },
    }
