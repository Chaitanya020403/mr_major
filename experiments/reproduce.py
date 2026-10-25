"""
Runners for Experiments C–F and the report plots.

    python -m experiments.reproduce c        # translation quality (needs an LLM API key)
    python -m experiments.reproduce d        # scaling with the number of orders
    python -m experiments.reproduce e        # feasibility sweeps (tightness, fridge fraction)
    python -m experiments.reproduce f        # rule ablation
    python -m experiments.reproduce plots    # PNGs from the results files
    python -m experiments.reproduce all      # everything above

Results go to --out-dir (default results/). Experiment A is experiments.run and
Experiment B is experiments.replan. Seeds and scenario families match Experiment A
(seeds 0..19, families cycling) except D, which uses feasible scenarios.
"""

from __future__ import annotations
import argparse
import json
import logging
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from core.json_validator import validate_constraints
from core.solver import solve
from experiments.generator import GeneratorConfig, generate

logger = logging.getLogger(__name__)

FAMILIES = ["feasible", "planted_infeasible", "barely_feasible"]
N_SEEDS = 20
SCALING_SIZES = [6, 8, 10, 15, 20, 30]
SCALING_PER_SIZE = 10
TIGHTNESS_VALUES = [0.4, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95]
FRIDGE_VALUES = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
SWEEP_PER_POINT = 30


def _scenario(seed: int, **cfg: Any):
    return generate(GeneratorConfig(seed=seed, family=FAMILIES[seed % len(FAMILIES)], **cfg))


def _write(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"wrote {len(rows)} rows to {path}")


# ---------------------------------------------------------------------------
# Experiment C — translation quality
# ---------------------------------------------------------------------------

def _constraint_key(c: dict) -> tuple:
    """Compare constraints by meaning: every field except the quoted source phrase."""
    return tuple(sorted((k, json.dumps(v)) for k, v in c.items() if k != "source"))


