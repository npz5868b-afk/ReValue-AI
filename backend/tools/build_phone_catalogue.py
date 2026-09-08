from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RAW_CSV = ROOT / "data" / "raw" / "phones_data_20250729_181901.csv"
OUT_JSON = ROOT / "data" / "phone_catalogue.json"
OUT_CSV = ROOT / "data" / "phone_catalogue.csv"
OUT_AUDIT = ROOT / "data" / "phone_catalogue_audit_v0.2.json"
OUT_LAST_BUILD = ROOT / "data" / "last_catalogue_build_output.json"
OUT_KNOWN = ROOT / "data" / "known_device_catalogue_audit_v0.2.csv"

SOURCE_NAME = "Global Smartphone Database 2025"
SOURCE_AUTHOR = "Rajib Dab"
SOURCE_URL = "https://www.kaggle.com/datasets/rajibdab/global-smartphone-database-2025"
SOURCE_LICENSE = "Apache 2.0"
LICENSE_EVIDENCE_SOURCE = "Kaggle dataset page"
LICENSE_FILE_EMBEDDED_IN_ARCHIVE = False

FIELD_WHITELIST = [
    "brand",
    "model",
    "device_type",
    "release_date",
    "status",
    "chipset",
    "internal_storage",
    "ram",
    "storage_type",
    "screen_size",
    "operating_system",
    "os_version",
    "detail_url",
    "scraped_at",
]

IGNORED_PRICE_FIELDS = [
    "price_official",
    "price_old",
    "price_savings",
    "price_unofficial",
    "price_updated",
    "price_variants",
]

STORAGE_SUFFIX_RE = re.compile(r"\s*\((\d+\s*(?:GB|TB))\)\s*$", re.IGNORECASE)
MEMORY_CONFIG_SUFFIX_RE = re.compile(
    r"\s*\((\d+\s*(?:GB|TB))\s*/\s*(\d+\s*(?:GB|TB))\)\s*$",
    re.IGNORECASE,
)
STORAGE_VALUE_RE = re.compile(r"^\s*(\d+)\s*(GB|TB)\s*$", re.IGNORECASE)
PUNCT_RE = re.compile(r"[\u2010-\u2015_./,:;]+")


def clean_text(value: str | None) -> str:
    return " ".join((value or "").strip().split())


def normalize_for_match(value: str | None) -> str:
    text = clean_text(value)
    text = text.replace("+", " plus ")
    text = PUNCT_RE.sub(" ", text)
    text = re.sub(r"[^0-9A-Za-z]+", " ", text)
    return " ".join(text.casefold().split())


def normalize_brand_label(value: str | None) -> str:
    return normalize_for_match(value)


def canonical_model_name(raw_model: str) -> tuple[str, str | None, str | None]:
    model = clean_text(raw_model)
    memory_match = MEMORY_CONFIG_SUFFIX_RE.search(model)
    if memory_match:
        ram = normalize_storage(memory_match.group(1))
        storage = normalize_storage(memory_match.group(2))
        model = MEMORY_CONFIG_SUFFIX_RE.sub("", model).strip()
        return model, storage, ram
    match = STORAGE_SUFFIX_RE.search(model)
    suffix_storage = None
    if match:
        suffix_storage = normalize_storage(match.group(1))
        model = STORAGE_SUFFIX_RE.sub("", model).strip()
    return model, suffix_storage, None


def normalize_storage(value: str | None) -> str | None:
    text = clean_text(value).replace(" ", "")
    match = STORAGE_VALUE_RE.match(text)
    if not match:
        return None
    amount, unit = match.groups()
    return f"{int(amount)}{unit.upper()}"


def parsed_release_year(release_date: str | None) -> int | None:
    text = clean_text(release_date)
    match = re.search(r"(19|20)\d{2}", text)
    return int(match.group(0)) if match else None


def parse_scraped_at(value: str | None) -> str:
    text = clean_text(value)
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text).isoformat()
    except ValueError:
        return text


def field_completeness(row: dict[str, str]) -> int:
    return sum(1 for field in FIELD_WHITELIST if clean_text(row.get(field)))


def stable_catalogue_id(normalized_brand: str, normalized_model: str) -> str:
    digest = hashlib.sha256(f"{normalized_brand}|{normalized_model}".encode("utf-8")).hexdigest()[:16]
    return f"phone_{digest}"


def is_scrape_artifact(row: dict[str, str]) -> bool:
    return normalize_for_match(row.get("brand")) == "comparisons" and normalize_for_match(row.get("model")) == "comparisons"


def choose_representative(rows: list[dict[str, str]]) -> dict[str, str]:
    return sorted(
        rows,
        key=lambda row: (
            parse_scraped_at(row.get("scraped_at")),
            field_completeness(row),
            clean_text(row.get("brand")),
            clean_text(row.get("model")),
            clean_text(row.get("detail_url")),
        ),
        reverse=True,
    )[0]


