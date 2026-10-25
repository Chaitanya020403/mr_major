"""
Spreadsheet front end for orders and fleet data.

Converts friendly Excel / CSV tables into the same list-of-dicts format as
orders.json and fleet.json, so data_validator, solver and referee are unchanged.
Also builds the downloadable template workbook.

Orders sheet columns:
    Order ID | Weight (kg) | Deadline | Earliest | Latest | Priority | Refrigerated | Heavy
Fleet sheet columns:
    Vehicle ID | Capacity (kg) | Type | 9AM | 11AM | 1PM | 3PM
"""

from __future__ import annotations
import datetime as dt
import io
import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from core.schema import VALID_SLOTS
from core.data_validator import KNOWN_VEHICLE_TYPES, KNOWN_ORDER_NEEDS


ORDER_COLUMNS = ["Order ID", "Weight (kg)", "Deadline", "Earliest", "Latest", "Priority",
                 "Refrigerated", "Heavy"]
FLEET_COLUMNS = ["Vehicle ID", "Capacity (kg)", "Type"] + VALID_SLOTS
# Only needs some vehicle type can satisfy (R5 matches need == vehicle type); "fragile" has none
NEED_COLUMNS = {"Refrigerated": "refrigerated", "Heavy": "heavy"}
assert set(NEED_COLUMNS.values()) <= KNOWN_ORDER_NEEDS & KNOWN_VEHICLE_TYPES

# Alternative header spellings people are likely to type (compared after _norm_header)
_HEADER_ALIASES = {
    "id": "Order ID", "order": "Order ID", "orderno": "Order ID", "ordernumber": "Order ID",
    "weight": "Weight (kg)", "weightkg": "Weight (kg)", "kg": "Weight (kg)",
    "earliesttime": "Earliest", "from": "Earliest", "latesttime": "Latest", "to": "Latest",
    "fridge": "Refrigerated", "needsfridge": "Refrigerated", "cold": "Refrigerated",
    "vehicle": "Vehicle ID", "truck": "Vehicle ID", "truckid": "Vehicle ID",
    "capacity": "Capacity (kg)", "capacitykg": "Capacity (kg)", "vehicletype": "Type",
}

_HOUR_TO_SLOT = {9: "9AM", 11: "11AM", 13: "1PM", 15: "3PM"}
_YES = {"yes", "y", "true", "1", "x", "✓", "✔", "available"}
_NO = {"no", "n", "false", "0", "-", "unavailable"}
_VEHICLE_TYPE_ALIASES = {"fridge": "refrigerated", "cold": "refrigerated", "reefer": "refrigerated"}

ORDERS_SHEET = "Orders"
FLEET_SHEET = "Fleet"


@dataclass
class LoadResult:
    orders: list[dict[str, Any]] | None = None
    fleet: list[dict[str, Any]] | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class CellError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Cell parsers
# ---------------------------------------------------------------------------

