"""Independent Grader for HIST-GEO-TIME-105-v1 (Structural + Chronology + Geography + Historical Continuity)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Expected domain/geographical keywords per segment (13 segments between the 14 anchors)
SEGMENT_CONTEXT_KEYWORDS: list[tuple[int, int, str, tuple[str, ...]]] = [
    (2, 8, "Athens 508 BCE -> Rome 44 BCE", ("Cleisthenes", "Athens", "Greek", "Acropolis", "Rome", "Roman", "Caesar", "Rubicon", "Assembly")),
    (10, 16, "Rome 44 BCE -> Chang'an 618 CE", ("Caesar", "Roman", "Augustus", "Principate", "Sui", "Tang", "Chang’an", "Li Yuan")),
    (18, 24, "Chang'an 618 CE -> Baghdad 762 CE", ("Tang", "Central Asia", "Chang’an", "Abbasid", "al-Mansur", "Tigris", "Madinat al-Salam")),
    (26, 32, "Baghdad 762 CE -> Kyoto 794 CE", ("Abbasid", "Baghdad", "Mesopotamia", "Paper", "Japan", "Nara", "Kanmu", "Heian-kyo", "palaces")),
    (34, 40, "Kyoto 794 CE -> Tenochtitlan 1325 CE", ("Heian", "warrior", "Japan", "Mesoamerica", "Mexica", "Lake Texcoco", "Causeways", "Chinampa")),
    (42, 48, "Tenochtitlan 1325 CE -> Constantinople 1453 CE", ("Tenochtitlan", "Triple Alliance", "Ottoman", "Byzantine", "Mehmed", "Golden Horn", "Theodosian")),
    (50, 56, "Constantinople 1453 CE -> Calicut 1498 CE", ("Ottoman", "Constantinople", "Mediterranean", "Portuguese", "Gama", "Indian Ocean", "Malabar", "Zamorin")),
    (58, 64, "Calicut 1498 CE -> Wittenberg 1517 CE", ("Portuguese", "Indian Ocean", "European", "German", "Saxony", "Luther", "indulgences", "Ninety-five")),
    (66, 72, "Wittenberg 1517 CE -> Philadelphia 1776 CE", ("Reformation", "North America", "colonial", "Continental Congress", "Lexington", "Jefferson", "July")),
    (74, 80, "Philadelphia 1776 CE -> Paris 1789 CE", ("American", "France", "Louis XVI", "Estates-General", "National Assembly", "Paris", "Bastille", "Rights of Man")),
    (82, 88, "Paris 1789 CE -> Haiti 1804 CE", ("Caribbean", "Saint-Domingue", "Louverture", "France", "French", "Napoleon", "Dessalines", "Vertières", "republic")),
    (90, 96, "Haiti 1804 CE -> Sarajevo 1914 CE", ("Haiti", "European", "Industrialized", "Austria-Hungary", "Bosnia", "Balkan", "Franz Ferdinand", "Princip")),
    (98, 104, "Sarajevo 1914 CE -> Berlin 1989 CE", ("July Crisis", "First World War", "Europe", "Germany", "Cold War", "Berlin Wall", "East German", "November", "concrete")),
]


def grade_105_response(response_path: Path, challenge_path: Path) -> dict[str, Any]:
    challenge = json.loads(Path(challenge_path).read_text(encoding="utf-8"))
    response = json.loads(Path(response_path).read_text(encoding="utf-8"))
    by_pos = {item["position"]: item["text"] for item in response.get("positions", [])}

    per_position: list[dict[str, Any]] = []
    structural_failures = 0
    historical_failures = 0

    # Check structural + anchor preservation
    for spec in challenge["positions"]:
        pos = spec["position"]
        kind = spec["kind"]
        text = by_pos.get(pos)
        if not isinstance(text, str) or not text.strip():
            structural_failures += 1
            per_position.append({"position": pos, "kind": kind, "status": "FAIL", "reason": "missing_text"})
            continue
        words = len(text.split())
        if kind == "ANCHOR":
            ok = text == spec["text"]
            reason = None if ok else "anchor_modified"
        else:
            ok = 4 <= words <= 9
            reason = None if ok else f"word_count_{words}_outside_4_9"
        if not ok:
            structural_failures += 1
        per_position.append({"position": pos, "kind": kind, "words": words, "status": "PASS" if ok else "FAIL", "reason": reason})

    # Check segment-level historical/geographical/chronological grounding
    segment_reports: list[dict[str, Any]] = []
    for start, end, label, keywords in SEGMENT_CONTEXT_KEYWORDS:
        seg_texts = [by_pos.get(p, "") for p in range(start, end + 1)]
        joined = " ".join(seg_texts)
        matched = [kw for kw in keywords if kw in joined]
        grounded_lines = sum(1 for t in seg_texts if any(kw in t for kw in keywords))
        ok = grounded_lines == (end - start + 1)
        if not ok:
            historical_failures += 1
        segment_reports.append({
            "segment": label,
            "positions": f"{start}..{end}",
            "grounded_lines": grounded_lines,
            "total_lines": end - start + 1,
            "matched_keywords": matched,
            "status": "PASS" if ok else "FAIL",
        })

    return {
        "challenge_id": challenge["id"],
        "total_positions": len(per_position),
        "structural_passes": len(per_position) - structural_failures,
        "structural_failures": structural_failures,
        "segment_historical_passes": len(segment_reports) - historical_failures,
        "segment_historical_total": len(segment_reports),
        "overall_status": "PASS" if (structural_failures == 0 and historical_failures == 0) else "FAIL",
        "segment_reports": segment_reports,
        "per_position": per_position,
    }
