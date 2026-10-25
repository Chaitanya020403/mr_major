"""
Turns Z3 results into plain-language explanations shown to the dispatcher.
Also produces the structured conflict JSON consumed by the referee.
"""

from __future__ import annotations
import json
import re
from dataclasses import dataclass, field

from core.solver import SolverResult
from core.schema import Order, Vehicle


RULE_DESCRIPTIONS = {
    "R1": "every order must be assigned exactly once",
    "R2": "vehicle capacity per slot",
    "R3": "vehicle availability",
    "R4": "delivery window / deadline",
    "R5": "special-handling compatibility",
    "R6": "urgent order deadline",
    "R7": "precedence between orders",
    "R8": "user restriction",
    "USER": "dispatcher instruction",
}


@dataclass
class Explanation:
    feasible: bool
    summary: str
    plan_table: list[dict] = field(default_factory=list)   # rows for SAT
    conflict_json: dict = field(default_factory=dict)       # structured for UNSAT
    detail: str = ""
    timed_out: bool = False


def explain(
    result: SolverResult,
    orders: list[Order],
    vehicles: list[Vehicle],
) -> Explanation:
    if result.feasible:
        return _explain_sat(result, orders, vehicles)
    if result.timed_out:
        return Explanation(
            feasible=False,
            timed_out=True,
            summary=(
                f"Could not decide within {result.solve_time_ms / 1000:.0f} s whether a valid plan exists. "
                "This happens when vehicles are nearly full. Try fewer orders, more vehicles "
                "or capacity, or looser deadlines."
            ),
        )
    return _explain_unsat(result, orders, vehicles)


def _explain_sat(result: SolverResult, orders: list[Order], vehicles: list[Vehicle]) -> Explanation:
    order_map = {o.id: o for o in orders}
    vehicle_map = {v.id: v for v in vehicles}
    rows = []
    for oid, (vid, slot) in sorted(result.plan.items()):
        o = order_map[oid]
        v = vehicle_map[vid]
        rows.append({"order": oid, "vehicle": vid, "slot": slot, "weight_kg": o.weight})

    # Compute per-vehicle loads per slot
    load: dict[tuple[str, str], float] = {}
    for oid, (vid, slot) in result.plan.items():
        key = (vid, slot)
        load[key] = load.get(key, 0) + order_map[oid].weight

    load_lines = [
        f"  {vid}@{slot}: {kg:.0f} kg / {vehicle_map[vid].capacity:.0f} kg"
        for (vid, slot), kg in sorted(load.items())
    ]

    summary = (
        f"Feasible plan found in {result.solve_time_ms:.1f} ms.\n"
        f"Vehicle loads:\n" + "\n".join(load_lines)
    )
    return Explanation(feasible=True, summary=summary, plan_table=rows)


def _explain_unsat(result: SolverResult, orders: list[Order], vehicles: list[Vehicle]) -> Explanation:
    labels = result.unsat_core_labels

    # Extract rule families and IDs mentioned in core labels
    rule_families: set[str] = set()
    involved_orders: set[str] = set()
    involved_vehicles: set[str] = set()
    order_ids = {o.id for o in orders}
    vehicle_ids = {v.id for v in vehicles}

    for label in labels:
        # e.g. R2_cap_T1_9AM, USER_precedence_B_before_C
        m = re.match(r"(R\d+|USER)", label)
        if m:
            rule_families.add(m.group(1))
        for oid in order_ids:
            if oid in label:
                involved_orders.add(oid)
        for vid in vehicle_ids:
            if vid in label:
                involved_vehicles.add(vid)

    rule_names = sorted(rule_families)
    descriptions = [RULE_DESCRIPTIONS.get(r, r) for r in rule_names]

    conflict_json = {
        "feasible": False,
        "conflict": {
            "orders":   sorted(involved_orders),
            "vehicles": sorted(involved_vehicles),
            "rules":    rule_names,
        },
    }

    lines = [f"No valid plan exists. Conflicting rules ({result.solve_time_ms:.1f} ms):"]
    for r, desc in zip(rule_names, descriptions):
        lines.append(f"  [{r}] {desc}")
    if involved_orders:
        lines.append(f"  Involved orders: {', '.join(sorted(involved_orders))}")
    if involved_vehicles:
        lines.append(f"  Involved vehicles: {', '.join(sorted(involved_vehicles))}")

    summary = "\n".join(lines)
    return Explanation(
        feasible=False,
        summary=summary,
        conflict_json=conflict_json,
        detail=json.dumps(conflict_json, indent=2),
    )
