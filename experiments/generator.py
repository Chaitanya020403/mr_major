"""
Seeded scenario generator.

The same seed always produces the same scenario. Scenarios come in five families:
  feasible, planted_infeasible, barely_feasible, hidden_conflict, ambiguous_request
"""

from __future__ import annotations
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.schema import Order, Vehicle, VALID_SLOTS


@dataclass
class GeneratorConfig:
    n_orders: int = 8
    n_vehicles: int = 2
    n_slots: int = 4
    tightness: float = 0.7        # total_weight / total_capacity target
    fridge_fraction: float = 0.25 # fraction of orders needing refrigeration
    precedence_chain: int = 2     # number of precedence pairs
    seed: int = 42
    family: str = "feasible"      # feasible | planted_infeasible | barely_feasible |
                                   #            hidden_conflict   | ambiguous_request


@dataclass
class Scenario:
    orders: list[Order]
    vehicles: list[Vehicle]
    request: str
    gold_constraints: list[dict]
    label: dict                    # {"feasible": bool, "conflict": {...} | null}
    change: str = ""               # for replanning round 2
    change_gold_constraints: list[dict] = field(default_factory=list)
    change_label: dict = field(default_factory=dict)


def generate(cfg: GeneratorConfig) -> Scenario:
    rng = random.Random(cfg.seed)
    slots = VALID_SLOTS[:cfg.n_slots]

    # Build vehicles
    vehicles: list[Vehicle] = []
    has_fridge = False
    for i in range(cfg.n_vehicles):
        is_fridge = (i == 0 and cfg.fridge_fraction > 0)
        vtype = "refrigerated" if is_fridge else "normal"
        if is_fridge:
            has_fridge = True
        cap = rng.randint(80, 150)
        avail = rng.sample(slots, k=rng.randint(max(1, len(slots)-1), len(slots)))
        vehicles.append(Vehicle(
            id=f"T{i+1}", capacity=cap, type=vtype,
            available_slots=sorted(avail, key=slots.index),
        ))

    # Build orders
    total_cap = sum(v.capacity for v in vehicles) * cfg.tightness
    orders: list[Order] = []
    weight_remaining = total_cap
    for i in range(cfg.n_orders):
        max_w = max(10, int(weight_remaining / (cfg.n_orders - i)))
        w = rng.randint(10, max_w)
        weight_remaining -= w
        needs_fridge = has_fridge and rng.random() < cfg.fridge_fraction
        dl_slot = rng.choice(slots)
        orders.append(Order(
            id=chr(ord("A") + i),
            weight=float(w),
            deadline=dl_slot,
            priority=rng.choice(["normal", "normal", "normal", "urgent"]),
            needs=["refrigerated"] if needs_fridge else [],
        ))

    # Generate precedence constraints
    gold: list[dict] = []
    shuffled = list(orders)
    rng.shuffle(shuffled)
    for j in range(min(cfg.precedence_chain, len(orders) - 1)):
        gold.append({
            "type": "PRECEDENCE",
            "before": shuffled[j].id,
            "after":  shuffled[j+1].id,
            "source": f"{shuffled[j].id} before {shuffled[j+1].id}",
        })

    request = _gold_to_request(gold, rng)
    label = {"feasible": True, "conflict": None}

    if cfg.family == "planted_infeasible":
        # Force infeasibility by forbidding the only refrigerated vehicle
        # from orders that need refrigeration
        fridge_orders = [o for o in orders if "refrigerated" in o.needs]
        fridge_vehicles = [v for v in vehicles if v.type == "refrigerated"]
        if fridge_orders and fridge_vehicles:
            for fo in fridge_orders:
                for fv in fridge_vehicles:
                    gold.append({
                        "type": "FORBIDDEN_VEHICLE",
                        "order": fo.id,
                        "vehicle": fv.id,
                        "source": f"don't use {fv.id} for {fo.id}",
                    })
            label = {
                "feasible": False,
                "conflict": {
                    "orders":   [fo.id for fo in fridge_orders],
                    "vehicles": [fv.id for fv in fridge_vehicles],
                    "rules":    ["R5"],
                },
            }

    change = "extend all deadlines by one slot"
    return Scenario(
        orders=orders,
        vehicles=vehicles,
        request=request,
        gold_constraints=gold,
        label=label,
        change=change,
    )


def _gold_to_request(gold: list[dict], rng: random.Random) -> str:
    parts = []
    for g in gold:
        if g["type"] == "PRECEDENCE":
            parts.append(f"{g['before']} must go before {g['after']}")
        elif g["type"] == "FORBIDDEN_VEHICLE":
            parts.append(f"don't put {g['order']} on {g['vehicle']}")
        elif g["type"] == "REQUIRED_VEHICLE":
            parts.append(f"use {g['vehicle']} for {g['order']}")
    if not parts:
        return "No special instructions today."
    return "; ".join(parts) + "."


def save_case(scenario: Scenario, path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)

    (path / "orders.json").write_text(
        json.dumps([o.model_dump() for o in scenario.orders], indent=2)
    )
    (path / "fleet.json").write_text(
        json.dumps([v.model_dump() for v in scenario.vehicles], indent=2)
    )
    (path / "request.txt").write_text(scenario.request)
    (path / "gold_constraints.json").write_text(
        json.dumps(scenario.gold_constraints, indent=2)
    )
    (path / "label.json").write_text(json.dumps(scenario.label, indent=2))
    if scenario.change:
        (path / "change.txt").write_text(scenario.change)
