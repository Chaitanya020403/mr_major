"""
Constraint schema shared by the LLM translator, JSON validator, and Z3 solver.
All constraint types the LLM is allowed to emit are defined here.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal, Optional
from pydantic import BaseModel, model_validator


VALID_SLOTS = ["9AM", "11AM", "1PM", "3PM"]

ConstraintType = Literal[
    "DEADLINE",
    "TIME_WINDOW",
    "URGENT_DELIVERY",
    "PRECEDENCE",
    "FORBIDDEN_VEHICLE",
    "REQUIRED_VEHICLE",
    "FORBIDDEN_SLOT",
    "VEHICLE_UNAVAILABLE",
    "CHANGE_DEADLINE",
    "ADD_ORDER",           # optional extension
]


# ---------------------------------------------------------------------------
# Data models for orders.json and fleet.json
# ---------------------------------------------------------------------------

class Order(BaseModel):
    id: str
    weight: float
    deadline: Optional[str] = None          # e.g. "9AM"
    time_window: Optional[list[str]] = None # [earliest, latest]
    priority: Literal["normal", "urgent"] = "normal"
    needs: list[str] = field(default_factory=list)  # e.g. ["refrigerated"]


class Vehicle(BaseModel):
    id: str
    capacity: float
    type: str = "normal"            # e.g. "normal", "refrigerated"
    available_slots: list[str]

    @model_validator(mode="after")
    def slots_must_be_known(self) -> "Vehicle":
        bad = [s for s in self.available_slots if s not in VALID_SLOTS]
        if bad:
            raise ValueError(f"Unknown slots {bad} for vehicle {self.id}")
        return self


# ---------------------------------------------------------------------------
# Constraint objects produced by the LLM translator
# ---------------------------------------------------------------------------

class BaseConstraint(BaseModel):
    type: ConstraintType
    source: str = ""   # verbatim fragment from the user request


class DeadlineConstraint(BaseConstraint):
    type: Literal["DEADLINE"]
    order: str
    slot: str


class TimeWindowConstraint(BaseConstraint):
    type: Literal["TIME_WINDOW"]
    order: str
    earliest: str
    latest: str


class UrgentDeliveryConstraint(BaseConstraint):
    type: Literal["URGENT_DELIVERY"]
    order: str
    deadline: str


class PrecedenceConstraint(BaseConstraint):
    type: Literal["PRECEDENCE"]
    before: str
    after: str


class ForbiddenVehicleConstraint(BaseConstraint):
    type: Literal["FORBIDDEN_VEHICLE"]
    order: str
    vehicle: str


class RequiredVehicleConstraint(BaseConstraint):
    type: Literal["REQUIRED_VEHICLE"]
    order: str
    vehicle: str


class ForbiddenSlotConstraint(BaseConstraint):
    type: Literal["FORBIDDEN_SLOT"]
    order: str
    slot: str


class VehicleUnavailableConstraint(BaseConstraint):
    type: Literal["VEHICLE_UNAVAILABLE"]
    vehicle: str
    slot: str


class ChangeDeadlineConstraint(BaseConstraint):
    type: Literal["CHANGE_DEADLINE"]
    order: str
    new_deadline: str


# Union type used by the JSON validator
from typing import Union, Annotated
from pydantic import Discriminator, Tag

AnyConstraint = Union[
    DeadlineConstraint,
    TimeWindowConstraint,
    UrgentDeliveryConstraint,
    PrecedenceConstraint,
    ForbiddenVehicleConstraint,
    RequiredVehicleConstraint,
    ForbiddenSlotConstraint,
    VehicleUnavailableConstraint,
    ChangeDeadlineConstraint,
]

# Map from type string to model class (used by the validator)
CONSTRAINT_CLASSES: dict[str, type[BaseConstraint]] = {
    "DEADLINE":            DeadlineConstraint,
    "TIME_WINDOW":         TimeWindowConstraint,
    "URGENT_DELIVERY":     UrgentDeliveryConstraint,
    "PRECEDENCE":          PrecedenceConstraint,
    "FORBIDDEN_VEHICLE":   ForbiddenVehicleConstraint,
    "REQUIRED_VEHICLE":    RequiredVehicleConstraint,
    "FORBIDDEN_SLOT":      ForbiddenSlotConstraint,
    "VEHICLE_UNAVAILABLE": VehicleUnavailableConstraint,
    "CHANGE_DEADLINE":     ChangeDeadlineConstraint,
}
