from __future__ import annotations

import csv
import re
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .gemini_client import GEMINI_MODEL, GeminiAuthorizationRequired, GeminiClient, GeminiTemporaryUnavailable
from .phone_catalogue import get_catalogue_device, match_catalogue_candidate, normalize_storage, normalize_text
from .schemas import MarketObservation


REQUIRED_MARKET_FIELDS = [
    "source",
    "source_url",
    "observed_at",
    "country",
    "currency",
    "brand",
    "model",
    "storage",
    "condition",
    "price",
    "price_type",
]

MARKET_CACHE_TTL_SECONDS = 24 * 60 * 60
VALID_EXTERIOR_GRADES = {"A", "B", "C", "C-"}
RESALE_PRICE_TYPES = {"asking_price", "used_retail_price", "refurbished_retail_price", "completed_sale"}
TRADE_IN_PRICE_TYPES = {"trade_in_offer"}
REPAIR_PRICE_TYPES = {"repair_cost"}
INTERNATIONAL_PRICE_TYPES = {"international_reference"}
PRICE_TYPE_ALIASES = {
    "c2c_asking": "asking_price",
    "asking": "asking_price",
    "used": "used_retail_price",
    "used_retail": "used_retail_price",
    "refurbished": "refurbished_retail_price",
    "trade_in": "trade_in_offer",
    "tradein": "trade_in_offer",
}
BAD_EVIDENCE_PATTERNS = {
    "accessory_only": re.compile(r"\b(case|cover|screen protector|tempered glass|casing|charger|cable)\b", re.I),
    "spare_part": re.compile(r"\b(spare parts?|parts only|housing only|back glass only|lcd only)\b", re.I),
    "wanted_ad": re.compile(r"\b(wanted|want to buy|wtb|looking for)\b", re.I),
    "deposit_only": re.compile(r"\b(deposit|booking fee)\b", re.I),
    "instalment_only": re.compile(r"\b(monthly|per month|installment|instalment|ansuran|bulan)\b", re.I),
    "bundle_unclear": re.compile(r"\b(bundle|with plan|contract)\b", re.I),
    "new_sealed": re.compile(r"\b(new sealed|sealed set|brand new sealed|unopened)\b", re.I),
}
MALAYSIA_COUNTRIES = {"my", "mys", "malaysia"}
MYR_CURRENCIES = {"myr", "rm"}
_MARKET_CACHE: dict[str, dict[str, Any]] = {}
TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_NAMES = {"fbclid", "gclid", "yclid", "mc_cid", "mc_eid"}


MARKET_EVIDENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "market_observations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "source_url": {"type": "string"},
                    "source_type": {"type": "string"},
                    "observed_at": {"type": "string"},
                    "country": {"type": "string"},
                    "currency": {"type": "string"},
                    "brand": {"type": "string"},
                    "model": {"type": "string"},
                    "storage": {"type": "string"},
                    "condition_text": {"type": "string"},
                    "condition_group": {"type": "string"},
                    "price": {"type": "number"},
                    "price_type": {"type": "string"},
                    "listing_title": {"type": "string"},
                },
                "required": ["source", "source_url", "country", "currency", "brand", "model", "price", "price_type"],
            },
        },
    },
    "required": ["market_observations"],
}


class MarketEvidenceProvider(Protocol):
    def retrieve(self, confirmed_device: dict[str, Any], condition_group: str) -> dict[str, Any]:
        ...


class GeminiGroundedMarketEvidenceProvider:
    def __init__(self, client: GeminiClient | None = None) -> None:
        self.client = client or GeminiClient(model=GEMINI_MODEL)

    def retrieve(self, confirmed_device: dict[str, Any], condition_group: str) -> dict[str, Any]:
        prompt = build_market_search_prompt(confirmed_device, condition_group)
        return self.client.generate_structured(
            prompt=prompt,
            images=[],
            response_schema=MARKET_EVIDENCE_SCHEMA,
            use_search_grounding=True,
        )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clear_market_cache() -> None:
    _MARKET_CACHE.clear()


