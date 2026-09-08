from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


CONDITION_LABELS = {
    "clean_a_like": "Clean / A-like",
    "normal_used_b_like": "Normal used / B-like",
    "damaged_c_like": "Damaged but working / C-like",
    "functional_defect": "Functional defect",
}


def _to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"yes", "true", "1", "y"}:
        return True
    if text in {"no", "false", "0", "n"}:
        return False
    return None


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round_rm(value: float, step: int = 50) -> int:
    return int(round(value / step) * step)


def _stats(values: list[float]) -> dict[str, Any]:
    clean_values = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not clean_values:
        return {"count": 0, "min": None, "q1": None, "median": None, "q3": None, "max": None}

    def quantile(q: float) -> float:
        if len(clean_values) == 1:
            return clean_values[0]
        position = (len(clean_values) - 1) * q
        lower = int(math.floor(position))
        upper = int(math.ceil(position))
        if lower == upper:
            return clean_values[lower]
        weight = position - lower
        return clean_values[lower] * (1 - weight) + clean_values[upper] * weight

    return {
        "count": len(clean_values),
        "min": clean_values[0],
        "q1": quantile(0.25),
        "median": quantile(0.5),
        "q3": quantile(0.75),
        "max": clean_values[-1],
    }


class ReValueMalaysiaValuationEngine:
    """Comparable-based Malaysia RM valuation engine for ReValue AI Phase 3."""

    def __init__(self, calibration_path: str | Path | None = None):
        if calibration_path is None:
            calibration_path = (
                Path(__file__).resolve().parents[1]
                / "calibration"
                / "malaysia_market_calibration_v0.1.json"
            )
        self.calibration_path = Path(calibration_path)
        with self.calibration_path.open("r", encoding="utf-8") as handle:
            self.calibration = json.load(handle)
        self.observations = self.calibration["observations"]
        self.trade_in_lookup = self.calibration["trade_in_lookup"]
        self.repair_cost_lookup = self.calibration["repair_cost_lookup"]

    @staticmethod
    def infer_condition_group(device_condition: dict[str, Any]) -> str:
        explicit = device_condition.get("condition_group")
        if explicit:
            return str(explicit)

        display_ok = _to_bool(device_condition.get("display_ok"))
        camera_ok = _to_bool(device_condition.get("camera_ok"))
        charging_ok = _to_bool(device_condition.get("charging_ok"))
        biometric_ok = _to_bool(device_condition.get("face_id_or_biometric_ok"))
        sensor_ok = _to_bool(device_condition.get("sensor_ok"))
        speaker_ok = _to_bool(device_condition.get("speaker_ok"))
        microphone_ok = _to_bool(device_condition.get("microphone_ok"))

        functional_flags = [display_ok, camera_ok, charging_ok, biometric_ok, sensor_ok, speaker_ok, microphone_ok]
        if any(flag is False for flag in functional_flags):
            return "functional_defect"

        if (
            _to_bool(device_condition.get("screen_cracked")) is True
            or _to_bool(device_condition.get("back_glass_cracked")) is True
            or str(device_condition.get("replacement_parts", "")).strip()
            or str(device_condition.get("repair_history", "")).strip()
            or str(device_condition.get("exterior_grade", "")).strip().upper().startswith("C")
        ):
            return "damaged_c_like"

        if (
            _to_bool(device_condition.get("screen_scratched")) is True
            or _to_bool(device_condition.get("frame_dented")) is True
            or str(device_condition.get("exterior_grade", "")).strip().upper().startswith("B")
        ):
            return "normal_used_b_like"

        return "clean_a_like"

    @staticmethod
    def _device_key(device_condition: dict[str, Any]) -> str:
        brand = str(device_condition.get("brand", "")).strip().lower()
        model = str(device_condition.get("model", "")).strip().lower()
        storage = str(device_condition.get("storage_gb", "")).strip()
        return f"{brand}|{model}|{storage}"

    def _matching_observations(self, device_condition: dict[str, Any]) -> list[dict[str, Any]]:
        key = self._device_key(device_condition)
        return [
            row
            for row in self.observations
            if row["device_key"] == key
            and row.get("training_eligible") == "yes"
            and _to_float(row.get("listing_price_rm")) is not None
        ]

    @staticmethod
    def _condition_observations(rows: list[dict[str, Any]], condition_group: str) -> list[dict[str, Any]]:
        return [row for row in rows if row.get("proposed_revalue_condition_group") == condition_group]

    @staticmethod
    def _defect_observations(rows: list[dict[str, Any]], device_condition: dict[str, Any]) -> list[dict[str, Any]]:
        defect_fields = [
            "screen_cracked",
            "back_glass_cracked",
            "frame_dented",
        ]
        active_defects = [field for field in defect_fields if _to_bool(device_condition.get(field)) is True]
        if not active_defects:
            return []
        matches = []
        for row in rows:
            if all(_to_bool(row.get(field)) is True for field in active_defects):
                matches.append(row)
        return matches

    def _trade_in(self, device_condition: dict[str, Any]) -> dict[str, Any] | None:
        key = self._device_key(device_condition)
        return self.trade_in_lookup.get(key)

    def _repair_cost(self, device_condition: dict[str, Any]) -> dict[str, Any] | None:
        key = self._device_key(device_condition)
        repairs = self.repair_cost_lookup.get(key, [])
        if not repairs:
            return None

        requested: list[str] = []
        if _to_bool(device_condition.get("back_glass_cracked")) is True:
            requested.append("back")
            requested.append("glass")
        if _to_bool(device_condition.get("screen_cracked")) is True or _to_bool(device_condition.get("display_ok")) is False:
            requested.append("screen")
            requested.append("display")

        if not requested:
            return None

        matched = []
        for row in repairs:
            repair_type = str(row.get("repair_type", "")).lower()
            if any(token in repair_type for token in requested):
                price = _to_float(row.get("repair_cost_rm"))
                if price is not None:
                    matched.append({**row, "repair_cost_rm": price})

        if not matched:
            return None
        prices = [row["repair_cost_rm"] for row in matched]
        return {
            "low": int(min(prices)),
            "high": int(max(prices)),
            "evidence_count": len(matched),
            "items": matched,
            "note": "Repair cost is reported separately and is not automatically subtracted from comparable-based market value.",
        }

    def _confidence(
        self,
        exact_count: int,
        condition_count: int,
        exact_defect_count: int,
        condition_group: str,
        requires_exact_defect: bool,
    ) -> str:
        if requires_exact_defect and exact_defect_count < 3:
            return "LOW"
        if condition_count >= 8 and exact_count >= 30:
            return "HIGH"
        if condition_count >= 5 and exact_count >= 20:
            return "MEDIUM"
        if condition_count >= 3 and exact_count >= 15:
            return "MEDIUM" if not requires_exact_defect else "LOW"
        return "LOW"

    def value_device(self, device_condition: dict[str, Any]) -> dict[str, Any]:
        condition_group = self.infer_condition_group(device_condition)
        exact_rows = self._matching_observations(device_condition)
        condition_rows = self._condition_observations(exact_rows, condition_group)
        defect_rows_all = self._defect_observations(exact_rows, device_condition)
        defect_rows_working = [
            row for row in defect_rows_all if row.get("proposed_revalue_condition_group") != "functional_defect"
        ]
        requires_exact_defect = bool(defect_rows_all) or _to_bool(device_condition.get("back_glass_cracked")) is True

        if len(defect_rows_working) >= 5:
            selected_rows = defect_rows_working
            evidence_tier = "exact_defect_comparable"
        elif len(defect_rows_working) >= 3:
            selected_rows = defect_rows_working
            evidence_tier = "limited_exact_defect_comparable"
        elif condition_rows:
            selected_rows = condition_rows
            evidence_tier = "condition_group_comparable"
        else:
            selected_rows = exact_rows
            evidence_tier = "exact_device_fallback"

        prices = [_to_float(row.get("listing_price_rm")) for row in selected_rows]
        price_values = [float(price) for price in prices if price is not None]
        selected_stats = _stats(price_values)

        if selected_stats["count"] == 0:
            raise ValueError("No comparable price evidence is available for this device input.")

        low_raw = selected_stats["q1"] if selected_stats["q1"] is not None else selected_stats["min"]
        high_raw = selected_stats["q3"] if selected_stats["q3"] is not None else selected_stats["max"]
        range_method = "Q1-Q3 robust comparable range"

        if condition_group == "normal_used_b_like" and selected_stats["count"] < 5:
            low_raw = selected_stats["min"]
            high_raw = selected_stats["max"]
            range_method = "limited-evidence min-max range, rounded"

        if condition_group == "damaged_c_like" and len(defect_rows_working) < 5:
            iqr = max(100.0, (selected_stats["q3"] or selected_stats["max"]) - (selected_stats["q1"] or selected_stats["min"]))
            low_raw = max(selected_stats["min"], (selected_stats["q1"] or selected_stats["min"]) - iqr)
            high_raw = min(selected_stats["max"], (selected_stats["q3"] or selected_stats["max"]) + iqr)
            range_method = "condition-group range widened for weak exact-defect evidence"

        low = max(0, _round_rm(float(low_raw), 50))
        high = max(low, _round_rm(float(high_raw), 50))
        midpoint = _round_rm((low + high) / 2, 50)

        repair = self._repair_cost(device_condition)
        trade_in = self._trade_in(device_condition)
        confidence = self._confidence(
            exact_count=len(exact_rows),
            condition_count=len(condition_rows),
            exact_defect_count=len(defect_rows_working),
            condition_group=condition_group,
            requires_exact_defect=_to_bool(device_condition.get("back_glass_cracked")) is True
            or _to_bool(device_condition.get("screen_cracked")) is True,
        )

        drivers = [
            f"Exact model/storage comparables: {len(exact_rows)}",
            f"{CONDITION_LABELS.get(condition_group, condition_group)} comparables: {len(condition_rows)}",
        ]
        if _to_bool(device_condition.get("back_glass_cracked")) is True:
            drivers.append(f"Exact back-glass cracked working comparables: {len(defect_rows_working)}")
        if repair:
            drivers.append(f"Repair evidence kept separate: RM{repair['low']}-{repair['high']}")
        if trade_in:
            drivers.append(f"Trade-in benchmark kept separate: up to RM{trade_in['trade_in_price_rm']}")

        evidence_sources = sorted({row.get("source_url", "") for row in selected_rows if row.get("source_url")})
        if trade_in and trade_in.get("source_url"):
            evidence_sources.append(trade_in["source_url"])
        if repair:
            evidence_sources.extend(row["source_url"] for row in repair["items"] if row.get("source_url"))

        if repair:
            separate_value_note = "Repair cost and trade-in benchmark are shown separately, so damage is not double-counted."
        else:
            separate_value_note = (
                "No repair-cost range is shown because this scenario has no repair-relevant defect; "
                "trade-in benchmark remains separate from consumer resale value."
            )

        explanation = (
            f"Based on {len(condition_rows)} Malaysia {device_condition.get('model')} "
            f"{device_condition.get('storage_gb')}GB {CONDITION_LABELS.get(condition_group, condition_group)} "
            f"comparables, the observed median asking price was approximately "
            f"RM{_round_rm(float(_stats([_to_float(row.get('listing_price_rm')) or 0 for row in condition_rows])['median'] or midpoint), 50)}. "
            f"The selected market range is RM{low:,}-RM{high:,} using {range_method}. "
            f"{separate_value_note}"
        )

        return {
            "device": {
                "brand": device_condition.get("brand"),
                "model": device_condition.get("model"),
                "storage_gb": device_condition.get("storage_gb"),
            },
            "condition_group": condition_group,
            "market_value_range_rm": {"low": low, "high": high},
            "market_value_midpoint_rm": midpoint,
            "trade_in_benchmark_rm": trade_in,
            "repair_cost_range_rm": repair,
            "confidence": confidence,
            "comparable_count": len(exact_rows),
            "condition_matched_count": len(condition_rows),
            "exact_defect_comparable_count": len(defect_rows_working),
            "evidence_tier": evidence_tier,
            "range_method": range_method,
            "main_value_drivers": drivers,
            "evidence_sources": evidence_sources,
            "explanation": explanation,
            "selected_observation_ids": [row.get("observation_id") for row in selected_rows],
        }


def value_device(device_condition: dict[str, Any], calibration_path: str | Path | None = None) -> dict[str, Any]:
    return ReValueMalaysiaValuationEngine(calibration_path).value_device(device_condition)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run ReValue AI Malaysia valuation engine v0.1")
    parser.add_argument("input_json", help="Structured device-condition input JSON")
    parser.add_argument("--calibration", default=None, help="Optional calibration JSON path")
    args = parser.parse_args()

    with open(args.input_json, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    result = value_device(payload, args.calibration)
    print(json.dumps(result, indent=2, ensure_ascii=False))
