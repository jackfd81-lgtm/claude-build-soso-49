"""Deeply immutable ContextEnvelope contract for Cross-Specialty V4 (AUTH:0, newly authored)."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any

from .authority import require_auth0


def deep_freeze(obj: Any) -> Any:
    if isinstance(obj, (dict, MappingProxyType)):
        return MappingProxyType({k: deep_freeze(v) for k, v in obj.items()})
    if isinstance(obj, (list, tuple)):
        return tuple(deep_freeze(v) for v in obj)
    return obj


def to_plain(obj: Any) -> Any:
    if isinstance(obj, (dict, MappingProxyType)):
        return {k: to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_plain(v) for v in obj]
    return obj


@dataclass(frozen=True)
class ContextEnvelope:
    investigation_id: str
    originating_question: str
    originating_specialty: str
    current_specialty: str
    consultation_purpose: str
    required_counterpart: tuple[str, ...] = ()
    required_intermediaries: tuple[str, ...] = ("L33", "U32", "L34", "U33")
    evidence_store: MappingProxyType | dict[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    evidence_refs: tuple[Any, ...] = ()
    interpretations: tuple[Any, ...] = ()
    conflicts: tuple[Any, ...] = ()
    coherence_status: str = "PENDING"
    execution_trace: tuple[Any, ...] = ()
    visited_specialties: tuple[str, ...] = ()
    authority: int = 0
    feature_enabled: bool = True

    def __post_init__(self):
        require_auth0(self.authority)
        for name in ("investigation_id", "originating_question", "originating_specialty", "consultation_purpose"):
            val = getattr(self, name)
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"{name} must be a non-empty string")
        object.__setattr__(self, "required_counterpart", deep_freeze(self.required_counterpart))
        object.__setattr__(self, "required_intermediaries", deep_freeze(self.required_intermediaries))
        object.__setattr__(self, "evidence_store", deep_freeze(self.evidence_store))
        object.__setattr__(self, "evidence_refs", deep_freeze(self.evidence_refs))
        object.__setattr__(self, "interpretations", deep_freeze(self.interpretations))
        object.__setattr__(self, "conflicts", deep_freeze(self.conflicts))
        object.__setattr__(self, "execution_trace", deep_freeze(self.execution_trace))
        visited = self.visited_specialties or (self.originating_specialty,)
        object.__setattr__(self, "visited_specialties", deep_freeze(visited))

    def with_step(
        self,
        *,
        current_specialty: str | None = None,
        interpretation: dict[str, Any] | None = None,
        conflict: dict[str, Any] | None = None,
        coherence_status: str | None = None,
        trace_entry: dict[str, Any] | None = None,
        visited_specialty: str | None = None,
    ) -> "ContextEnvelope":
        return replace(
            self,
            current_specialty=current_specialty or self.current_specialty,
            interpretations=self.interpretations + ((deep_freeze(interpretation),) if interpretation else ()),
            conflicts=self.conflicts + ((deep_freeze(conflict),) if conflict else ()),
            coherence_status=coherence_status or self.coherence_status,
            execution_trace=self.execution_trace + ((deep_freeze(trace_entry),) if trace_entry else ()),
            visited_specialties=self.visited_specialties + ((visited_specialty,) if visited_specialty else ()),
        )