def _blank(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _norm_header(h: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(h).lower())


def parse_slot(v: Any) -> str | None:
    """Accepts 9AM, 9 am, 9:00 AM, 09:00, 13:00, 1PM, 1, Excel time cells, ..."""
    if _blank(v):
        return None
    hour = minute = None
    if isinstance(v, (dt.time, dt.datetime, pd.Timestamp)):
        hour, minute = v.hour, v.minute
    elif isinstance(v, (int, float)):
        if 0 < v < 1:                      # Excel stores times as a fraction of a day
            total = round(v * 24 * 60)
            hour, minute = divmod(total, 60)
        elif float(v).is_integer():
            hour, minute = int(v), 0
    else:
        s = re.sub(r"[\s.]", "", str(v).upper())
        m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(?::\d{2})?(AM|PM)?", s)
        if m:
            hour, minute = int(m.group(1)), int(m.group(2) or 0)
            if m.group(3) == "PM" and hour < 12:
                hour += 12
            elif m.group(3) == "AM" and hour == 12:
                hour = 0
    if hour is not None and hour in (1, 3):  # "1" / "3" can only mean the afternoon slots
        hour += 12
    if minute == 0 and hour in _HOUR_TO_SLOT:
        return _HOUR_TO_SLOT[hour]
    raise CellError(f"'{v}' is not a delivery time. Use one of {', '.join(VALID_SLOTS)}.")


def parse_yes_no(v: Any) -> bool:
    if _blank(v):
        return False
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s.endswith(".0"):
        s = s[:-2]
    if s in _YES:
        return True
    if s in _NO:
        return False
    raise CellError(f"'{v}' should be Yes or No (or left empty for No).")


def parse_positive_number(v: Any, what: str) -> int | float:
    if _blank(v):
        raise CellError(f"{what} is empty.")
    try:
        x = float(re.sub(r"(?i)\s*kgs?$", "", str(v).strip()))
    except ValueError:
        raise CellError(f"{what} '{v}' is not a number.") from None
    if x <= 0:
        raise CellError(f"{what} must be more than 0 (got {v}).")
    return int(x) if x.is_integer() else x


def parse_id(v: Any) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


# ---------------------------------------------------------------------------
# Table → JSON-format records
# ---------------------------------------------------------------------------

def _canonical_columns(df: pd.DataFrame, expected: list[str]) -> dict[str, str]:
    """Map each expected column to the actual header in df (case/space/alias tolerant)."""
    lookup = {_norm_header(c): c for c in expected}
    lookup.update({k: v for k, v in _HEADER_ALIASES.items() if v in expected})
    found: dict[str, str] = {}
    for actual in df.columns:
        canon = lookup.get(_norm_header(actual))
        if canon and canon not in found:
            found[canon] = actual
    return found


def _rows(df: pd.DataFrame, cols: dict[str, str]):
    """Yield (excel_row_number, getter) for non-empty rows. Header is Excel row 1."""
    for i, (_, row) in enumerate(df.iterrows()):
        if all(_blank(v) for v in row.values):
            continue
        yield i + 2, (lambda name, row=row: row[cols[name]] if name in cols else None)


def orders_from_table(df: pd.DataFrame) -> tuple[list[dict[str, Any]], list[str]]:
    cols = _canonical_columns(df, ORDER_COLUMNS)
    missing = [c for c in ("Order ID", "Weight (kg)") if c not in cols]
    if missing:
        return [], [f"Orders sheet is missing column(s): {', '.join(missing)}. "
                    f"Expected columns: {', '.join(ORDER_COLUMNS)}."]

    orders, errors = [], []
    for rownum, get in _rows(df, cols):
        oid = parse_id(get("Order ID")) if not _blank(get("Order ID")) else ""
        where = f"Orders row {rownum}" + (f" (order {oid})" if oid else "")
        row_errors = []

        def cell(fn, *args):
            try:
                return fn(*args)
            except CellError as e:
                row_errors.append(f"{where}: {e}")
                return None

        if not oid:
            row_errors.append(f"{where}: Order ID is empty.")
        weight = cell(parse_positive_number, get("Weight (kg)"), "Weight")
        deadline = cell(parse_slot, get("Deadline"))
        earliest = cell(parse_slot, get("Earliest"))
        latest = cell(parse_slot, get("Latest"))
        needs = [need for col, need in NEED_COLUMNS.items() if cell(parse_yes_no, get(col))]

        priority_raw = get("Priority")
        priority = "normal" if _blank(priority_raw) else str(priority_raw).strip().lower()
        if priority not in ("normal", "urgent"):
            row_errors.append(f"{where}: Priority '{priority_raw}' should be Normal or Urgent.")

        time_window = None
        if _blank(get("Earliest")) != _blank(get("Latest")):
            row_errors.append(f"{where}: fill in both Earliest and Latest, or leave both empty.")
        elif earliest and latest:
            if VALID_SLOTS.index(earliest) > VALID_SLOTS.index(latest):
                row_errors.append(f"{where}: Earliest ({earliest}) is after Latest ({latest}).")
            time_window = [earliest, latest]

        if row_errors:
            errors.extend(row_errors)
            continue
        record: dict[str, Any] = {"id": oid, "weight": weight}
        if deadline:
            record["deadline"] = deadline
        if time_window:
            record["time_window"] = time_window
        record["priority"] = priority
        record["needs"] = needs
        orders.append(record)

    errors.extend(_duplicate_id_errors(orders, "Orders", "order"))
    if not orders and not errors:
        errors.append("Orders sheet has no orders in it.")
    return orders, errors


def fleet_from_table(df: pd.DataFrame) -> tuple[list[dict[str, Any]], list[str]]:
    cols = _canonical_columns(df, FLEET_COLUMNS)
    missing = [c for c in ("Vehicle ID", "Capacity (kg)") if c not in cols]
    if missing:
        return [], [f"Fleet sheet is missing column(s): {', '.join(missing)}. "
                    f"Expected columns: {', '.join(FLEET_COLUMNS)}."]
    if not any(s in cols for s in VALID_SLOTS):
        return [], [f"Fleet sheet needs one column per time slot ({', '.join(VALID_SLOTS)}) "
                    "marked Yes when the vehicle is available."]

    fleet, errors = [], []
    for rownum, get in _rows(df, cols):
        vid = parse_id(get("Vehicle ID")) if not _blank(get("Vehicle ID")) else ""
        where = f"Fleet row {rownum}" + (f" (vehicle {vid})" if vid else "")
        row_errors = []
        if not vid:
            row_errors.append(f"{where}: Vehicle ID is empty.")
        try:
            capacity = parse_positive_number(get("Capacity (kg)"), "Capacity")
        except CellError as e:
            row_errors.append(f"{where}: {e}")

        type_raw = get("Type")
        vtype = "normal" if _blank(type_raw) else str(type_raw).strip().lower()
        vtype = _VEHICLE_TYPE_ALIASES.get(vtype, vtype)
        if vtype not in KNOWN_VEHICLE_TYPES:
            row_errors.append(f"{where}: Type '{type_raw}' should be one of "
                              f"{', '.join(sorted(t.capitalize() for t in KNOWN_VEHICLE_TYPES))}.")

        slots = []
        for slot in VALID_SLOTS:
            try:
                if parse_yes_no(get(slot)):
                    slots.append(slot)
            except CellError as e:
                row_errors.append(f"{where}, column {slot}: {e}")
        if not slots and not row_errors:
            row_errors.append(f"{where}: vehicle is not available in any slot (mark at least one Yes).")

        if row_errors:
            errors.extend(row_errors)
            continue
        fleet.append({"id": vid, "capacity": capacity, "type": vtype, "available_slots": slots})

    errors.extend(_duplicate_id_errors(fleet, "Fleet", "vehicle"))
    if not fleet and not errors:
        errors.append("Fleet sheet has no vehicles in it.")
    return fleet, errors


def _duplicate_id_errors(records: list[dict[str, Any]], sheet: str, what: str) -> list[str]:
    seen, dups = set(), []
    for r in records:
        if r["id"] in seen and r["id"] not in dups:
            dups.append(r["id"])
        seen.add(r["id"])
    return [f"{sheet} sheet: {what} ID '{d}' appears more than once." for d in dups]


# ---------------------------------------------------------------------------
# File loading
# ---------------------------------------------------------------------------

def load_spreadsheet(filename: str, data: bytes, expect: str = "orders") -> LoadResult:
    """
    expect="orders": .xlsx reads the 'Orders' sheet (or the first sheet) and, if present,
                     the 'Fleet' sheet; .csv is read as orders.
    expect="fleet":  .xlsx reads the 'Fleet' sheet (or the first sheet); .csv is read as fleet.
    """
    result = LoadResult()
    try:
        if filename.lower().endswith(".csv"):
            df = pd.read_csv(io.BytesIO(data), dtype=object, skipinitialspace=True)
            if expect == "fleet":
                result.fleet, result.errors = fleet_from_table(df)
            else:
                result.orders, result.errors = orders_from_table(df)
            return result

        sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, dtype=object)
    except Exception:
        result.errors.append(f"Could not read '{filename}'. Make sure it is an Excel (.xlsx) or "
                             "CSV file, e.g. saved from Excel or Google Sheets.")
        return result

    by_name = {n.strip().lower(): df for n, df in sheets.items()}
    first = next(iter(sheets.values()))
    if expect == "fleet":
        result.fleet, result.errors = fleet_from_table(by_name.get(FLEET_SHEET.lower(), first))
        return result

    result.orders, errs = orders_from_table(by_name.get(ORDERS_SHEET.lower(), first))
    result.errors.extend(errs)
    if FLEET_SHEET.lower() in by_name:
        result.fleet, errs = fleet_from_table(by_name[FLEET_SHEET.lower()])
        result.errors.extend(errs)
    return result


