from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


DamageType = Literal[
    "crack",
    "screen_crack",
    "back_glass_crack",
    "scuff",
    "scratch",
    "deep_scratch",
    "frame_dent",
    "corner_chip",
    "camera_glass_damage",
    "camera_island_impact",
    "other_visible_damage",
]

ViewType = Literal["front", "back", "camera", "side", "unknown"]
Severity = Literal["minor", "moderate", "severe", "unknown"]
VerificationState = Literal["confirmed", "unverified", "rejected"]


@dataclass
class ImageInput:
    view: ViewType
    filename: str | None = None
    mime_type: str | None = None
    bytes_b64: str | None = None


@dataclass
class DeviceCandidate:
    brand: str
    model: str
    confidence_label: Literal["likely", "possible", "uncertain"]
    visual_evidence: list[str] = field(default_factory=list)


@dataclass
class DeviceIdentificationResult:
    status: Literal["ok", "blocked", "error", "temporarily_unavailable"]
    gemini_model: str
    top_candidates: list[DeviceCandidate] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    requires_user_confirmation: bool = True
    error: str | None = None
    retryable: bool = False
    stage: str | None = None


@dataclass
class YoloDetection:
    damage_type: DamageType | None
    confidence: float
    view: ViewType
    raw_class: str = ""
    bounding_box_xyxy: list[float] | None = None
    source: str = "revalue_exterior_yolo11n_v0.2"
    requires_gemini_resolution: bool = False


@dataclass
class GeminiDamageVerification:
    damage_type: DamageType
    view: ViewType
    status: Literal["supports", "does_not_support", "uncertain"]
    severity: Severity = "unknown"
    visual_evidence: list[str] = field(default_factory=list)


@dataclass
class MergedDamage:
    damage_type: DamageType
    view: ViewType
    severity: Severity
    support: Literal["yolo_only", "gemini_only", "hybrid_supported", "needs_review"]
    verification: VerificationState = "unverified"
    grade_affecting: bool = False
    manual_review: bool = True
    confidence: float | None = None
    bounding_box_xyxy: list[float] | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class GradeResult:
    exterior_grade: Literal["A", "B", "C", "C-", "pending"]
    reason_codes: list[str]
    note: str


@dataclass
class MarketObservation:
    source: str
    source_url: str
    observed_at: str
    country: str
    currency: str
    brand: str
    model: str
    storage_gb: int | None
    condition: str
    price: float | None
    price_type: str
    source_type: str = ""
    retrieved_at: str = ""
    condition_text: str = ""
    condition_group: str = ""
    listing_title: str = ""
    exact_model_match: bool = False
    exact_storage_match: bool = False
    usable_for_market_range: bool = False
    exclusion_reason: str = ""
    grounding_verified: bool = False
    grounded_source_url: str = ""
    grounded_source_title: str = ""
    freshness_status: Literal["fresh", "stale", "unknown"] = "unknown"


@dataclass
class ValuationResult:
    status: Literal[
        "estimated_market_value",
        "prototype_market_estimate",
        "limited_local_market_evidence",
        "insufficient_market_evidence",
    ]
    currency: str
    value_low: float | None
    value_high: float | None
    comparable_count: int
    exact_model_count: int
    condition_matched_count: int
    notes: list[str] = field(default_factory=list)
    distribution: dict[str, Any] = field(default_factory=dict)
    midpoint: float | None = None
    evidence_status: str = ""
    trade_in_benchmark: list[dict[str, Any]] = field(default_factory=list)
    repair_references: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
