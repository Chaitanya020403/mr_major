"""
Plan checker — validates a plan against the gold constraints without using Z3.

The referee is written in plain Python so it is independent of both systems
being evaluated. Plans are always checked against gold constraints, not against
the LLM's translation.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from core.schema import Order, Vehicle, VALID_SLOTS, VEHICLE_TYPE_NEEDS, FRAGILE_MAX_LOAD, AnyConstraint
from core.schema import (
    PrecedenceConstraint,
    ForbiddenVehicleConstraint,
    RequiredVehicleConstraint,
    ForbiddenSlotConstraint,
    VehicleUnavailableConstraint,
    DeadlineConstraint,
    TimeWindowConstraint,
    UrgentDeliveryConstraint,
    ChangeDeadlineConstraint,
)


@dataclass
class CheckResult:
    valid: bool
    violations: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        if self.valid:
            return "VALID"
        return "INVALID:\n" + "\n".join(f"  - {v}" for v in self.violations)


def check_plan(
    plan: dict[str, tuple[str, str]],   # order_id → (vehicle_id, slot)
    orders: list[Order],
    vehicles: list[Vehicle],
    gold_constraints: list[AnyConstraint],
) -> CheckResult:
    violations: list[str] = []
    order_map  = {o.id: o for o in orders}
    vehicle_map = {v.id: v for v in vehicles}
    slot_idx = {s: i for i, s in enumerate(VALID_SLOTS)}

    def slot_of(oid: str) -> str:
        return plan[oid][1]

    # Collect deadline overrides from user constraints
    deadline_overrides: dict[str, str] = {}
    for uc in gold_constraints:
        if isinstance(uc, ChangeDeadlineConstraint):
            deadline_overrides[uc.order] = uc.new_deadline
        elif isinstance(uc, DeadlineConstraint):
            deadline_overrides[uc.order] = uc.slot

    # R1: every order assigned
    for o in orders:
        if o.id not in plan:
            violations.append(f"R1: order {o.id} not assigned")

    for oid, (vid, slot) in plan.items():
        o = order_map.get(oid)
        v = vehicle_map.get(vid)
        if o is None:
            violations.append(f"Unknown order ID in plan: {oid}")
            continue
        if v is None:
            violations.append(f"Unknown vehicle ID in plan: {vid}")
            continue
        if slot not in VALID_SLOTS:
            violations.append(f"Unknown slot in plan: {slot}")
            continue

        # R3: availability
        if slot not in v.available_slots:
            violations.append(f"R3: vehicle {vid} not available at {slot} (order {oid})")

        # R4: delivery window (user CHANGE_DEADLINE/DEADLINE overrides order data)
        effective_deadline = deadline_overrides.get(oid, o.deadline)
        if effective_deadline and slot_idx[slot] > slot_idx[effective_deadline]:
            violations.append(f"R4: order {oid} deadline {effective_deadline} missed (dispatched {slot})")
        if o.time_window and oid not in deadline_overrides:
            early, late = o.time_window
            if slot_idx[slot] < slot_idx[early] or slot_idx[slot] > slot_idx[late]:
                violations.append(f"R4: order {oid} outside time window [{early},{late}] at {slot}")

        # R5: compatibility
        for need in o.needs:
            if need in VEHICLE_TYPE_NEEDS and need not in v.type:
                violations.append(f"R5: order {oid} needs '{need}' but vehicle {vid} is '{v.type}'")

        # R6: urgent
        if o.priority == "urgent" and o.deadline:
            if slot_idx[slot] > slot_idx[o.deadline]:
                violations.append(f"R6: urgent order {oid} missed deadline {o.deadline}")

    # R2: capacity per vehicle per slot
    load: dict[tuple[str, str], float] = {}
    for oid, (vid, slot) in plan.items():
        key = (vid, slot)
        load[key] = load.get(key, 0) + order_map[oid].weight
    for (vid, slot), w in load.items():
        cap = vehicle_map[vid].capacity
        if w > cap:
            violations.append(f"R2: {vid}@{slot} overloaded ({w:.0f} kg > {cap:.0f} kg)")

    # R9: fragile handling per vehicle per slot
    groups: dict[tuple[str, str], list[Order]] = {}
    for oid, (vid, slot) in plan.items():
        groups.setdefault((vid, slot), []).append(order_map[oid])
    for (vid, slot), group in groups.items():
        fragile = [o.id for o in group if "fragile" in o.needs]
        if not fragile:
            continue
        limit = FRAGILE_MAX_LOAD * vehicle_map[vid].capacity
        if load[(vid, slot)] > limit:
            violations.append(
                f"R9: {vid}@{slot} carries fragile {', '.join(fragile)} but is loaded "
                f"{load[(vid, slot)]:.0f} kg > {limit:.0f} kg ({FRAGILE_MAX_LOAD:.0%})"
            )
        oversized = [o.id for o in group if "oversized" in o.needs and o.id not in fragile]
        if oversized:
            violations.append(
                f"R9: {vid}@{slot} carries fragile {', '.join(fragile)} together with "
                f"oversized {', '.join(oversized)}"
            )

    # Gold user constraints
    for uc in gold_constraints:
        if isinstance(uc, PrecedenceConstraint):
            if uc.before in plan and uc.after in plan:
                if slot_idx[slot_of(uc.before)] >= slot_idx[slot_of(uc.after)]:
                    violations.append(
                        f"R7/USER: {uc.before} ({slot_of(uc.before)}) must be before "
                        f"{uc.after} ({slot_of(uc.after)})"
                    )

        elif isinstance(uc, ForbiddenVehicleConstraint):
            if uc.order in plan and plan[uc.order][0] == uc.vehicle:
                violations.append(f"R8/USER: order {uc.order} must not use vehicle {uc.vehicle}")

        elif isinstance(uc, RequiredVehicleConstraint):
            if uc.order in plan and plan[uc.order][0] != uc.vehicle:
                violations.append(f"R8/USER: order {uc.order} must use vehicle {uc.vehicle}")

        elif isinstance(uc, ForbiddenSlotConstraint):
            if uc.order in plan and plan[uc.order][1] == uc.slot:
                violations.append(f"R8/USER: order {uc.order} must not use slot {uc.slot}")

        elif isinstance(uc, (DeadlineConstraint, ChangeDeadlineConstraint)):
            oid = uc.order
            dl = uc.slot if isinstance(uc, DeadlineConstraint) else uc.new_deadline
            if oid in plan and slot_idx[slot_of(oid)] > slot_idx[dl]:
                violations.append(f"USER deadline: order {oid} must leave by {dl}")

        elif isinstance(uc, UrgentDeliveryConstraint):
            if uc.order in plan and slot_idx[slot_of(uc.order)] > slot_idx[uc.deadline]:
                violations.append(f"USER urgent: order {uc.order} must leave by {uc.deadline}")

        elif isinstance(uc, VehicleUnavailableConstraint):
            for oid, (vid, slot) in plan.items():
                if vid == uc.vehicle and slot == uc.slot:
                    violations.append(f"R8/USER: vehicle {vid} unavailable at {slot} (order {oid})")

        elif isinstance(uc, TimeWindowConstraint):
            if uc.order in plan:
                s = slot_idx[slot_of(uc.order)]
                if s < slot_idx[uc.earliest] or s > slot_idx[uc.latest]:
                    violations.append(
                        f"USER window: order {uc.order} must leave between {uc.earliest} "
                        f"and {uc.latest} (dispatched {slot_of(uc.order)})"
                    )

    return CheckResult(valid=len(violations) == 0, violations=violations)
