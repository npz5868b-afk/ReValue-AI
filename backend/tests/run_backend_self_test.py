from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from backend.src.grading_rules import grade_from_atomic_damage
from backend.src.hybrid_merge import merge_damage_signals
from backend.src.schemas import GeminiDamageVerification, MarketObservation, YoloDetection
from backend.src.valuation import value_from_market_observations


def main() -> int:
    clean_grade = grade_from_atomic_damage([])
    assert clean_grade.exterior_grade == "A"

    detections = [
        YoloDetection(
            damage_type="back_glass_crack",
            confidence=0.82,
            view="back",
            raw_class="generic_crack",
            bounding_box_xyxy=[10, 10, 200, 300],
        )
    ]
    verifications = [
        GeminiDamageVerification(
            damage_type="back_glass_crack",
            view="back",
            status="supports",
            severity="severe",
            visual_evidence=["visible branching crack on back glass"],
        )
    ]
    merged = merge_damage_signals(detections, verifications)
    damaged_grade = grade_from_atomic_damage(merged)
    assert damaged_grade.exterior_grade == "C-"

    no_evidence = value_from_market_observations(
        observations=[],
        brand="Samsung",
        model="Galaxy S23 Ultra",
        storage_gb=256,
        condition_group="damaged-working",
    )
    assert no_evidence.status == "insufficient_market_evidence"

    observations = [
        MarketObservation(
            source="local_csv",
            source_url="https://example.invalid/listing",
            observed_at="2026-09-07",
            country="MY",
            currency="MYR",
            brand="Samsung",
            model="Galaxy S23 Ultra",
            storage_gb=256,
            condition="damaged-working",
            price=1600 + i * 10,
            price_type="c2c_asking",
        )
        for i in range(5)
    ]
    valuation = value_from_market_observations(
        observations=observations,
        brand="Samsung",
        model="Galaxy S23 Ultra",
        storage_gb=256,
        condition_group="damaged-working",
    )
    assert valuation.status == "estimated_market_value"
    assert valuation.condition_matched_count == 5
    print("backend self-test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
