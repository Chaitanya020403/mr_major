"""
Main experiment runner.

Usage:
  python -m experiments.run --case data/cases/case_000 --method both
  python -m experiments.run --generate --n 50 --method both --out results/exp_a.jsonl
"""

from __future__ import annotations
import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def load_case(case_dir: Path) -> dict[str, Any]:
    orders_raw  = json.loads((case_dir / "orders.json").read_text())
    fleet_raw   = json.loads((case_dir / "fleet.json").read_text())
    request     = (case_dir / "request.txt").read_text().strip()
    gold_raw    = json.loads((case_dir / "gold_constraints.json").read_text())
    label       = json.loads((case_dir / "label.json").read_text())
    return dict(orders_raw=orders_raw, fleet_raw=fleet_raw, request=request,
                gold_raw=gold_raw, label=label)


def run_our_system(case: dict[str, Any], provider: str, model: str | None) -> dict[str, Any]:
    from core.data_validator import validate_orders, validate_fleet
    from core.translator import translate
    from core.json_validator import validate_constraints
    from core.solver import solve
    from core.explainer import explain

    orders, ov = validate_orders(case["orders_raw"])
    vehicles, fv = validate_fleet(case["fleet_raw"])
    if not ov.ok or not fv.ok:
        return {"error": f"Data validation failed:\n{ov}\n{fv}"}

    raw_json, llm_text = translate(case["request"], orders, vehicles, provider, model)
    cv = validate_constraints(raw_json, orders, vehicles)
    if not cv.ok:
        return {"error": f"Constraint validation failed:\n{cv}", "llm_raw": llm_text}

    result = solve(orders, vehicles, cv.constraints)
    exp = explain(result, orders, vehicles)

    return {
        "method": "llm_z3",
        "feasible": result.feasible,
        "plan": result.plan if result.feasible else None,
        "unsat_core": result.unsat_core_labels,
        "conflict_json": exp.conflict_json,
        "solve_time_ms": result.solve_time_ms,
        "llm_constraints": raw_json,
    }


def run_llm_alone(case: dict[str, Any], provider: str, model: str | None) -> dict[str, Any]:
    from core.data_validator import validate_orders, validate_fleet
    from core.llm_alone import plan_with_llm_alone

    orders, ov = validate_orders(case["orders_raw"])
    vehicles, fv = validate_fleet(case["fleet_raw"])
    if not ov.ok or not fv.ok:
        return {"error": f"Data validation failed:\n{ov}\n{fv}"}

    parsed, raw = plan_with_llm_alone(case["request"], orders, vehicles, provider, model)
    return {
        "method": "llm_alone",
        "feasible": parsed.get("feasible"),
        "plan": parsed.get("plan"),
        "conflict_json": parsed if not parsed.get("feasible") else {},
        "llm_raw": raw,
    }


def evaluate(result: dict[str, Any], case: dict[str, Any], orders, vehicles) -> dict[str, Any]:
    from core.data_validator import validate_orders, validate_fleet
    from core.json_validator import validate_constraints
    from referee.checker import check_plan

    gold_raw = case["gold_raw"]
    gold_cv = validate_constraints(gold_raw, orders, vehicles)
    gold_constraints = gold_cv.constraints

    ground_truth_feasible = case["label"]["feasible"]
    claimed_feasible = result.get("feasible")

    eval_out: dict[str, Any] = {
        "ground_truth_feasible": ground_truth_feasible,
        "claimed_feasible":      claimed_feasible,
        "correct_verdict":       claimed_feasible == ground_truth_feasible,
    }

    if claimed_feasible and result.get("plan"):
        # Our system stores tuples; LLM-alone stores {"vehicle": ..., "slot": ...}
        def _to_tuple(v: Any) -> tuple[str, str]:
            if isinstance(v, (list, tuple)):
                return (str(v[0]), str(v[1]))
            return (str(v["vehicle"]), str(v["slot"]))
        plan = {oid: _to_tuple(v) for oid, v in result["plan"].items()}
        check = check_plan(plan, orders, vehicles, gold_constraints)
        eval_out["plan_valid"] = check.valid
        eval_out["plan_violations"] = check.violations
    elif claimed_feasible and not result.get("plan"):
        eval_out["plan_valid"] = False
        eval_out["plan_violations"] = ["LLM-alone returned feasible=true but no plan"]

    return eval_out


def run_case(case_dir: Path, method: str, provider: str, model: str | None) -> dict[str, Any]:
    case = load_case(case_dir)

    from core.data_validator import validate_orders, validate_fleet
    orders, _ = validate_orders(case["orders_raw"])
    vehicles, _ = validate_fleet(case["fleet_raw"])

    output: dict[str, Any] = {"case": str(case_dir)}

    if method in ("our", "both"):
        r = run_our_system(case, provider, model)
        ev = evaluate(r, case, orders, vehicles)
        output["llm_z3"] = {**r, "eval": ev}

    if method in ("llm_alone", "both"):
        r = run_llm_alone(case, provider, model)
        ev = evaluate(r, case, orders, vehicles)
        output["llm_alone"] = {**r, "eval": ev}

    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=Path, help="Path to a single case directory")
    parser.add_argument("--generate", action="store_true", help="Generate n scenarios")
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--method", choices=["our", "llm_alone", "both"], default="both")
    parser.add_argument("--provider", default="groq")
    parser.add_argument("--model", default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--delay", type=float, default=15.0,
                        help="Seconds to sleep between cases (avoid rate limits)")
    args = parser.parse_args()

    results = []

    if args.case:
        r = run_case(args.case, args.method, args.provider, args.model)
        results.append(r)
        print(json.dumps(r, indent=2))

    elif args.generate:
        from experiments.generator import GeneratorConfig, generate, save_case
        import tempfile, os, time
        families = ["feasible", "planted_infeasible", "barely_feasible"]
        for i in range(args.n):
            cfg = GeneratorConfig(seed=i, family=families[i % len(families)])
            scenario = generate(cfg)
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp) / f"case_{i:04d}"
                save_case(scenario, p)
                r = run_case(p, args.method, args.provider, args.model)
                r["seed"] = i
                results.append(r)
                print(f"case {i:04d}: {r.get('llm_z3', r.get('llm_alone', {})).get('feasible')}")
                time.sleep(args.delay)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w") as f:
            for r in results:
                f.write(json.dumps(r) + "\n")
        logger.info("Results written to %s", args.out)


if __name__ == "__main__":
    main()
