"""
Z3 encoding of the dispatch planning problem.

Decision variable: x[o, v, t] = True iff order o is dispatched on vehicle v in slot t.

Rules R1–R9 are added as named (tracked) assertions so that UNSAT cores map back
to rule labels. Replanning with a previous plan uses Z3 Optimize to move as few
orders as possible.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from z3 import (
    Bool, BoolRef, Solver, Optimize, sat, unknown,
    Not, And, Or, If, Implies, Sum, PbEq, ArithRef,
)

from core.schema import (
    Order, Vehicle, VALID_SLOTS, VEHICLE_TYPE_NEEDS, FRAGILE_MAX_LOAD,
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
    TimeWindowConstraint,
)


@dataclass
class SolverResult:
    feasible: bool
    plan: dict[str, tuple[str, str]] = field(default_factory=dict)
    # plan[order_id] = (vehicle_id, slot)
    unsat_core_labels: list[str] = field(default_factory=list)
    solve_time_ms: float = 0.0
    timed_out: bool = False     # Z3 gave up before deciding; feasible is False but not proven
    moved: Optional[int] = None  # replans only: orders whose (vehicle, slot) changed, minimised


def _slot_index(slot: str) -> int:
    return VALID_SLOTS.index(slot)


def solve(
    orders: list[Order],
    vehicles: list[Vehicle],
    user_constraints: list[AnyConstraint],
    timeout_ms: int | None = None,
    previous_plan: dict[str, tuple[str, str]] | None = None,
) -> SolverResult:
    """
    Encode and solve the dispatch planning problem.

    timeout_ms: give up after this long (result.timed_out). None = no limit, as in
    the experiments; the app passes a limit so near-full fleets cannot hang it.
    previous_plan: replan with the fewest orders moved away from this plan. Orders
    that are new or were dropped do not count as moves.
    """
    import time

    slots = VALID_SLOTS
    # Boolean variables
    x: dict[tuple[str, str, str], BoolRef] = {}
    for o in orders:
        for v in vehicles:
            for t in slots:
                x[(o.id, v.id, t)] = Bool(f"x_{o.id}_{v.id}_{t}")

    s: Solver | Optimize = Optimize() if previous_plan else Solver()
    if not previous_plan:
        s.set("unsat_core", True)   # Optimize tracks cores without this flag
    if timeout_ms is not None:
        s.set("timeout", timeout_ms)

    label_counts: dict[str, int] = {}

    def track(constraint: BoolRef, label: str) -> None:
        # Repeated instructions (e.g. a replan restating the original request) reuse a label
        label_counts[label] = label_counts.get(label, 0) + 1
        if label_counts[label] > 1:
            label = f"{label}_{label_counts[label]}"
        s.assert_and_track(constraint, Bool(label))  # type: ignore[attr-defined]

    # --- R1: every order assigned exactly once ---
    for o in orders:
        lits = [(x[(o.id, v.id, t)], 1) for v in vehicles for t in slots]
        track(PbEq(lits, 1), f"R1_once_{o.id}")

    # --- R2: capacity per vehicle per slot ---
    load: dict[tuple[str, str], ArithRef] = {}
    for v in vehicles:
        for t in slots:
            load[(v.id, t)] = Sum([If(x[(o.id, v.id, t)], o.weight, 0) for o in orders])
            track(load[(v.id, t)] <= v.capacity, f"R2_cap_{v.id}_{t}")

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
            if need not in VEHICLE_TYPE_NEEDS:
                continue
            for v in vehicles:
                if need not in v.type and need != v.type:
                    for t in slots:
                        track(Not(x[(o.id, v.id, t)]), f"R5_compat_{o.id}_{v.id}_{need}_{t}")

    # --- R9: fragile handling (load limit, no oversized orders alongside) ---
    fragile = [o for o in orders if "fragile" in o.needs]
    oversized = [o for o in orders if "oversized" in o.needs]
    for f in fragile:
        for v in vehicles:
            for t in slots:
                track(
                    Implies(x[(f.id, v.id, t)], load[(v.id, t)] <= FRAGILE_MAX_LOAD * v.capacity),
                    f"R9_fragile_load_{f.id}_{v.id}_{t}",
                )
                for g in oversized:
                    if g.id != f.id:
                        track(
                            Not(And(x[(f.id, v.id, t)], x[(g.id, v.id, t)])),
                            f"R9_fragile_mix_{f.id}_{g.id}_{v.id}_{t}",
                        )

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

        elif isinstance(uc, TimeWindowConstraint):
            if uc.order in order_ids and uc.earliest in slots and uc.latest in slots:
                track(
                    And(slot_term(uc.order) >= _slot_index(uc.earliest),
                        slot_term(uc.order) <= _slot_index(uc.latest)),
                    f"USER_window_{uc.order}_{uc.earliest}_{uc.latest}",
                )

    # --- Minimal change: an order counts as moved unless it keeps its previous assignment ---
    if previous_plan:
        kept = [x[(oid, vid, t)] for oid, (vid, t) in previous_plan.items() if (oid, vid, t) in x]
        still_present = sum(1 for oid in previous_plan if oid in {o.id for o in orders})
        s.minimize(still_present - Sum([If(k, 1, 0) for k in kept]))  # type: ignore[attr-defined]

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
        moved = None
        if previous_plan is not None:
            moved = sum(1 for oid, a in plan.items() if oid in previous_plan and previous_plan[oid] != a)
        return SolverResult(feasible=True, plan=plan, solve_time_ms=elapsed_ms, moved=moved)

    if outcome == unknown:
        return SolverResult(feasible=False, solve_time_ms=elapsed_ms, timed_out=True)

    # UNSAT
    core_labels = [str(lit) for lit in s.unsat_core()]  # type: ignore[attr-defined]
    return SolverResult(
        feasible=False,
        unsat_core_labels=core_labels,
        solve_time_ms=elapsed_ms,
    )
