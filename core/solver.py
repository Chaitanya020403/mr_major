"""
Z3 encoding of the dispatch planning problem.

Decision variable: x[o, v, t] = True iff order o is dispatched on vehicle v in slot t.

Rules R1–R9 are added as named (tracked) assertions so that UNSAT cores map back
to rule labels.  R9 (soft preferences) uses Z3 Optimize if enabled.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from z3 import (
    Bool, BoolRef, Solver, Optimize, sat, unsat,
    Not, And, Or, If, Sum, PbEq, ArithRef,
)

from core.schema import (
    Order, Vehicle, VALID_SLOTS,
    AnyConstraint,
    PrecedenceConstraint,
    ForbiddenVehicleConstraint,
    RequiredVehicleConstraint,
    ForbiddenSlotConstraint,
    VehicleUnavailableConstraint,
    DeadlineConstraint,
    TimeWindowConstraint,
    UrgentDeliveryConstraint,
    ChangeDeadlineConstraint,
    PreferredEarlyConstraint,
)


@dataclass
class SolverResult:
    feasible: bool
    plan: dict[str, tuple[str, str]] = field(default_factory=dict)
    # plan[order_id] = (vehicle_id, slot)
    unsat_core_labels: list[str] = field(default_factory=list)
    solve_time_ms: float = 0.0


def _slot_index(slot: str) -> int:
    return VALID_SLOTS.index(slot)


def solve(
    orders: list[Order],
    vehicles: list[Vehicle],
    user_constraints: list[AnyConstraint],
    optimize: bool = False,
) -> SolverResult:
    """
    Encode and solve the dispatch planning problem.

    If optimize=True, uses Z3 Optimize to minimise total slot index (R9).
    """
    import time

    slots = VALID_SLOTS
    # Boolean variables
    x: dict[tuple[str, str, str], BoolRef] = {}
    for o in orders:
        for v in vehicles:
            for t in slots:
                x[(o.id, v.id, t)] = Bool(f"x_{o.id}_{v.id}_{t}")

    s: Solver | Optimize = Optimize() if optimize else Solver()
    s.set("unsat_core", True)  # type: ignore[attr-defined]

    def track(constraint: BoolRef, label: str) -> None:
        s.assert_and_track(constraint, Bool(label))  # type: ignore[attr-defined]

    # --- R1: every order assigned exactly once ---
    for o in orders:
        lits = [(x[(o.id, v.id, t)], 1) for v in vehicles for t in slots]
        track(PbEq(lits, 1), f"R1_once_{o.id}")

    # --- R2: capacity per vehicle per slot ---
    for v in vehicles:
        for t in slots:
            total = Sum([If(x[(o.id, v.id, t)], o.weight, 0) for o in orders])
            track(total <= v.capacity, f"R2_cap_{v.id}_{t}")

    # --- R3: vehicle availability ---
    for o in orders:
        for v in vehicles:
            for t in slots:
                if t not in v.available_slots:
                    track(Not(x[(o.id, v.id, t)]), f"R3_avail_{o.id}_{v.id}_{t}")

    # --- R4: delivery window / deadline from order data ---
    # CHANGE_DEADLINE/DEADLINE user constraints override the order's original window.
    deadline_overrides: dict[str, str] = {}
    for uc in user_constraints:
        if isinstance(uc, ChangeDeadlineConstraint):
            deadline_overrides[uc.order] = uc.new_deadline
        elif isinstance(uc, DeadlineConstraint):
            deadline_overrides[uc.order] = uc.slot

    for o in orders:
        for v in vehicles:
            for t in slots:
                blocked = False
                effective_deadline = deadline_overrides.get(o.id, o.deadline)
                if effective_deadline and _slot_index(t) > _slot_index(effective_deadline):
                    blocked = True
                # time_window is only enforced if there's no deadline override
                if o.time_window and o.id not in deadline_overrides:
                    early, late = o.time_window
                    if _slot_index(t) < _slot_index(early) or _slot_index(t) > _slot_index(late):
                        blocked = True
                if blocked:
                    track(Not(x[(o.id, v.id, t)]), f"R4_window_{o.id}_{v.id}_{t}")

    # --- R5: special-handling compatibility ---
    for o in orders:
        for need in o.needs:
            for v in vehicles:
                if need not in v.type and need != v.type:
                    for t in slots:
                        track(Not(x[(o.id, v.id, t)]), f"R5_compat_{o.id}_{v.id}_{need}_{t}")

    # --- Derived slot term for an order ---
    def slot_term(oid: str) -> ArithRef:
        return Sum([If(x[(oid, v.id, t)], _slot_index(t), 0) for v in vehicles for t in slots])

    # --- R6 (urgent from order data) and user constraints ---
    for o in orders:
        if o.priority == "urgent" and o.deadline:
            track(slot_term(o.id) <= _slot_index(o.deadline), f"R6_urgent_{o.id}")

    # --- User constraints (from LLM JSON) ---
    order_ids = {o.id for o in orders}
    vehicle_ids = {v.id for v in vehicles}

    for uc in user_constraints:
        if isinstance(uc, PrecedenceConstraint):
            if uc.before in order_ids and uc.after in order_ids:
                track(
                    slot_term(uc.before) < slot_term(uc.after),
                    f"USER_precedence_{uc.before}_before_{uc.after}",
                )

        elif isinstance(uc, ForbiddenVehicleConstraint):
            if uc.order in order_ids and uc.vehicle in vehicle_ids:
                for t in slots:
                    track(Not(x[(uc.order, uc.vehicle, t)]), f"USER_forbid_{uc.order}_{uc.vehicle}_{t}")

        elif isinstance(uc, RequiredVehicleConstraint):
            if uc.order in order_ids and uc.vehicle in vehicle_ids:
                track(
                    Or([x[(uc.order, uc.vehicle, t)] for t in slots]),
                    f"USER_require_{uc.order}_{uc.vehicle}",
                )

        elif isinstance(uc, ForbiddenSlotConstraint):
            if uc.order in order_ids and uc.slot in slots:
                for v in vehicles:
                    track(Not(x[(uc.order, v.id, uc.slot)]), f"USER_forbid_slot_{uc.order}_{uc.slot}_{v.id}")

        elif isinstance(uc, VehicleUnavailableConstraint):
            if uc.vehicle in vehicle_ids and uc.slot in slots:
                for o in orders:
                    track(Not(x[(o.id, uc.vehicle, uc.slot)]), f"USER_unavail_{uc.vehicle}_{uc.slot}_{o.id}")

        elif isinstance(uc, (DeadlineConstraint, ChangeDeadlineConstraint)):
            oid = uc.order
            slot = uc.slot if isinstance(uc, DeadlineConstraint) else uc.new_deadline
            if oid in order_ids and slot in slots:
                track(slot_term(oid) <= _slot_index(slot), f"USER_deadline_{oid}_{slot}")

        elif isinstance(uc, UrgentDeliveryConstraint):
            if uc.order in order_ids and uc.deadline in slots:
                track(slot_term(uc.order) <= _slot_index(uc.deadline), f"USER_urgent_{uc.order}")

    # --- R9: soft preference (minimize total slot index) ---
    if optimize:
        s.minimize(Sum([slot_term(o.id) for o in orders]))  # type: ignore[attr-defined]

    t0 = time.perf_counter()
    outcome = s.check()
    elapsed_ms = (time.perf_counter() - t0) * 1000

    if outcome == sat:
        model = s.model()
        plan: dict[str, tuple[str, str]] = {}
        for o in orders:
            for v in vehicles:
                for t in slots:
                    if model.evaluate(x[(o.id, v.id, t)]):
                        plan[o.id] = (v.id, t)
                        break
        return SolverResult(feasible=True, plan=plan, solve_time_ms=elapsed_ms)

    # UNSAT
    core_labels = [str(lit) for lit in s.unsat_core()]  # type: ignore[attr-defined]
    return SolverResult(
        feasible=False,
        unsat_core_labels=core_labels,
        solve_time_ms=elapsed_ms,
    )
