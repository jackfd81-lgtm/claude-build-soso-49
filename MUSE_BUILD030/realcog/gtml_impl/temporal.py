"""Append-only bitemporal ledger for exp-impl-0.2.0-hardening.

EXPERIMENTAL — NOT HISTORICALLY AUTHENTICATED. Authority ZERO.

The LEDGER MECHANISM here (Ledger, LedgerEntry, replay semantics) is
EXPERIMENTAL DEFINITION — NOT A BASELINE LAW. It implements the bitemporal
principle (baseline laws T-1: independent event/knowledge time; T-2:
immutable historical record with replay preserving what was known at ledger
time), which is RECOVERED per the four-domain baseline (time domain).

Stdlib only. Deterministic. Fail-closed: typed TemporalError, never silent.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .ir import Record


class TemporalError(Exception):
    """Fail-closed temporal operation error."""


@dataclass(frozen=True)
class LedgerEntry:
    """One immutable ledger entry.

    seq: monotonically increasing append sequence (starts at 1).
    knowledge_time: when this was recorded (ledger time T_k). Non-empty.
    event_time: when the recorded event occurred (T_e); None if unknown.
    payload: the IR Record as known at knowledge_time.
    supersedes: seq of a prior entry this entry supersedes, or None.
      Supersession is a recorded claim; it never deletes or edits the
      superseded entry.
    """
    seq: int
    knowledge_time: str
    event_time: Optional[str]
    payload: Record
    supersedes: Optional[int] = None

    def __post_init__(self) -> None:
        if not isinstance(self.seq, int) or self.seq < 1:
            raise TemporalError("seq must be a positive int")
        if not isinstance(self.knowledge_time, str) or not self.knowledge_time:
            raise TemporalError("knowledge_time must be a non-empty string")
        if self.event_time is not None and (
                not isinstance(self.event_time, str) or not self.event_time):
            raise TemporalError("event_time must be a non-empty string or None")
        if not isinstance(self.payload, Record):
            raise TemporalError("payload must be an IR Record")
        if self.supersedes is not None and (
                not isinstance(self.supersedes, int) or self.supersedes < 1):
            raise TemporalError("supersedes must be a positive int or None")


class Ledger:
    """Append-only bitemporal ledger. EXPERIMENTAL mechanism.

    Entries are immutable (frozen dataclasses; no update/delete methods
    exist on this class). replay(T_k) returns the ledger state as known at
    knowledge time T_k: every entry with entry.knowledge_time <= T_k, in seq
    order. Later entries are invisible to replay at an earlier T_k.

    knowledge_time comparison is lexicographic on the stored strings;
    callers MUST use a consistent timestamp format (ISO-8601) for ordering
    to be meaningful. The class is deterministic: replay output order is
    always seq order, and append assigns seq deterministically.
    """

    def __init__(self) -> None:
        self._entries: Dict[int, LedgerEntry] = {}
        self._order: List[int] = []
        self._next_seq: int = 1

    def append(self, knowledge_time: str, event_time: Optional[str],
               payload: Record, supersedes: Optional[int] = None) -> int:
        """Append a new entry. Returns the assigned seq.

        EXPERIMENTAL DEFINITION — NOT A BASELINE LAW.

        Fail-closed: empty knowledge_time, non-Record payload, or a
        supersedes value that does not reference an existing seq raises
        TemporalError. The ledger is append-only: there is no way to alter
        or remove an entry once appended.
        """
        if not isinstance(knowledge_time, str) or not knowledge_time:
            raise TemporalError("knowledge_time must be a non-empty string")
        if supersedes is not None and supersedes not in self._entries:
            raise TemporalError(
                f"supersedes references unknown seq: {supersedes!r}")
        entry = LedgerEntry(
            seq=self._next_seq,
            knowledge_time=knowledge_time,
            event_time=event_time,
            payload=payload,
            supersedes=supersedes,
        )
        self._entries[entry.seq] = entry
        self._order.append(entry.seq)
        self._next_seq += 1
        return entry.seq

    def entries(self, seq: int) -> LedgerEntry:
        """Return the entry with the given seq. Fail-closed on unknown seq."""
        try:
            return self._entries[seq]
        except KeyError:
            raise TemporalError(f"no ledger entry with seq {seq!r}") from None

    def replay(self, knowledge_time: str) -> Tuple[LedgerEntry, ...]:
        """State of the ledger as known at knowledge time T_k.

        Returns every entry with entry.knowledge_time <= T_k, in seq order.
        Entries recorded after T_k are invisible. Deterministic: output
        order is always seq order, regardless of input patterns.
        """
        if not isinstance(knowledge_time, str) or not knowledge_time:
            raise TemporalError("knowledge_time must be a non-empty string")
        return tuple(
            self._entries[seq]
            for seq in self._order
            if self._entries[seq].knowledge_time <= knowledge_time
        )

    def __len__(self) -> int:
        return len(self._order)