# ---------------------------------------------------------------------------
# JSON-format records → friendly tables (for previews and the template)
# ---------------------------------------------------------------------------

def orders_to_table(orders: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for o in orders:
        tw = o.get("time_window") or [None, None]
        row = {"Order ID": o["id"], "Weight (kg)": o["weight"], "Deadline": o.get("deadline"),
               "Earliest": tw[0], "Latest": tw[1],
               "Priority": o.get("priority", "normal").capitalize()}
        for col, need in NEED_COLUMNS.items():
            row[col] = "Yes" if need in o.get("needs", []) else "No"
        rows.append(row)
    return pd.DataFrame(rows, columns=ORDER_COLUMNS)


def fleet_to_table(fleet: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for v in fleet:
        row = {"Vehicle ID": v["id"], "Capacity (kg)": v["capacity"],
               "Type": v.get("type", "normal").capitalize()}
        for slot in VALID_SLOTS:
            row[slot] = "Yes" if slot in v["available_slots"] else "No"
        rows.append(row)
    return pd.DataFrame(rows, columns=FLEET_COLUMNS)


def build_template(example_orders: list[dict[str, Any]],
                   example_fleet: list[dict[str, Any]]) -> bytes:
    """Excel workbook with Orders, Fleet and How-to sheets, dropdowns on every choice column."""
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        orders_to_table(example_orders).to_excel(writer, sheet_name=ORDERS_SHEET, index=False)
        fleet_to_table(example_fleet).to_excel(writer, sheet_name=FLEET_SHEET, index=False)
        pd.DataFrame({"How to fill in this file": HOW_TO_LINES}).to_excel(
            writer, sheet_name="How to fill", index=False)

        wb = writer.book
        header_fill = PatternFill("solid", fgColor="DDEBF7")
        for ws in wb.worksheets:
            ws.freeze_panes = "A2"
            for c in ws[1]:
                c.font = Font(bold=True)
                c.fill = header_fill
            for i, col in enumerate(ws.columns, start=1):
                width = max(len(str(c.value or "")) for c in col) + 3
                ws.column_dimensions[get_column_letter(i)].width = min(max(width, 10), 90)
        wb["How to fill"].column_dimensions["A"].width = 100
        for row in wb["How to fill"].iter_rows(min_row=2):
            row[0].alignment = Alignment(wrap_text=True)

        def dropdown(ws, columns: list[str], header: list[str], choices: list[str]):
            dv = DataValidation(type="list", formula1='"' + ",".join(choices) + '"',
                                allow_blank=True, showErrorMessage=True,
                                errorTitle="Invalid value",
                                error="Pick a value from the list: " + ", ".join(choices))
            ws.add_data_validation(dv)
            for name in columns:
                letter = get_column_letter(header.index(name) + 1)
                dv.add(f"{letter}2:{letter}500")

        dropdown(wb[ORDERS_SHEET], ["Deadline", "Earliest", "Latest"], ORDER_COLUMNS, VALID_SLOTS)
        dropdown(wb[ORDERS_SHEET], ["Priority"], ORDER_COLUMNS, ["Normal", "Urgent"])
        dropdown(wb[ORDERS_SHEET], list(NEED_COLUMNS), ORDER_COLUMNS, ["Yes", "No"])
        dropdown(wb[FLEET_SHEET], ["Type"], FLEET_COLUMNS,
                 sorted(t.capitalize() for t in KNOWN_VEHICLE_TYPES))
        dropdown(wb[FLEET_SHEET], VALID_SLOTS, FLEET_COLUMNS, ["Yes", "No"])
    return buf.getvalue()


HOW_TO_LINES = [
    "The rows already filled in are an example. Replace them with your own orders and vehicles.",
    "ORDERS sheet: one row per order.",
    "  Order ID: any short name, e.g. A, B, 101. Each order needs a different ID.",
    "  Weight (kg): how heavy the order is.",
    f"  Times: deliveries happen in slots {', '.join(VALID_SLOTS)}. Pick from the dropdown.",
    "  Deadline: the latest time the order may be delivered (optional).",
    "  Earliest / Latest: the time window the customer can receive it (optional; fill both or neither).",
    "  Priority: Normal or Urgent.",
    "  Refrigerated / Heavy: Yes if the order must go on a Refrigerated / Heavy vehicle.",
    "FLEET sheet: one row per vehicle. If you delete this sheet, the app's default fleet is used.",
    "  Vehicle ID: e.g. T1. Capacity (kg): the most it can carry in one trip.",
    "  Type: Normal, Refrigerated or Heavy.",
    f"  {' / '.join(VALID_SLOTS)}: Yes if the vehicle is available at that time.",
    "Then upload this file in the app and type your instructions in plain English.",
]
