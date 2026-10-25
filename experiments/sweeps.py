"""
Experiment E — Parameter sweeps.

Varies one parameter at a time and plots feasibility rate.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any

from experiments.generator import GeneratorConfig, generate, Scenario


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
    """Vary tightness across all three scenario families to see the feasibility transition."""
    families = ["feasible", "planted_infeasible", "barely_feasible"]
    results = []
    for tightness in tightness_values:
        n_feasible = 0
        for i in range(n_per_point):
            family = families[i % len(families)]
            cfg = GeneratorConfig(tightness=tightness, seed=base_seed + i, family=family)
            scenario = generate(cfg)
            # Label is set by the OR-Tools oracle and cross-checked against Z3 in generate()
            if scenario.label["feasible"]:
                n_feasible += 1
        results.append(SweepResult("tightness", tightness, n_feasible, n_per_point))
    return results


def sweep_fridge_fraction(
    fridge_values: list[float],
    n_per_point: int = 20,
    base_seed: int = 100,
) -> list[SweepResult]:
    """Vary fridge fraction across all families to see compatibility-driven infeasibility."""
    families = ["feasible", "planted_infeasible", "barely_feasible"]
    results = []
    for ff in fridge_values:
        n_feasible = 0
        for i in range(n_per_point):
            family = families[i % len(families)]
            cfg = GeneratorConfig(fridge_fraction=ff, seed=base_seed + i, family=family)
            scenario = generate(cfg)
            # Label is set by the OR-Tools oracle and cross-checked against Z3 in generate()
            if scenario.label["feasible"]:
                n_feasible += 1
        results.append(SweepResult("fridge_fraction", ff, n_feasible, n_per_point))
    return results