def canonicalize_confirmed_device(confirmed_device: dict[str, Any] | None) -> tuple[bool, str, dict[str, Any] | None]:
    if not confirmed_device:
        return False, "confirmed_device_required", None
    catalogue_id = str(confirmed_device.get("catalogue_id") or "").strip()
    storage = str(confirmed_device.get("storage") or "").strip()
    if not catalogue_id:
        return False, "catalogue_id_required", None
    if not storage:
        return False, "storage_required", None
    catalogue_device = get_catalogue_device(catalogue_id)
    if not catalogue_device:
        return False, "catalogue_id_not_found", None
    supplied_brand = str(confirmed_device.get("brand") or "").strip()
    supplied_model = str(confirmed_device.get("model") or "").strip()
    if supplied_brand and normalize_text(supplied_brand) != normalize_text(str(catalogue_device["brand"])):
        return False, "confirmed_device_identity_mismatch", None
    if supplied_model and normalize_text(supplied_model) != normalize_text(str(catalogue_device["model"])):
        return False, "confirmed_device_identity_mismatch", None
    normalized_storage = normalize_storage(storage)
    if not normalized_storage:
        return False, "storage_confirmation_required", None
    return (
        True,
        "",
        {
            "catalogue_id": catalogue_device["catalogue_id"],
            "brand": catalogue_device["brand"],
            "model": catalogue_device["model"],
            "storage": normalized_storage,
        },
    )


def condition_group_from_exterior_grade(exterior_grade: str | None) -> str:
    grade = (exterior_grade or "").strip().upper()
    if grade == "A":
        return "clean"
    if grade == "B":
        return "normal_used"
    if grade == "C":
        return "damaged_working"
    if grade == "C-":
        return "severe_cosmetic_damage"
    return "unknown"


def normalize_exterior_grade(exterior_grade: str | None) -> str:
    grade = (exterior_grade or "").strip().upper()
    return grade if grade in VALID_EXTERIOR_GRADES else ""


def exterior_grade_error_response() -> dict[str, Any]:
    return {"status": "error", "error": "valid_exterior_grade_required"}


def build_market_search_prompt(confirmed_device: dict[str, Any], condition_group: str) -> str:
    brand = confirmed_device.get("brand", "")
    model = confirmed_device.get("model", "")
    storage = confirmed_device.get("storage", "")
    return f"""Find current Malaysia used-phone market evidence for {brand} {model} {storage}.
Return observable evidence rows only, not a final valuation.
Prioritize exact model and exact storage Malaysian asking, used retail, refurbished retail,
trade-in, and relevant repair references. Label each price_type clearly. Never treat asking
listings as sold prices. Exclude accessories, deposits, instalments, wrong models, wrong
storage, sealed-new devices for used-market distribution, and unclear bundles.
Target ReValue condition group for comparison: {condition_group}."""


def parse_storage_gb(value: Any) -> int | None:
    if value is None:
        return None
    normalized = normalize_storage(str(value))
    if normalized:
        amount = normalized[:-2]
        unit = normalized[-2:]
        if amount.isdigit():
            return int(amount) * (1024 if unit == "TB" else 1)
    text = str(value).replace("GB", "").strip()
    return int(text) if text.isdigit() else None


