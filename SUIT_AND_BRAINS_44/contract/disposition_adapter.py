"""Named adapter for U09's dedicated Higher49 runtime route.

U09 (RC1 Motor Disposition) is not in CROSS_OPERATIONS and generic
``BrainsWiringRegistry.invoke`` does not execute it. Its supported runtime
route is ``Brains49Runtime.dispatch_higher49``'s U09 branch, which now calls
this adapter. The adapter wraps
``contract.disposition_engine.evaluate_motor_branches`` and preserves its
behaviour exactly: same input, same events, same PASS / DROP / HOLD
dispositions. It exists so the typed dispatch (``contract.slot_specs``) and
the runtime share one named entry point for U09 instead of two inline calls.
"""
from __future__ import annotations

from typing import Any, Mapping

from contract.disposition_engine import evaluate_motor_branches

ADAPTER_ID = "contract.disposition_adapter.evaluate_u09"


def evaluate_u09(
    observation: Mapping[str, Any],
    execution_id: str = "exec-motor-001",
    *,
    causal_parent_id: str | None = None,
) -> list[dict[str, Any]]:
    """Evaluate a motor observation through U09 and return its events.

    ``observation`` must carry ``obs_id`` and a ``facts`` mapping, exactly as
    ``evaluate_motor_branches`` requires. A missing ``obs_id`` raises
    ``KeyError``; the typed dispatch converts that to a REJECTED disposition
    with code MISSING_FIELD instead of letting it escape.
    """
    return evaluate_motor_branches(
        observation, execution_id=execution_id, causal_parent_id=causal_parent_id
    )
