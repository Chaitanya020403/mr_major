"""
Validates the JSON list produced by the LLM translator against the constraint schema.

Checks:
- Valid JSON (caller must parse before passing)
- Known constraint types
- All referenced order/vehicle/slot IDs exist in the loaded data
- Required fields present (delegated to pydantic)
"""

from __future__ import annotations
from typing import Any

from pydantic import ValidationError

from core.schema import (
    CONSTRAINT_CLASSES,
    AnyConstraint,
    VALID_SLOTS,
    Order,
    Vehicle,
)


class JSONValidationResult:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.constraints: list[AnyConstraint] = []

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def __str__(self) -> str:
        return "\n".join(self.errors) if self.errors else "OK"


def validate_constraints(
    raw: list[dict[str, Any]],
    orders: list[Order],
    vehicles: list[Vehicle],
) -> JSONValidationResult:
    result = JSONValidationResult()
    order_ids = {o.id for o in orders}
    vehicle_ids = {v.id for v in vehicles}

    for i, item in enumerate(raw):
        ctype = item.get("type")
        if ctype not in CONSTRAINT_CLASSES:
            result.errors.append(
                f"constraints[{i}]: unknown type '{ctype}'. "
                f"Valid types: {sorted(CONSTRAINT_CLASSES)}"
            )
            continue

        cls = CONSTRAINT_CLASSES[ctype]
        try:
            constraint = cls.model_validate(item)
        except ValidationError as e:
            result.errors.append(f"constraints[{i}] ({ctype}): {e}")
            continue

        # Cross-reference IDs
        for attr in ("order", "before", "after"):
            oid = getattr(constraint, attr, None)
            if oid and oid not in order_ids:
                result.errors.append(
                    f"constraints[{i}] ({ctype}): unknown order ID '{oid}'"
                )

        vid = getattr(constraint, "vehicle", None)
        if vid and vid not in vehicle_ids:
            result.errors.append(
                f"constraints[{i}] ({ctype}): unknown vehicle ID '{vid}'"
            )

        for slot_attr in ("slot", "earliest", "latest", "deadline", "new_deadline"):
            s = getattr(constraint, slot_attr, None)
            if s and s not in VALID_SLOTS:
                result.errors.append(
                    f"constraints[{i}] ({ctype}): unknown slot '{s}'"
                )

        result.constraints.append(constraint)

    return result