def load_rows() -> tuple[list[str], list[dict[str, str]]]:
    with RAW_CSV.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = [{key: value for key, value in row.items()} for row in reader]
        return list(reader.fieldnames or []), rows


def build() -> dict[str, Any]:
    fields, rows = load_rows()
    null_counts = Counter()
    seen_full_rows: set[tuple[tuple[str, str], ...]] = set()
    full_duplicates = 0
    for row in rows:
        row_key = tuple((field, row.get(field, "")) for field in fields)
        if row_key in seen_full_rows:
            full_duplicates += 1
        seen_full_rows.add(row_key)
        for field in fields:
            if not clean_text(row.get(field)):
                null_counts[field] += 1

    raw_brand_count = len({row.get("brand", "") for row in rows})
    normalized_brand_labels = {normalize_brand_label(row.get("brand")) for row in rows if normalize_brand_label(row.get("brand"))}
    artifacts = [row for row in rows if is_scrape_artifact(row)]
    usable_rows = [row for row in rows if not is_scrape_artifact(row)]
    normalized_usable_brand_count = len(
        {normalize_brand_label(row.get("brand")) for row in usable_rows if normalize_brand_label(row.get("brand"))}
    )

    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in usable_rows:
        brand_norm = normalize_brand_label(row.get("brand"))
        canonical_model, _, _ = canonical_model_name(row.get("model", ""))
        model_norm = normalize_for_match(canonical_model)
        if not brand_norm or not model_norm:
            continue
        grouped[(brand_norm, model_norm)].append(row)

    catalogue: list[dict[str, Any]] = []
    duplicate_group_count = 0
    duplicate_source_row_count = 0
    for (brand_norm, model_norm), group_rows in sorted(grouped.items()):
        if len(group_rows) > 1:
            duplicate_group_count += 1
            duplicate_source_row_count += len(group_rows)
        representative = choose_representative(group_rows)
        canonical_model, representative_suffix_storage, representative_suffix_ram = canonical_model_name(representative.get("model", ""))
        observed_storage = set()
        ram_values = set()
        source_row_refs = []
        for row in group_rows:
            row_model, suffix_storage, suffix_ram = canonical_model_name(row.get("model", ""))
            for candidate in [suffix_storage, normalize_storage(row.get("internal_storage"))]:
                if candidate:
                    observed_storage.add(candidate)
            ram = suffix_ram or normalize_storage(row.get("ram"))
            if ram:
                ram_values.add(ram)
            source_row_refs.append(
                {
                    "model": clean_text(row.get("model")),
                    "internal_storage": normalize_storage(row.get("internal_storage")),
                    "ram": suffix_ram or normalize_storage(row.get("ram")),
                    "detail_url": clean_text(row.get("detail_url")),
                    "scraped_at": parse_scraped_at(row.get("scraped_at")),
                }
            )

        entry = {
            "catalogue_id": stable_catalogue_id(brand_norm, model_norm),
            "brand": clean_text(representative.get("brand")),
            "model": canonical_model,
            "normalized_brand": brand_norm,
            "normalized_model": model_norm,
            "device_type": clean_text(representative.get("device_type")),
            "release_date": clean_text(representative.get("release_date")),
            "release_year": parsed_release_year(representative.get("release_date")),
            "status": clean_text(representative.get("status")),
            "chipset": clean_text(representative.get("chipset")),
            "storage_options": sorted(observed_storage, key=storage_sort_key),
            "storage_options_source": "catalogue_observed",
            "storage_options_complete": False,
            "requires_user_confirmation": True,
            "allows_manual_storage_entry": True,
            "ram": sorted(ram_values, key=storage_sort_key),
            "storage_type": clean_text(representative.get("storage_type")),
            "screen_size": clean_text(representative.get("screen_size")),
            "operating_system": clean_text(representative.get("operating_system")),
            "os_version": clean_text(representative.get("os_version")),
            "detail_url": clean_text(representative.get("detail_url")),
            "scraped_at": parse_scraped_at(representative.get("scraped_at")),
            "source_name": SOURCE_NAME,
            "source_author": SOURCE_AUTHOR,
            "source_url": SOURCE_URL,
            "source_license": SOURCE_LICENSE,
            "source_row_count": len(group_rows),
            "source_rows": source_row_refs,
        }
        if representative_suffix_storage and representative_suffix_storage not in observed_storage:
            entry["storage_options"].append(representative_suffix_storage)
        if representative_suffix_ram and representative_suffix_ram not in ram_values:
            entry["ram"].append(representative_suffix_ram)
        catalogue.append(entry)

    OUT_JSON.write_text(json.dumps(catalogue, ensure_ascii=False, indent=2), encoding="utf-8")
    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "catalogue_id",
                "brand",
                "model",
                "normalized_brand",
                "normalized_model",
                "device_type",
                "release_date",
                "release_year",
                "status",
                "chipset",
                "storage_options",
                "storage_options_source",
                "storage_options_complete",
                "requires_user_confirmation",
                "allows_manual_storage_entry",
                "ram",
                "storage_type",
                "screen_size",
                "operating_system",
                "os_version",
                "detail_url",
                "scraped_at",
                "source_name",
                "source_author",
                "source_url",
                "source_license",
                "source_row_count",
            ],
        )
        writer.writeheader()
        for entry in catalogue:
            row = dict(entry)
            row["storage_options"] = "|".join(entry["storage_options"])
            row["ram"] = "|".join(entry["ram"])
            row.pop("source_rows")
            writer.writerow(row)

    audit = {
        "source_name": SOURCE_NAME,
        "source_author": SOURCE_AUTHOR,
        "source_url": SOURCE_URL,
        "source_license": SOURCE_LICENSE,
        "license_evidence_source": LICENSE_EVIDENCE_SOURCE,
        "license_file_embedded_in_archive": LICENSE_FILE_EMBEDDED_IN_ARCHIVE,
        "source_file_used": str(RAW_CSV.name),
        "source_file_sha256": hashlib.sha256(RAW_CSV.read_bytes()).hexdigest().upper(),
        "raw_row_count": len(rows),
        "raw_column_count": len(fields),
        "raw_fields": fields,
        "field_whitelist": FIELD_WHITELIST,
        "ignored_price_fields": IGNORED_PRICE_FIELDS,
        "null_counts": dict(null_counts),
        "full_row_duplicate_count": full_duplicates,
        "raw_unique_brand_label_count": raw_brand_count,
        "normalized_brand_label_count_including_artifacts": len(normalized_brand_labels),
        "excluded_scrape_artifacts": [
            {
                "brand": row.get("brand"),
                "model": row.get("model"),
                "detail_url": row.get("detail_url"),
            }
            for row in artifacts
        ],
        "excluded_scrape_artifact_count": len(artifacts),
        "normalized_usable_brand_count": normalized_usable_brand_count,
        "canonical_model_count": len(catalogue),
        "duplicate_canonical_group_count": duplicate_group_count,
        "duplicate_source_row_count_in_duplicate_groups": duplicate_source_row_count,
        "deduplication_rule": [
            "group by normalized brand + canonical normalized model",
            "strip only terminal storage suffix such as (128GB), (256GB), (512GB), (1TB)",
            "strip only terminal RAM/storage configuration suffix such as (12GB/256GB) and retain RAM/storage evidence",
            "choose representative by latest valid scraped_at",
            "then greater whitelist field completeness",
            "then stable brand/model/detail_url tie-break",
        ],
    }
    OUT_AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_LAST_BUILD.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    write_known_device_audit(catalogue)
    return audit


