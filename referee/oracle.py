"""
Ground-truth oracle for UNSAT verification.

Two independent methods:
1. Backtracking search — exhaustive for small cases (up to ~12-15 orders).
2. OR-Tools CP-SAT — independent second solver for larger cases.

Used only by the referee, never inside the pipeline being evaluated.
"""

from __future__ import annotations
from itertools import product

from core.schema import Order, Vehicle, VALID_SLOTS, VEHICLE_TYPE_NEEDS, FRAGILE_MAX_LOAD, AnyConstraint
from referee.checker import check_plan, CheckResult


def _all_valid_assignments(
    orders: list[Order],
    vehicles: list[Vehicle],
) -> list[dict[str, tuple[str, str]]]:
    """Enumerate all possible assignment dicts (ignoring constraints)."""
    choices = [(v.id, t) for v in vehicles for t in VALID_SLOTS]
    result = []
    for combo in product(choices, repeat=len(orders)):
        plan = {orders[i].id: combo[i] for i in range(len(orders))}
        result.append(plan)
    return result


def backtrack_oracle(
    orders: list[Order],
    vehicles: list[Vehicle],
    gold_constraints: list[AnyConstraint],
) -> tuple[bool, dict[str, tuple[str, str]] | None]:
    """
    Returns (feasible, plan_or_None).
    Feasible = True if any assignment passes the checker.
    Only practical for up to ~12 orders (15^12 ≈ 1.5e14 would be too slow;
    in practice the domain is much smaller: 2-3 vehicles × 4-6 slots).
    """
    for plan in _all_valid_assignments(orders, vehicles):
        result = check_plan(plan, orders, vehicles, gold_constraints)
        if result.valid:
            return True, plan
    return False, None


def ortools_oracle(
    orders: list[Order],
    vehicles: list[Vehicle],
    gold_constraints: list[AnyConstraint],
) -> tuple[bool, dict[str, tuple[str, str]] | None]:
    """
    Uses Google OR-Tools CP-SAT as an independent second solver.
    Returns (feasible, plan_or_None).
    """
    from ortools.sat.python import cp_model

    model = cp_model.CpModel()
    slots = VALID_SLOTS
    slot_idx = {s: i for i, s in enumerate(slots)}

    # x[o, v, t] = 1 iff order o assigned to vehicle v at slot t
    x: dict[tuple[str, str, str], cp_model.IntVar] = {}
    for o in orders:
        for v in vehicles:
            for t in slots:
                x[(o.id, v.id, t)] = model.new_bool_var(f"x_{o.id}_{v.id}_{t}")

    # R1
    for o in orders:
        model.add_exactly_one(x[(o.id, v.id, t)] for v in vehicles for t in slots)

    # R2
    for v in vehicles:
        for t in slots:
            model.add(
                sum(int(o.weight) * x[(o.id, v.id, t)] for o in orders) <= int(v.capacity)
            )

    # R9: fragile handling (CP-SAT is integer-only, so compare 100 * load to percent * capacity)
    pct = round(FRAGILE_MAX_LOAD * 100)
    for f in (o for o in orders if "fragile" in o.needs):
        for v in vehicles:
            for t in slots:
                model.add(
                    sum(100 * int(o.weight) * x[(o.id, v.id, t)] for o in orders)
                    <= pct * int(v.capacity)
                ).only_enforce_if(x[(f.id, v.id, t)])
                for g in orders:
                    if g.id != f.id and "oversized" in g.needs:
                        model.add(x[(f.id, v.id, t)] + x[(g.id, v.id, t)] <= 1)

    # R3
    for o in orders:
        for v in vehicles:
            for t in slots:
                if t not in v.available_slots:
                    model.add(x[(o.id, v.id, t)] == 0)

    # R4
    for o in orders:
        for v in vehicles:
            for t in slots:
                if o.deadline and slot_idx[t] > slot_idx[o.deadline]:
                    model.add(x[(o.id, v.id, t)] == 0)
                if o.time_window:
                    early, late = o.time_window
                    if slot_idx[t] < slot_idx[early] or slot_idx[t] > slot_idx[late]:
                        model.add(x[(o.id, v.id, t)] == 0)

    # R5
    for o in orders:
        for need in o.needs:
            if need not in VEHICLE_TYPE_NEEDS:
                continue
            for v in vehicles:
                if need not in v.type:
                    for t in slots:
                        model.add(x[(o.id, v.id, t)] == 0)

    # Gold user constraints
    from core.schema import (
        PrecedenceConstraint, ForbiddenVehicleConstraint, RequiredVehicleConstraint,
        ForbiddenSlotConstraint, VehicleUnavailableConstraint,
        DeadlineConstraint, UrgentDeliveryConstraint, ChangeDeadlineConstraint,
    )

    def slot_var(oid: str) -> cp_model.LinearExprT:
        return sum(slot_idx[t] * x[(oid, v.id, t)] for v in vehicles for t in slots)

    for uc in gold_constraints:
        if isinstance(uc, PrecedenceConstraint):
            model.add(slot_var(uc.before) < slot_var(uc.after))
        elif isinstance(uc, ForbiddenVehicleConstraint):
            for t in slots:
                model.add(x[(uc.order, uc.vehicle, t)] == 0)
        elif isinstance(uc, RequiredVehicleConstraint):
            model.add(sum(x[(uc.order, uc.vehicle, t)] for t in slots) == 1)
        elif isinstance(uc, ForbiddenSlotConstraint):
            for v in vehicles:
                model.add(x[(uc.order, v.id, uc.slot)] == 0)
        elif isinstance(uc, (DeadlineConstraint, ChangeDeadlineConstraint)):
            dl = uc.slot if isinstance(uc, DeadlineConstraint) else uc.new_deadline
            model.add(slot_var(uc.order) <= slot_idx[dl])
        elif isinstance(uc, UrgentDeliveryConstraint):
            model.add(slot_var(uc.order) <= slot_idx[uc.deadline])

    solver = cp_model.CpSolver()
    status = solver.solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        plan: dict[str, tuple[str, str]] = {}
        for o in orders:
            for v in vehicles:
                for t in slots:
                    if solver.value(x[(o.id, v.id, t)]):
                        plan[o.id] = (v.id, t)
                        break
        return True, plan

    return False, None
