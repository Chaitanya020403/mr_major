"""
Experiment E — Parameter sweeps.

Varies one parameter at a time and plots feasibility rate.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any

from experiments.generator import GeneratorConfig, generate, Scenario
from core.data_validator import validate_orders, validate_fleet
from core.json_validator import validate_constraints
from core.solver import solve


@dataclass
class SweepResult:
    param_name: str
    param_value: float
    n_feasible: int
    n_total: int

    @property
    def feasibility_rate(self) -> float:
        return self.n_feasible / self.n_total if self.n_total else 0.0


def sweep_tightness(
    tightness_values: list[float],
    n_per_point: int = 20,
    base_seed: int = 0,
) -> list[SweepResult]:
    results = []
    for tightness in tightness_values:
        n_feasible = 0
        for i in range(n_per_point):
            cfg = GeneratorConfig(tightness=tightness, seed=base_seed + i)
            scenario = generate(cfg)
            orders, _ = validate_orders([o.model_dump() for o in scenario.orders])
            vehicles, _ = validate_fleet([v.model_dump() for v in scenario.vehicles])
            gc = validate_constraints(scenario.gold_constraints, orders, vehicles)
            r = solve(orders, vehicles, gc.constraints)
            if r.feasible:
                n_feasible += 1
        results.append(SweepResult("tightness", tightness, n_feasible, n_per_point))
    return results


def sweep_fridge_fraction(
    fridge_values: list[float],
    n_per_point: int = 20,
    base_seed: int = 100,
) -> list[SweepResult]:
    results = []
    for ff in fridge_values:
        n_feasible = 0
        for i in range(n_per_point):
            cfg = GeneratorConfig(fridge_fraction=ff, seed=base_seed + i)
            scenario = generate(cfg)
            orders, _ = validate_orders([o.model_dump() for o in scenario.orders])
            vehicles, _ = validate_fleet([v.model_dump() for v in scenario.vehicles])
            gc = validate_constraints(scenario.gold_constraints, orders, vehicles)
            r = solve(orders, vehicles, gc.constraints)
            if r.feasible:
                n_feasible += 1
        results.append(SweepResult("fridge_fraction", ff, n_feasible, n_per_point))
    return results