def storage_sort_key(value: str) -> tuple[int, int, str]:
    match = STORAGE_VALUE_RE.match(value)
    if not match:
        return (2, 0, value)
    amount, unit = match.groups()
    multiplier = 1024 if unit.upper() == "TB" else 1
    return (0, int(amount) * multiplier, value)


def write_known_device_audit(catalogue: list[dict[str, Any]]) -> None:
    wanted = [
        ("Apple", "iPhone 13 Pro"),
        ("Apple", "iPhone 13 Pro Max"),
        ("Samsung", "Galaxy S22 Ultra"),
        ("Samsung", "Galaxy S22 Ultra 5G"),
        ("Samsung", "Galaxy S23 Ultra"),
        ("Samsung", "Galaxy S24"),
        ("Samsung", "Galaxy S24+"),
        ("Samsung", "Galaxy S24 Plus"),
        ("Samsung", "Galaxy S24 Ultra"),
    ]
    lookup = {(entry["normalized_brand"], entry["normalized_model"]): entry for entry in catalogue}
    with OUT_KNOWN.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "brand",
                "model",
                "status",
                "catalogue_id",
                "canonical_brand",
                "canonical_model",
                "storage_options",
                "source_row_count",
                "detail_url",
            ],
        )
        writer.writeheader()
        for brand, model in wanted:
            key = (normalize_brand_label(brand), normalize_for_match(model))
            entry = lookup.get(key)
            writer.writerow(
                {
                    "brand": brand,
                    "model": model,
                    "status": "found" if entry else "not_found",
                    "catalogue_id": entry["catalogue_id"] if entry else "",
                    "canonical_brand": entry["brand"] if entry else "",
                    "canonical_model": entry["model"] if entry else "",
                    "storage_options": "|".join(entry["storage_options"]) if entry else "",
                    "source_row_count": entry["source_row_count"] if entry else "",
                    "detail_url": entry["detail_url"] if entry else "",
                }
            )


def main() -> int:
    audit = build()
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
