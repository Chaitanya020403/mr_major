"""
Experiment B — Replanning (RQ3).

For each seeded scenario that has a round-1 plan, take T1 out of service in its
first available slot ("T1 is unavailable at 9AM.") and replan with the original
gold constraints plus that change, moving as few orders as possible away from the
round-1 plan. Records whether a plan still exists, the minimum number of orders
that had to change vehicle or slot, and the round-2 solve time.

Runs on gold constraints (no LLM), so it measures the solver's replanning and is
fully reproducible. Feasibility verdicts are cross-checked against the OR-Tools
oracle via ground_truth_feasible().

Run:
    python -m experiments.replan --n 20 --out results/exp_b.jsonl
"""

from __future__ import annotations
import argparse
import json
import logging
from pathlib import Path
from typing import Any

from core.json_validator import validate_constraints
from core.solver import solve
from experiments.generator import GeneratorConfig, LabelDisagreement, generate, ground_truth_feasible

logger = logging.getLogger(__name__)

FAMILIES = ["feasible", "planted_infeasible", "barely_feasible"]   # same cycle as Exp A
CHANGED_VEHICLE = "T1"


def replan_case(seed: int) -> dict[str, Any]:
    scenario = generate(GeneratorConfig(seed=seed, family=FAMILIES[seed % len(FAMILIES)]))
    orders, vehicles = scenario.orders, scenario.vehicles

    round1 = solve(orders, vehicles, validate_constraints(scenario.gold_constraints, orders, vehicles).constraints)
    if not round1.feasible:
        return {"seed": seed, "skipped": True}   # nothing to replan

    vehicle = next(v for v in vehicles if v.id == CHANGED_VEHICLE)
    slot = vehicle.available_slots[0]
    change = f"{CHANGED_VEHICLE} is unavailable at {slot}."
    gold2 = scenario.gold_constraints + [
        {"type": "VEHICLE_UNAVAILABLE", "vehicle": CHANGED_VEHICLE, "slot": slot, "source": change}
    ]
    round2 = solve(orders, vehicles, validate_constraints(gold2, orders, vehicles).constraints,
                   previous_plan=round1.plan)
    if round2.feasible != ground_truth_feasible(orders, vehicles, gold2):
        raise LabelDisagreement(f"seed {seed}: Z3 and the OR-Tools oracle disagree on round 2")

    # Minimum over all valid round-2 plans, so it does not depend on which plan Z3 picks
    moved = round2.moved if round2.feasible else 0
    return {
        "seed": seed,
        "round1_feasible": True,
        "round2_feasible": round2.feasible,
        "assignments_moved": moved,
        "solve_time_ms": round2.solve_time_ms,
        "change": change,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Experiment B — replanning after a vehicle outage")
    parser.add_argument("--n", type=int, default=20, help="Number of seeds (0..n-1)")
    parser.add_argument("--out", type=Path, help="Write JSONL results here")
    args = parser.parse_args()

    rows = [replan_case(seed) for seed in range(args.n)]
    for r in rows:
        print(json.dumps(r))

    done = [r for r in rows if not r.get("skipped")]
    if done:
        still = sum(r["round2_feasible"] for r in done)
        print(f"\n{len(done)} replanned ({len(rows) - len(done)} skipped: no round-1 plan); "
              f"{still} still feasible after the change; "
              f"max round-2 solve time {max(r['solve_time_ms'] for r in done):.1f} ms")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        logger.info("Results written to %s", args.out)


if __name__ == "__main__":
    main()
