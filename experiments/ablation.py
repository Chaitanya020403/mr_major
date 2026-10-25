"""
Experiment F — Rule ablation.

For each rule in {R2…R9}, removes all constraints for that rule across a set of
scenarios and re-solves. Measures how often removing a rule changes the outcome
(UNSAT → SAT or a different plan).
"""

from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from core.schema import Order, Vehicle, AnyConstraint
from core.solver import solve, SolverResult

logger = logging.getLogger(__name__)

ABLATABLE_RULES = ["R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9"]


def _solve_without_rule(
    orders: list[Order],
    vehicles: list[Vehicle],
    user_constraints: list[AnyConstraint],
    rule: str,
) -> SolverResult:
    """Patch orders/vehicles/constraints to remove a rule's effect, then solve."""
    from copy import deepcopy
    from core.schema import (
        PrecedenceConstraint, ForbiddenVehicleConstraint, RequiredVehicleConstraint,
        ForbiddenSlotConstraint, VehicleUnavailableConstraint,
        DeadlineConstraint, TimeWindowConstraint, UrgentDeliveryConstraint,
        ChangeDeadlineConstraint,
    )

    patched_orders = deepcopy(orders)
    patched_vehicles = deepcopy(vehicles)
    patched_constraints = list(user_constraints)

    if rule == "R2":
        for v in patched_vehicles:
            v.capacity = 1_000_000
    elif rule == "R3":
        from core.schema import VALID_SLOTS
        for v in patched_vehicles:
            v.available_slots = list(VALID_SLOTS)
    elif rule == "R4":
        for o in patched_orders:
            o.deadline = None
            o.time_window = None
    elif rule == "R5":
        from core.schema import VEHICLE_TYPE_NEEDS
        for o in patched_orders:
            o.needs = [n for n in o.needs if n not in VEHICLE_TYPE_NEEDS]
    elif rule == "R9":
        for o in patched_orders:
            o.needs = [n for n in o.needs if n != "fragile"]
    elif rule == "R6":
        for o in patched_orders:
            if o.priority == "urgent":
                o.priority = "normal"
    elif rule == "R7":
        patched_constraints = [
            c for c in patched_constraints
            if not isinstance(c, PrecedenceConstraint)
        ]
    elif rule == "R8":
        patched_constraints = [
            c for c in patched_constraints
            if not isinstance(c, (ForbiddenVehicleConstraint, RequiredVehicleConstraint,
                                   ForbiddenSlotConstraint, VehicleUnavailableConstraint,
                                   DeadlineConstraint, TimeWindowConstraint,
                                   UrgentDeliveryConstraint, ChangeDeadlineConstraint))
        ]

    return solve(patched_orders, patched_vehicles, patched_constraints)


@dataclass
class AblationRow:
    case: str
    rule_removed: str
    original_feasible: bool
    ablated_feasible: bool
    flipped: bool


def run_ablation(
    cases: list[dict],  # list of dicts with keys: orders, vehicles, constraints, original_result
) -> list[AblationRow]:
    rows: list[AblationRow] = []
    for case in cases:
        orig_feasible = case["original_result"].feasible
        for rule in ABLATABLE_RULES:
            ablated = _solve_without_rule(
                case["orders"], case["vehicles"], case["constraints"], rule
            )
            rows.append(AblationRow(
                case=case["name"],
                rule_removed=rule,
                original_feasible=orig_feasible,
                ablated_feasible=ablated.feasible,
                flipped=ablated.feasible != orig_feasible,
            ))
    return rows
