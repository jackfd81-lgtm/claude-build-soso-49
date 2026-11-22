"""Dual-Stream Visual Evidence Adapter for SoSo / RealCog (Directive 011).

Strictly separates:
  - Stream A: Deterministic Python pixel/EXIF/hash measurements.
  - Stream B: Source-attributed Gemini multimodal visual observations.

Never promotes Stream B semantic observations into verified Stream A measurements.
"""
from __future__ import annotations

from typing import Any, Mapping

from .disposition_engine import make_disposition_event


def ingest_dual_stream(
    stream_a: Mapping[str, Any],
    stream_b: Mapping[str, Any],
    *,
    execution_id: str = "exec-vis-011",
    authority: int = 0,
) -> dict[str, Any]:
    """Validate provenance and reconcile Stream A measurements with Stream B observations."""
    if authority != 0 or isinstance(authority, bool):
        raise PermissionError(f"AUTH:0 violation: nonzero or invalid authority {authority!r} rejected")

    lum = float(stream_a["mean_lum_rec709"])
    lighting = str(stream_b.get("lighting_condition") or "unknown")
    is_composite = bool(stream_b.get("is_composite_screenshot", False))
    exif_avail = bool(stream_a.get("exif_available", False))

    conflicts: list[str] = []
    missing_evidence: list[str] = []

    # Detect measurement vs semantic lighting conflicts (Test B)
    if lum < 70.0 and "daylight" in lighting:
        conflicts.append(f"LOW_LUMINANCE_{lum}_CONTRADICTS_{lighting}")
    if lum > 110.0 and "nighttime" in lighting:
        conflicts.append(f"HIGH_LUMINANCE_{lum}_CONTRADICTS_{lighting}")
    if is_composite and stream_b.get("claimed_single_exposure"):
        conflicts.append("COMPOSITE_SCREENSHOT_CANNOT_BE_SINGLE_EXPOSURE")

    if not exif_avail:
        missing_evidence.append("exif_timestamp_and_iso")

    if conflicts:
        disp = make_disposition_event(
            event_id=f"{execution_id}-RECON-DROP",
            source_function="U09",
            destination_function="L24_CONFLICT",
            input_reference=str(stream_a["filename"]),
            disposition="DROP",
            reason="STREAM_A_STREAM_B_CONFLICT",
            result={
                "conflicts": conflicts,
                "stream_a_preserved": {"sha256": stream_a["sha256"], "mean_lum_rec709": lum},
                "stream_b_preserved": {"source": stream_b.get("source"), "lighting_condition": lighting},
                "missing_evidence": missing_evidence + ["reconciled_lighting_or_exif"],
                "recommended_next_operation": "REQUEST_DISCRIMINATING_EVIDENCE",
            },
            authority=0,
            execution_id=execution_id,
        )
    elif missing_evidence:
        disp = make_disposition_event(
            event_id=f"{execution_id}-RECON-HOLD",
            source_function="U09",
            destination_function="U10_TEMPORAL",
            input_reference=str(stream_a["filename"]),
            disposition="HOLD",
            reason="MISSING_EXIF_CHRONOLOGY_AND_ISO",
            result={
                "finding": "Scene analyzed; exact chronological order and ISO underdetermined without EXIF",
                "pending_conditions": missing_evidence,
                "recommended_next_operation": "ABSTAIN_ON_EXACT_CHRONOLOGY",
            },
            authority=0,
            execution_id=execution_id,
        )
    else:
        disp = make_disposition_event(
            event_id=f"{execution_id}-RECON-PASS",
            source_function="U09",
            destination_function="U37_VISUAL_AUDIT",
            input_reference=str(stream_a["filename"]),
            disposition="PASS",
            reason="STREAMS_RECONCILED",
            result={"recommended_next_operation": "COMMIT_VISUAL_AUDIT"},
            authority=0,
            execution_id=execution_id,
        )

    return {
        "schema": "muse-dual-stream-visual-evidence/1.0",
        "filename": stream_a["filename"],
        "stream_a_provenance": "python_deterministic_measurements",
        "stream_b_provenance": stream_b.get("source", "gemini_multimodal_vision"),
        "conflicts": conflicts,
        "missing_evidence": missing_evidence,
        "disposition_event": disp,
        "native_soso_vision_status": "UNSUPPORTED",
    }


def classify_cross_image_relationship(
    img_a: tuple[Mapping[str, Any], Mapping[str, Any]],
    img_b: tuple[Mapping[str, Any], Mapping[str, Any]],
) -> dict[str, Any]:
    """Distinguish exact SHA-256 duplicate, composite screenshot, same-location cross-lighting, and unrelated."""
    a_meas, a_sem = img_a
    b_meas, b_sem = img_b

    if a_meas["sha256"] == b_meas["sha256"]:
        rel_type = "EXACT_BYTE_DUPLICATE"
        same_event_assumed = False  # Identical file bytes, not two independent captures
    elif a_sem.get("is_composite_screenshot") or b_sem.get("is_composite_screenshot"):
        rel_type = "COMPOSITE_SCREENSHOT_ARTIFACT"
        same_event_assumed = False
    elif a_sem.get("scene_cluster") == b_sem.get("scene_cluster"):
        rel_type = "SAME_LOCATION_DIFFERENT_CONDITIONS"
        same_event_assumed = False  # Do not assume same event or chronology from brightness alone
    else:
        rel_type = "UNRELATED_SCENES"
        same_event_assumed = False

    return {
        "image_a": a_meas["filename"],
        "image_b": b_meas["filename"],
        "relationship_type": rel_type,
        "same_event_assumed": same_event_assumed,
        "chronology_inferred_from_brightness": False,
    }
