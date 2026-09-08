from __future__ import annotations

from .schemas import GradeResult, MergedDamage


SEVERE_DAMAGE_TYPES = {
    "screen_crack",
    "back_glass_crack",
    "camera_glass_damage",
    "camera_island_impact",
}

MODERATE_DAMAGE_TYPES = {
    "deep_scratch",
    "frame_dent",
    "corner_chip",
    "other_visible_damage",
}


def grade_from_atomic_damage(damage: list[MergedDamage]) -> GradeResult:
    """Apply deterministic exterior grading to atomic damage observations.

    Demo grades are not connected to this function. This module is for the
    future live path after validated damage rules are approved.
    """

    eligible_damage = [
        item
        for item in damage
        if item.verification == "confirmed" and item.grade_affecting
    ]

    if not eligible_damage:
        return GradeResult(
            exterior_grade="A",
            reason_codes=["no_confirmed_grade_affecting_damage"],
            note="No confirmed grade-affecting exterior damage was supplied to the grading module.",
        )

    if any(item.damage_type in SEVERE_DAMAGE_TYPES and item.severity == "severe" for item in eligible_damage):
        return GradeResult(
            exterior_grade="C-",
            reason_codes=["severe_primary_surface_damage"],
            note="Severe visible damage requires the lowest exterior prototype grade pending final rules.",
        )

    if any(item.damage_type in SEVERE_DAMAGE_TYPES for item in eligible_damage):
        return GradeResult(
            exterior_grade="C",
            reason_codes=["primary_surface_damage"],
            note="Visible crack or camera glass damage maps to damaged-working exterior condition.",
        )

    if any(item.damage_type in MODERATE_DAMAGE_TYPES for item in eligible_damage):
        return GradeResult(
            exterior_grade="B",
            reason_codes=["visible_wear_without_major_crack"],
            note="Visible wear is present, but no major crack was supplied.",
        )

    return GradeResult(
        exterior_grade="B",
        reason_codes=["minor_surface_marks"],
        note="Only minor marks were supplied.",
    )
