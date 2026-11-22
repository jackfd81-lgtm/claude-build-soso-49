"""Unified 98-Function (Lower 49 + Higher 49) Wiring & Region Dispatcher.

Wires all 49 Lower (L01-L49) and 49 Higher (U01-U49) slots into one coherent
AUTH:0 dispatcher backed by the 7 canonical 49-Brains regions, distinguishing
CORE_SPECIALIZED functions (15) from REGION_ROUTED slots (83) without
misrepresenting region-routed slots as independent specialized solvers.
"""
from __future__ import annotations

from dataclasses import dataclass
from .authority import require_task
from typing import Any, Callable


REGIONS: dict[int, tuple[int, int, str]] = {
    1: (1, 7, "EVIDENCE_INTERFACE"),
    2: (8, 14, "TOK_REPRESENTATION"),
    3: (15, 21, "RELATIONSHIP_CORTEX"),
    4: (22, 28, "TEMPORAL_CONTINUITY"),
    5: (29, 35, "INVESTIGATION_NAVIGATION"),
    6: (36, 42, "AUDIT_METACOGNITION"),
    7: (43, 49, "AI_CLOSET_GATES"),
}

CORE_LOWER_SPECIALIZED = frozenset({"L01", "L02", "L15", "L21", "L24", "L30"})
CORE_HIGHER_SPECIALIZED = frozenset({"U01", "U02", "U03", "U04", "U05", "U08", "U09", "U10", "U37"})


def region_for_index(idx: int) -> tuple[int, str]:
    if not 1 <= idx <= 49:
        raise ValueError(f"Function index out of range 1..49: {idx}")
    reg_id = (idx - 1) // 7 + 1
    return reg_id, REGIONS[reg_id][2]


@dataclass(frozen=True)
class WiredFunction:
    function_id: str
    layer: str  # "LOWER_49" or "HIGHER_49"
    region_id: int
    region_name: str
    implementation_tier: str  # "CORE_SPECIALIZED" or "REGION_ROUTED"
    handler: Callable[[dict[str, Any]], dict[str, Any]]


class BrainsWiringRegistry:
    """Complete 98-slot (L01-L49, U01-U49) AUTH:0 dispatcher."""

    def __init__(self, trace_fn: Callable[[str, dict[str, Any]], None] | None = None,
                 *, operations: dict[str, Callable[..., Any]] | None = None):
        self._trace_fn = trace_fn
        self._registry: dict[str, WiredFunction] = {}
        self._build_registry()
        self._operations = dict(operations or {})
        if self._operations.keys() - self._registry.keys():
            raise ValueError("unknown operation binding")

    def _default_region_handler(self, fid: str, layer: str, reg_id: int, reg_name: str, tier: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
        def _run(payload: dict[str, Any]) -> dict[str, Any]:
            require_task(payload)
            result = {
                "function_id": fid,
                "layer": layer,
                "region_id": reg_id,
                "region_name": reg_name,
                "implementation_tier": tier,
                "authority": 0,
                "status": "OK",
                "task_id": payload.get("task_id"),
            }
            if self._trace_fn is not None:
                self._trace_fn(fid, result)
            return result
        return _run

    def _build_registry(self) -> None:
        for i in range(1, 50):
            reg_id, reg_name = region_for_index(i)
            lid = f"L{i:02d}"
            ltier = "CORE_SPECIALIZED" if lid in CORE_LOWER_SPECIALIZED else "REGION_ROUTED"
            self._registry[lid] = WiredFunction(
                function_id=lid,
                layer="LOWER_49",
                region_id=reg_id,
                region_name=reg_name,
                implementation_tier=ltier,
                handler=self._default_region_handler(lid, "LOWER_49", reg_id, reg_name, ltier),
            )

            uid = f"U{i:02d}"
            utier = "CORE_SPECIALIZED" if uid in CORE_HIGHER_SPECIALIZED else "REGION_ROUTED"
            self._registry[uid] = WiredFunction(
                function_id=uid,
                layer="HIGHER_49",
                region_id=reg_id,
                region_name=reg_name,
                implementation_tier=utier,
                handler=self._default_region_handler(uid, "HIGHER_49", reg_id, reg_name, utier),
            )

    def invoke(self, function_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if function_id not in self._registry:
            raise KeyError(f"Unknown 49-Brains function: {function_id}")
        return self._registry[function_id].handler(payload)

    def execute_operation(self, function_id: str, payload: dict[str, Any], *args):
        """Execute a bound operation. An unbound registration is never substituted."""
        require_task(payload)
        return self._operations[function_id](*args)

    def summary(self) -> dict[str, Any]:
        lower = [v for v in self._registry.values() if v.layer == "LOWER_49"]
        higher = [v for v in self._registry.values() if v.layer == "HIGHER_49"]
        return {
            "total_wired": len(self._registry),
            "lower_49_wired": len(lower),
            "higher_49_wired": len(higher),
            "core_specialized": sum(1 for v in self._registry.values() if v.implementation_tier == "CORE_SPECIALIZED"),
            "region_routed": sum(1 for v in self._registry.values() if v.implementation_tier == "REGION_ROUTED"),
            "regions": 7,
        }
