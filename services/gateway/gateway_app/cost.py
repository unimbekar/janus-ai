"""Estimate per-request cost from registry metadata.

Phase 3 uses cost_class heuristics until provider price tables are populated in
the registry database. Numbers are conservative placeholders — not published
benchmarks.
"""

from __future__ import annotations

from decimal import Decimal

from janus_schemas.chat import Usage
from janus_schemas.common import CostClass

from gateway_app.registry.records import ModelRecord

# USD per 1M tokens (rough midpoints for routing penalties, not invoicing)
_INPUT_PER_M: dict[CostClass, Decimal] = {
    CostClass.FREE: Decimal("0"),
    CostClass.LOW: Decimal("0.15"),
    CostClass.MEDIUM: Decimal("0.60"),
    CostClass.HIGH: Decimal("3.00"),
    CostClass.FIXED: Decimal("15.00"),
}

_OUTPUT_PER_M: dict[CostClass, Decimal] = {
    CostClass.FREE: Decimal("0"),
    CostClass.LOW: Decimal("0.30"),
    CostClass.MEDIUM: Decimal("1.20"),
    CostClass.HIGH: Decimal("6.00"),
    CostClass.FIXED: Decimal("30.00"),
}


def estimate_cost_usd(model: ModelRecord, usage: Usage) -> Decimal:
    input_rate = _INPUT_PER_M.get(model.cost_class, Decimal("0.60"))
    output_rate = _OUTPUT_PER_M.get(model.cost_class, Decimal("1.20"))
    prompt = Decimal(usage.prompt_tokens) / Decimal(1_000_000)
    completion = Decimal(usage.completion_tokens) / Decimal(1_000_000)
    return (prompt * input_rate + completion * output_rate).quantize(Decimal("0.00000001"))