def exp_c(provider: str, model: str | None) -> list[dict]:
    from core.translator import translate
    rows = []
    for seed in range(N_SEEDS):
        sc = _scenario(seed)
        llm, _ = translate(sc.request, sc.orders, sc.vehicles, provider, model)
        gold_keys = Counter(_constraint_key(c) for c in sc.gold_constraints)
        llm_keys = Counter(_constraint_key(c) for c in llm if isinstance(c, dict))
        tp = sum((gold_keys & llm_keys).values())
        fp = sum(llm_keys.values()) - tp
        fn = sum(gold_keys.values()) - tp
        precision = tp / (tp + fp) if tp + fp else 1.0
        recall = tp / (tp + fn) if tp + fn else 1.0
        rows.append({
            "seed": seed,
            "validator_ok": validate_constraints(llm, sc.orders, sc.vehicles).ok,
            "gold_constraints": len(sc.gold_constraints),
            "llm_constraints": len(llm),
            "tp": tp, "fp": fp, "fn": fn,
            "precision": precision,
            "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        })
    return rows


# ---------------------------------------------------------------------------
# Experiment D — scaling with the number of orders
# ---------------------------------------------------------------------------

def exp_d() -> list[dict]:
    """Z3 verdict vs the oracle-verified label, and solve time, as orders grow."""
    rows = []
    for n in SCALING_SIZES:
        correct, times = 0, []
        for seed in range(SCALING_PER_SIZE):
            sc = generate(GeneratorConfig(n_orders=n, seed=seed, family="feasible"))
            gc = validate_constraints(sc.gold_constraints, sc.orders, sc.vehicles).constraints
            r = solve(sc.orders, sc.vehicles, gc)
            correct += r.feasible == sc.label["feasible"]
            times.append(r.solve_time_ms)
        rows.append({"n_orders": n, "correct": correct, "total": SCALING_PER_SIZE,
                     "avg_ms": round(statistics.mean(times), 2)})
    return rows


# ---------------------------------------------------------------------------
# Experiment E — feasibility sweeps
# ---------------------------------------------------------------------------

def exp_e() -> tuple[list[dict], list[dict]]:
    from experiments.sweeps import sweep_fridge_fraction, sweep_tightness
    tight = sweep_tightness(TIGHTNESS_VALUES, n_per_point=SWEEP_PER_POINT)
    fridge = sweep_fridge_fraction(FRIDGE_VALUES, n_per_point=SWEEP_PER_POINT)
    return (
        [{"tightness": r.param_value, "feasible": r.n_feasible, "total": r.n_total} for r in tight],
        [{"fridge_fraction": r.param_value, "feasible": r.n_feasible, "total": r.n_total} for r in fridge],
    )


# ---------------------------------------------------------------------------
# Experiment F — rule ablation
# ---------------------------------------------------------------------------

def _ablation_cases() -> list[dict]:
    cases = []
    for seed in range(N_SEEDS):
        sc = _scenario(seed)
        gc = validate_constraints(sc.gold_constraints, sc.orders, sc.vehicles).constraints
        cases.append({"name": f"seed_{seed}", "orders": sc.orders, "vehicles": sc.vehicles,
                      "constraints": gc, "original_result": solve(sc.orders, sc.vehicles, gc)})
    return cases


def exp_f() -> list[dict]:
    from experiments.ablation import run_ablation
    return [
        {"case": r.case, "rule": r.rule_removed, "orig_feasible": r.original_feasible,
         "ablated_feasible": r.ablated_feasible, "flipped": r.flipped}
        for r in run_ablation(_ablation_cases())
    ]


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------

def plots(results: Path) -> None:
    from experiments.ablation import AblationRow
    from experiments.plots import plot_ablation_flip_rate, plot_core_frequency, plot_feasibility_curve
    from experiments.sweeps import SweepResult

    def read(name: str) -> list[dict]:
        return [json.loads(line) for line in (results / name).open()]

    out = results / "plots"
    out.mkdir(parents=True, exist_ok=True)
    for name, key, png, title in [
        ("exp_e_tightness.jsonl", "tightness", "tightness_curve.png", "Feasibility rate vs capacity tightness"),
        ("exp_e_fridge.jsonl", "fridge_fraction", "fridge_curve.png", "Feasibility rate vs refrigeration fraction"),
    ]:
        sweep = [SweepResult(key, r[key], r["feasible"], r["total"]) for r in read(name)]
        plot_feasibility_curve(sweep, out / png, title=title)
    ablation = [AblationRow(r["case"], r["rule"], r["orig_feasible"], r["ablated_feasible"], r["flipped"])
                for r in read("exp_f_ablation.jsonl")]
    plot_ablation_flip_rate(ablation, out / "ablation_flip_rate.png")
    # Which rule families appear in the UNSAT cores of the infeasible Exp F scenarios
    cores = [c["original_result"].unsat_core_labels for c in _ablation_cases()
             if not c["original_result"].feasible]
    plot_core_frequency(cores, out / "unsat_core_freq.png")
    print(f"wrote plots to {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Experiments C–F and the plots")
    parser.add_argument("what", choices=["c", "d", "e", "f", "plots", "all"])
    parser.add_argument("--out-dir", type=Path, default=Path("results"))
    parser.add_argument("--provider", default="groq", help="LLM provider for Experiment C")
    parser.add_argument("--model", default=None)
    args = parser.parse_args()
    out = args.out_dir

    if args.what in ("c", "all"):
        _write(exp_c(args.provider, args.model), out / "exp_c.jsonl")
    if args.what in ("d", "all"):
        _write(exp_d(), out / "exp_d.jsonl")
    if args.what in ("e", "all"):
        tight, fridge = exp_e()
        _write(tight, out / "exp_e_tightness.jsonl")
        _write(fridge, out / "exp_e_fridge.jsonl")
    if args.what in ("f", "all"):
        _write(exp_f(), out / "exp_f_ablation.jsonl")
    if args.what in ("plots", "all"):
        plots(out)


if __name__ == "__main__":
    main()
