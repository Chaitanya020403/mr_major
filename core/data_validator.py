"""
Validates orders.json and fleet.json before anything reaches Z3.

Rules checked:
- Unique IDs within each file
- Positive weights and capacities
- All slots in VALID_SLOTS
- Known vehicle types and order needs
- Delivery windows are consistent (earliest <= latest, both valid slots)
"""

from __future__ import annotations
from typing import Any
from pydantic import ValidationError

from core.schema import Order, Vehicle, VALID_SLOTS

KNOWN_VEHICLE_TYPES = {"normal", "refrigerated", "heavy"}
KNOWN_ORDER_NEEDS = {"refrigerated", "heavy", "fragile"}


class ValidationResult:
    def __init__(self) -> None:
        self.errors: list[str] = []

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def __str__(self) -> str:
        return "\n".join(self.errors) if self.errors else "OK"


def validate_orders(raw: list[dict[str, Any]]) -> tuple[list[Order], ValidationResult]:
    result = ValidationResult()
    orders: list[Order] = []
    seen_ids: set[str] = set()

    for i, item in enumerate(raw):
        try:
            order = Order.model_validate(item)
        except ValidationError as e:
            result.errors.append(f"orders[{i}]: {e}")
            continue

        if order.id in seen_ids:
            result.errors.append(f"Duplicate order ID: {order.id}")
        seen_ids.add(order.id)

        if order.weight <= 0:
            result.errors.append(f"Order {order.id}: weight must be positive")

        if order.deadline and order.deadline not in VALID_SLOTS:
            result.errors.append(f"Order {order.id}: unknown deadline slot '{order.deadline}'")

        if order.time_window:
            if len(order.time_window) != 2:
                result.errors.append(f"Order {order.id}: time_window must have exactly 2 elements")
            else:
                early, late = order.time_window
                if early not in VALID_SLOTS or late not in VALID_SLOTS:
                    result.errors.append(f"Order {order.id}: unknown slot in time_window")
                elif VALID_SLOTS.index(early) > VALID_SLOTS.index(late):
                    result.errors.append(f"Order {order.id}: time_window earliest > latest")

        for need in order.needs:
            if need not in KNOWN_ORDER_NEEDS:
                result.errors.append(f"Order {order.id}: unknown need '{need}'")

        orders.append(order)

    return orders, result


def validate_fleet(raw: list[dict[str, Any]]) -> tuple[list[Vehicle], ValidationResult]:
    result = ValidationResult()
    vehicles: list[Vehicle] = []
    seen_ids: set[str] = set()

    for i, item in enumerate(raw):
        try:
            vehicle = Vehicle.model_validate(item)
        except ValidationError as e:
            result.errors.append(f"fleet[{i}]: {e}")
            continue

        if vehicle.id in seen_ids:
            result.errors.append(f"Duplicate vehicle ID: {vehicle.id}")
        seen_ids.add(vehicle.id)

        if vehicle.capacity <= 0:
            result.errors.append(f"Vehicle {vehicle.id}: capacity must be positive")

        if vehicle.type not in KNOWN_VEHICLE_TYPES:
            result.errors.append(f"Vehicle {vehicle.id}: unknown type '{vehicle.type}'")

        vehicles.append(vehicle)

    return vehicles, result