def parse_price(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("RM", "").replace("MYR", "").replace(",", "").strip()
    match = re.search(r"\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None


def normalize_price_type(value: Any) -> str:
    text = normalize_text(str(value or "")).replace(" ", "_")
    return PRICE_TYPE_ALIASES.get(text, text)


def normalize_condition_group(value: Any) -> str:
    text = normalize_text(str(value or "")).replace(" ", "_").replace("-", "_")
    if text in {"clean", "a", "a_like", "like_new"}:
        return "clean"
    if text in {"normal_used", "normal", "used", "b", "b_like"}:
        return "normal_used"
    if text in {"damaged_working", "damaged_but_working", "c", "c_like"}:
        return "damaged_working"
    if text in {"severe_cosmetic_damage", "c_"}:
        return "severe_cosmetic_damage"
    if text == "functional_defect":
        return "functional_defect"
    return text or "unknown"


def country_is_malaysia(value: str) -> bool:
    return normalize_text(value) in MALAYSIA_COUNTRIES


def currency_is_myr(value: str) -> bool:
    return normalize_text(value) in MYR_CURRENCIES


def _cache_key(confirmed_device: dict[str, Any], condition_group: str) -> str:
    return "|".join(
        [
            normalize_text(str(confirmed_device.get("brand", ""))),
            normalize_text(str(confirmed_device.get("model", ""))),
            normalize_storage(str(confirmed_device.get("storage", ""))) or "",
            condition_group,
        ]
    )


def _dedupe_key(obs: MarketObservation) -> tuple[str, str, str, str, str]:
    url = normalize_text(obs.source_url)
    if url:
        return ("url", url, "", "", "")
    return (
        normalize_text(obs.source),
        normalize_text(obs.listing_title),
        normalize_text(obs.model),
        str(obs.storage_gb or ""),
        str(obs.price or ""),
    )


def normalize_source_url(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"
    query_items = []
    for key, val in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered in TRACKING_QUERY_NAMES or any(lowered.startswith(prefix) for prefix in TRACKING_QUERY_PREFIXES):
            continue
        query_items.append((key, val))
    query = urlencode(query_items, doseq=True)
    return urlunparse((scheme, netloc, path, "", query, ""))


def grounded_sources_from_payload(provider_payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = provider_payload.get("grounded_sources")
    if candidates is None:
        candidates = provider_payload.get("_grounded_sources", [])
    sources = []
    seen = set()
    for item in candidates or []:
        url = normalize_source_url(str(item.get("url") or item.get("uri") or ""))
        if not url or url in seen:
            continue
        seen.add(url)
        sources.append(
            {
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or item.get("uri") or ""),
                "normalized_url": url,
                "source_kind": str(item.get("source_kind") or "google_search_grounding"),
            }
        )
    return sources


def verify_grounding_source(source_url: str, grounded_sources: list[dict[str, Any]]) -> tuple[bool, str, str]:
    normalized = normalize_source_url(source_url)
    for source in grounded_sources:
        if normalized and normalized == source.get("normalized_url"):
            return True, source.get("url", ""), source.get("title", "")
    return False, "", ""


def classify_freshness(observed_at: str | None, retrieved_at: str | None = None) -> str:
    text = (observed_at or "").strip()
    if not text:
        return "unknown"
    normalized = text.replace("Z", "+00:00")
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return "unknown"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    reference = datetime.now(timezone.utc)
    if retrieved_at:
        try:
            reference = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
            if reference.tzinfo is None:
                reference = reference.replace(tzinfo=timezone.utc)
        except ValueError:
            reference = datetime.now(timezone.utc)
    return "fresh" if (reference - parsed).days <= 90 else "stale"


def _exclusion_from_text(title: str, condition_text: str, price_type: str) -> str:
    text = f"{title} {condition_text}"
    for reason, pattern in BAD_EVIDENCE_PATTERNS.items():
        if reason == "new_sealed" and price_type not in RESALE_PRICE_TYPES:
            continue
        if pattern.search(text):
            return reason
    return ""


def _matches_confirmed_model(row: dict[str, Any], confirmed_device: dict[str, Any]) -> bool:
    brand = str(row.get("brand") or "")
    model = str(row.get("model") or "")
    confirmed_id = str(confirmed_device.get("catalogue_id") or "")
    match = match_catalogue_candidate(brand, model)
    catalogue_match = match.get("catalogue_match") or {}
    if catalogue_match.get("catalogue_id") and confirmed_id:
        return catalogue_match["catalogue_id"] == confirmed_id
    return (
        normalize_text(brand) == normalize_text(str(confirmed_device.get("brand", "")))
        and normalize_text(model) == normalize_text(str(confirmed_device.get("model", "")))
    )


def require_confirmed_device(confirmed_device: dict[str, Any] | None) -> tuple[bool, str]:
    ok, error, _ = canonicalize_confirmed_device(confirmed_device)
    return ok, error


def validate_market_observations(
    rows: list[dict[str, Any]],
    *,
    confirmed_device: dict[str, Any],
    condition_group: str,
    retrieved_at: str,
    grounded_sources: list[dict[str, Any]] | None = None,
    require_grounding: bool = True,
) -> tuple[list[MarketObservation], list[MarketObservation]]:
    validated: list[MarketObservation] = []
    excluded: list[MarketObservation] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    confirmed_storage_gb = parse_storage_gb(confirmed_device.get("storage"))
    grounded_sources = grounded_sources or []

    for row in rows:
        price_type = normalize_price_type(row.get("price_type"))
        condition_text = str(row.get("condition_text") or row.get("condition") or "")
        row_condition_group = normalize_condition_group(row.get("condition_group") or condition_text)
        storage_gb = parse_storage_gb(row.get("storage") or row.get("storage_gb"))
        price = parse_price(row.get("price"))
        title = str(row.get("listing_title") or "")
        currency = "MYR" if currency_is_myr(str(row.get("currency") or "")) else str(row.get("currency") or "").upper()
        country = "MY" if country_is_malaysia(str(row.get("country") or "")) else str(row.get("country") or "")
        exact_model = _matches_confirmed_model(row, confirmed_device)
        exact_storage = confirmed_storage_gb is not None and storage_gb == confirmed_storage_gb
        grounding_verified, grounded_source_url, grounded_source_title = verify_grounding_source(
            str(row.get("source_url") or ""), grounded_sources
        )
        freshness_status = classify_freshness(str(row.get("observed_at") or ""), retrieved_at)

        observation = MarketObservation(
            source=str(row.get("source") or ""),
            source_url=str(row.get("source_url") or ""),
            source_type=str(row.get("source_type") or ""),
            observed_at=str(row.get("observed_at") or ""),
            retrieved_at=retrieved_at,
            country=country,
            currency=currency,
            brand=str(row.get("brand") or ""),
            model=str(row.get("model") or ""),
            storage_gb=storage_gb,
            condition=condition_text,
            condition_text=condition_text,
            condition_group=row_condition_group,
            price=price,
            price_type=price_type,
            listing_title=title,
            exact_model_match=exact_model,
            exact_storage_match=exact_storage,
            grounding_verified=grounding_verified,
            grounded_source_url=grounded_source_url,
            grounded_source_title=grounded_source_title,
            freshness_status=freshness_status,
        )

        reason = ""
        text_reason = _exclusion_from_text(title, condition_text, price_type)
        if not observation.source or not observation.source_url:
            reason = "missing_source_or_url"
        elif price is None or price <= 0:
            reason = "missing_or_invalid_price"
        elif price_type not in RESALE_PRICE_TYPES | TRADE_IN_PRICE_TYPES | REPAIR_PRICE_TYPES | INTERNATIONAL_PRICE_TYPES:
            reason = "unsupported_price_type"
        elif text_reason:
            reason = text_reason
        elif require_grounding and not grounding_verified:
            reason = "unverified_grounding_source"
        elif not exact_model:
            reason = "wrong_model_or_variant"
        elif confirmed_storage_gb is not None and not exact_storage and price_type not in REPAIR_PRICE_TYPES:
            reason = "wrong_or_missing_storage"
        elif price_type in RESALE_PRICE_TYPES and not country_is_malaysia(country):
            reason = "non_malaysia_resale_evidence"
        elif price_type in RESALE_PRICE_TYPES and not currency_is_myr(currency):
            reason = "non_myr_resale_evidence"

        dedupe_key = _dedupe_key(observation)
        if not reason and dedupe_key in seen:
            reason = "duplicate_listing"
        if not reason:
            seen.add(dedupe_key)

        if reason:
            observation.exclusion_reason = reason
            excluded.append(observation)
            continue

        if price_type in RESALE_PRICE_TYPES:
            observation.usable_for_market_range = True
        else:
            observation.usable_for_market_range = False
            observation.exclusion_reason = "separate_benchmark"
        validated.append(observation)

    return validated, excluded


def load_market_observations_csv(path: str | Path) -> list[MarketObservation]:
    observations: list[MarketObservation] = []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [field for field in REQUIRED_MARKET_FIELDS if field not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"Missing required market fields: {', '.join(missing)}")
        for row in reader:
            observations.append(
                MarketObservation(
                    source=row["source"],
                    source_url=row["source_url"],
                    observed_at=row["observed_at"],
                    retrieved_at=row.get("retrieved_at", ""),
                    country=row["country"],
                    currency="MYR" if currency_is_myr(row["currency"]) else row["currency"],
                    brand=row["brand"],
                    model=row["model"],
                    storage_gb=parse_storage_gb(row.get("storage")),
                    condition=row["condition"],
                    condition_text=row.get("condition_text", row["condition"]),
                    condition_group=normalize_condition_group(row.get("condition_group") or row["condition"]),
                    price=parse_price(row.get("price")),
                    price_type=normalize_price_type(row["price_type"]),
                    source_type=row.get("source_type", ""),
                    listing_title=row.get("listing_title", ""),
                    exact_model_match=row.get("exact_model_match", "").lower() == "true",
                    exact_storage_match=row.get("exact_storage_match", "").lower() == "true",
                    usable_for_market_range=row.get("usable_for_market_range", "").lower() == "true",
                    exclusion_reason=row.get("exclusion_reason", ""),
                    grounding_verified=row.get("grounding_verified", "").lower() == "true",
                    grounded_source_url=row.get("grounded_source_url", ""),
                    grounded_source_title=row.get("grounded_source_title", ""),
                    freshness_status=classify_freshness(row.get("observed_at"), row.get("retrieved_at")),
                )
            )
    return observations


def retrieve_current_market_evidence(
    confirmed_device: dict[str, Any] | None = None,
    exterior_grade: str | None = None,
    provider: MarketEvidenceProvider | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    if confirmed_device is None:
        return {
            "status": "ready",
            "service": "market_evidence",
            "requires_confirmed_device": True,
            "requires_user_confirmed_storage": True,
            "cache_ttl_seconds": MARKET_CACHE_TTL_SECONDS,
            "notes": [
                "POST /market-evidence/retrieve with a confirmed catalogue device and exterior grade.",
                "No frontend changes are required for Phase 6C.",
            ],
        }

    normalized_grade = normalize_exterior_grade(exterior_grade)
    if not normalized_grade:
        return {
            "status": "error",
            "retrieved_at": now_iso(),
            "error": "valid_exterior_grade_required",
            "market_observations": [],
            "usable_observation_count": 0,
            "excluded_observation_count": 0,
        }

    ok, error, canonical_device = canonicalize_confirmed_device(confirmed_device)
    retrieved_at = now_iso()
    if not ok:
        return {
            "status": "error",
            "retrieved_at": retrieved_at,
            "error": error,
            "market_observations": [],
            "usable_observation_count": 0,
            "excluded_observation_count": 0,
        }
    assert canonical_device is not None

    condition_group = condition_group_from_exterior_grade(normalized_grade)
    cache_key = _cache_key(canonical_device, condition_group)
    if use_cache and cache_key in _MARKET_CACHE:
        cached = _MARKET_CACHE[cache_key]
        age_seconds = int(time.time() - cached["cached_at_epoch"])
        if age_seconds <= MARKET_CACHE_TTL_SECONDS:
            payload = dict(cached["payload"])
            payload["cache_hit"] = True
            payload["cache_age_seconds"] = age_seconds
            return payload

    provider = provider or GeminiGroundedMarketEvidenceProvider()
    try:
        provider_payload = provider.retrieve(canonical_device, condition_group)
    except GeminiAuthorizationRequired as exc:
        return {
            "status": "blocked",
            "retrieved_at": retrieved_at,
            "query_device": canonical_device,
            "condition_group": condition_group,
            "internal_assessment_status": "not_assessed",
            "retryable": False,
            "error": str(exc),
            "market_observations": [],
            "usable_observation_count": 0,
            "excluded_observation_count": 0,
            "trade_in_benchmarks": [],
            "repair_references": [],
            "international_references": [],
            "grounded_sources": [],
            "notes": ["Live market retrieval requires a private backend Gemini/Search credential."],
            "cache_hit": False,
        }
    except GeminiTemporaryUnavailable:
        return {
            "status": "temporarily_unavailable",
            "stage": "market_evidence_retrieval",
            "retrieved_at": retrieved_at,
            "query_device": canonical_device,
            "condition_group": condition_group,
            "internal_assessment_status": "not_assessed",
            "retryable": True,
            "error": "Market evidence retrieval is temporarily unavailable. Please try again.",
            "market_observations": [],
            "usable_observation_count": 0,
            "excluded_observation_count": 0,
            "trade_in_benchmarks": [],
            "repair_references": [],
            "international_references": [],
            "grounded_sources": [],
            "notes": ["No market rows were fabricated after the retryable retrieval failure."],
            "cache_hit": False,
        }
    except RuntimeError as exc:
        return {
            "status": "error",
            "stage": "market_evidence_retrieval",
            "retrieved_at": retrieved_at,
            "query_device": canonical_device,
            "condition_group": condition_group,
            "internal_assessment_status": "not_assessed",
            "retryable": False,
            "error": str(exc),
            "market_observations": [],
            "usable_observation_count": 0,
            "excluded_observation_count": 0,
            "trade_in_benchmarks": [],
            "repair_references": [],
            "international_references": [],
            "grounded_sources": [],
            "notes": ["Market evidence retrieval failed safely without fabricating evidence."],
            "cache_hit": False,
        }

    grounded_sources = grounded_sources_from_payload(provider_payload)
    observations, excluded = validate_market_observations(
        list(provider_payload.get("market_observations") or []),
        confirmed_device=canonical_device,
        condition_group=condition_group,
        retrieved_at=retrieved_at,
        grounded_sources=grounded_sources,
        require_grounding=True,
    )
    market_rows = [obs for obs in observations if obs.usable_for_market_range]
    trade_in = [obs for obs in observations if obs.price_type in TRADE_IN_PRICE_TYPES]
    repair = [obs for obs in observations if obs.price_type in REPAIR_PRICE_TYPES]
    international = [obs for obs in observations if obs.price_type in INTERNATIONAL_PRICE_TYPES]
    status = "ok" if market_rows else "insufficient_market_evidence"
    notes = [
        "Live Internal Check is not implemented; internal_assessment_status is not_assessed.",
        "Gemini/Search evidence is used only to retrieve structured source observations. Python valuation calculates the RM range.",
        "Outlier filtering is not applied in this prototype; Q1/median/Q3 provide robust descriptive statistics.",
    ]
    freshness_values = {obs.freshness_status for obs in market_rows}
    if "stale" in freshness_values:
        notes.append("Some usable market evidence is stale based on observable dates.")
    if "unknown" in freshness_values:
        notes.append("Some usable market evidence has unknown observable listing dates.")
    payload = {
        "status": status,
        "retrieved_at": retrieved_at,
        "query_device": canonical_device,
        "condition_group": condition_group,
        "revalue_exterior_grade": normalized_grade,
        "internal_assessment_status": "not_assessed",
        "market_observations": [asdict(obs) for obs in market_rows],
        "usable_observation_count": len(market_rows),
        "excluded_observation_count": len(excluded),
        "excluded_observations": [asdict(obs) for obs in excluded],
        "trade_in_benchmarks": [asdict(obs) for obs in trade_in],
        "repair_references": [asdict(obs) for obs in repair],
        "international_references": [asdict(obs) for obs in international],
        "grounded_sources": grounded_sources,
        "notes": notes,
        "cache_hit": False,
        "cache_age_seconds": 0,
    }
    if use_cache:
        _MARKET_CACHE[cache_key] = {"cached_at_epoch": time.time(), "payload": payload}
    return payload
