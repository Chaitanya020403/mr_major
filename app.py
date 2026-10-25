"""
Streamlit demo — single-page dispatch planner.

Run:
    streamlit run app.py
"""

from __future__ import annotations
import json
import os
from pathlib import Path

import streamlit as st

from core.schema import FRAGILE_MAX_LOAD

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def _default_fleet_raw():
    return json.loads(Path("data/fleet_default.json").read_text())


@st.cache_data(show_spinner=False)
def _excel_template() -> bytes:
    from core.excel_loader import build_template
    return build_template(
        json.loads((WORKED_EXAMPLE / "orders.json").read_text()),
        json.loads((WORKED_EXAMPLE / "fleet.json").read_text()),
    )


def _load_upload(uploaded, expect: str) -> tuple[list[dict] | None, list[dict] | None]:
    """
    Returns (orders, fleet) from an uploaded Excel, CSV or JSON file.
    A JSON file holds only the expected kind; an Excel orders file may also carry a Fleet sheet.
    """
    if uploaded is None:
        return None, None
    data = uploaded.getvalue()
    if uploaded.name.lower().endswith(".json"):
        try:
            raw = json.loads(data)
        except Exception as e:
            st.error(f"Could not parse JSON in '{uploaded.name}': {e}")
            return None, None
        return (raw, None) if expect == "orders" else (None, raw)

    from core.excel_loader import load_spreadsheet
    res = load_spreadsheet(uploaded.name, data, expect=expect)
    if not res.ok:
        shown = res.errors[:15]
        more = f"\n- …and {len(res.errors) - 15} more" if len(res.errors) > 15 else ""
        st.error(f"Please fix '{uploaded.name}':\n- " + "\n- ".join(shown) + more)
        return None, None
    return res.orders, res.fleet


CLARIFY_PREFIX = "The LLM needs clarification: "
SOLVER_TIMEOUT_MS = 20_000   # near-full fleets can take minutes to prove infeasible


def _show_infeasible(exp, show_conflict: bool = True) -> None:
    if exp.timed_out:
        st.warning(exp.summary)
        return
    st.error(exp.summary)
    if show_conflict:
        st.json(exp.conflict_json)


def _show_pipeline_error(err: str) -> None:
    if err.startswith(CLARIFY_PREFIX):
        st.warning(err)
    else:
        st.error(err)


def _run_pipeline(orders_raw, fleet_raw, request, provider, model, base_request=None):
    """base_request: for replanning, the original instructions whose constraints still apply."""
    from core.data_validator import validate_orders, validate_fleet
    from core.translator import translate
    from core.json_validator import validate_constraints
    from core.solver import solve
    from core.explainer import explain

    orders, ov = validate_orders(orders_raw)
    vehicles, fv = validate_fleet(fleet_raw)
    if not ov.ok or not fv.ok:
        return None, None, None, None, f"Data validation error:\n{ov}\n{fv}"

    try:
        raw_json = []
        for req in [base_request, request]:
            if req and req.strip():
                part, _ = translate(req, orders, vehicles, provider, model)
                raw_json.extend(part)
    except Exception as e:
        return None, None, None, None, f"LLM translation failed: {e}"

    clarify = [c for c in raw_json if isinstance(c, dict) and c.get("type") == "AMBIGUOUS"]
    if clarify:
        question = clarify[0].get("question") or "Please clarify your request."
        return None, raw_json, None, None, CLARIFY_PREFIX + question

    cv = validate_constraints(raw_json, orders, vehicles)
    if not cv.ok:
        return None, raw_json, None, None, f"Constraint validation error:\n{cv}"

    result = solve(orders, vehicles, cv.constraints, timeout_ms=SOLVER_TIMEOUT_MS)
    exp = explain(result, orders, vehicles)
    return orders, raw_json, result, exp, None


def _run_llm_alone(orders_raw, fleet_raw, request, provider, model):
    from core.data_validator import validate_orders, validate_fleet
    from core.llm_alone import plan_with_llm_alone

    orders, ov = validate_orders(orders_raw)
    vehicles, fv = validate_fleet(fleet_raw)
    if not ov.ok or not fv.ok:
        return None, f"Data validation error:\n{ov}\n{fv}"

    try:
        parsed, raw = plan_with_llm_alone(request, orders, vehicles, provider, model)
    except Exception as e:
        return None, f"LLM-alone failed: {e}"

    return parsed, None


WORKED_EXAMPLE = Path("data/cases/case_000")


def _referee_constraints(orders_raw, fleet_raw, request, translated):
    """
    Constraints the referee checks plans against, plus a label saying where they came from.
    Gold constraints exist only for the unmodified worked example; otherwise fall back to
    the LLM + Z3 translation (so the referee still enforces the base rulebook plus the request).
    """
    from core.data_validator import validate_orders, validate_fleet
    from core.json_validator import validate_constraints

    orders_v, _ = validate_orders(orders_raw)
    vehicles_v, _ = validate_fleet(fleet_raw)
    is_worked_example = (
        orders_raw == json.loads((WORKED_EXAMPLE / "orders.json").read_text())
        and fleet_raw == json.loads((WORKED_EXAMPLE / "fleet.json").read_text())
        and request.strip() == (WORKED_EXAMPLE / "request.txt").read_text().strip()
    )
    if is_worked_example:
        raw, label = json.loads((WORKED_EXAMPLE / "gold_constraints.json").read_text()), "gold constraints"
    elif translated is not None:
        raw, label = translated, "LLM + Z3 translated constraints"
    else:
        return orders_v, vehicles_v, None, None
    gc = validate_constraints(raw, orders_v, vehicles_v)
    return orders_v, vehicles_v, (gc.constraints if gc.ok else None), label


# ---------------------------------------------------------------------------
# Page layout
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Warehouse Dispatch Planner", layout="wide")
st.title("Warehouse Dispatch Planner")
st.caption("AID-851 Machine Reasoning · LLM + Z3 vs LLM-alone")

# Sidebar — configuration
with st.sidebar:
    st.header("Configuration")
    provider = st.selectbox("LLM provider", ["groq", "openai", "anthropic"])
    default_models = {"groq": "openai/gpt-oss-120b", "openai": "gpt-4o", "anthropic": "claude-sonnet-4-6"}
    model = st.text_input("Model", value=default_models[provider])
    env_key = {"groq": "GROQ_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}[provider]
    api_key = st.text_input("API key", type="password", value=os.environ.get(env_key, ""))
    if api_key:
        os.environ[env_key] = api_key

    st.divider()
    st.header("Inputs")
    st.download_button(
        "Download Excel template",
        data=_excel_template(),
        file_name="dispatch_template.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        help="Fill in your orders (and vehicles, on the Fleet sheet), then upload it below.",
    )
    orders_file = st.file_uploader("Orders (Excel, CSV or JSON)", type=["xlsx", "csv", "json"])
    fleet_file  = st.file_uploader(
        "Fleet (optional)", type=["xlsx", "csv", "json"],
        help="Not needed if your Excel file has a Fleet sheet. Otherwise the default T1/T2/T3 fleet is used.",
    )

# Main area
# Filled in after inputs are resolved, but shown above the instructions
data_area = st.container()

col_left, col_right = st.columns([1, 1])

with col_left:
    st.subheader("Today's instructions")
    request = st.text_area(
        "Natural-language request",
        value="B must go before C, and don't put A on T2.",
        height=80,
    )

    run_plan   = st.button("Plan (LLM + Z3)", type="primary")
    run_compare = st.button("Compare with LLM-alone")
    show_rules  = st.button("Show rulebook")

    if show_rules:
        st.info(
            "R1 Every order assigned exactly once\n"
            "R2 Capacity per vehicle per slot\n"
            "R3 Vehicle availability\n"
            "R4 Delivery window / deadline\n"
            "R5 Special-handling compatibility\n"
            "R6 Urgent orders by deadline\n"
            "R7 Precedence between orders\n"
            "R8 User restrictions\n"
            f"R9 Fragile: vehicle at most {FRAGILE_MAX_LOAD:.0%} loaded, no oversized orders in the same trip"
        )

with col_right:
    st.subheader("Replan")
    change_request = st.text_area("Change or fix", value="Extend A's deadline to 11AM.", height=80)
    run_replan = st.button("Replan (LLM + Z3)")

# ---------------------------------------------------------------------------
# Resolve inputs
# ---------------------------------------------------------------------------

orders_raw, sheet_fleet = _load_upload(orders_file, "orders")
_, uploaded_fleet = _load_upload(fleet_file, "fleet")
# Fleet precedence: separate fleet file > Fleet sheet in the orders workbook > default fleet
fleet_raw = uploaded_fleet or sheet_fleet or _default_fleet_raw()

if orders_file is None:
    # Use the worked example by default
    orders_raw = json.loads((WORKED_EXAMPLE / "orders.json").read_text())
    fleet_raw  = uploaded_fleet or json.loads((WORKED_EXAMPLE / "fleet.json").read_text())
    st.info("Using worked example (orders A, B, C + fleet T1/T2). Upload your own files to override.")

# ---------------------------------------------------------------------------
# Editable data tables — the planner uses whatever is shown here
# ---------------------------------------------------------------------------

def _upload_key(f) -> str:
    return "" if f is None else str(getattr(f, "file_id", None) or f"{f.name}:{f.size}")


if orders_raw:
    from core.excel_loader import (
        NEED_COLUMNS, build_template, fleet_from_table, fleet_to_table,
        orders_from_table, orders_to_table,
    )
    from core.schema import VALID_SLOTS
    from core.data_validator import KNOWN_VEHICLE_TYPES

    with data_area:
        st.subheader("Orders and vehicles")
        try:
            orders_table = orders_to_table(orders_raw, checkboxes=True)
            fleet_table = fleet_to_table(fleet_raw, checkboxes=True)
        except Exception:
            # Malformed JSON records: the pipeline's data validator reports the details
            orders_table = fleet_table = None
            st.json({"orders": orders_raw, "fleet": fleet_raw})

    if orders_table is not None:
        with data_area:
            st.caption("Click a cell to change it. Add rows at the bottom of a table; select rows "
                       "to delete them. Planning uses the tables exactly as shown.")
            slot_col = st.column_config.SelectboxColumn(options=VALID_SLOTS)
            # A new upload gets fresh tables; otherwise edits persist across reruns
            orders_key = _upload_key(orders_file) or "example"
            st.caption("Orders")
            orders_df = st.data_editor(
                orders_table, key=f"orders_editor_{orders_key}",
                num_rows="dynamic", hide_index=True,
                column_config={
                    "Order ID": st.column_config.TextColumn(required=True),
                    "Weight (kg)": st.column_config.NumberColumn(min_value=0, required=True),
                    "Deadline": slot_col, "Earliest": slot_col, "Latest": slot_col,
                    "Priority": st.column_config.SelectboxColumn(
                        options=["Normal", "Urgent"], default="Normal"),
                    **{c: st.column_config.CheckboxColumn(default=False) for c in NEED_COLUMNS},
                },
            )
            st.caption("Fleet" + ("" if uploaded_fleet or sheet_fleet or orders_file is None
                                  else " (default — no fleet uploaded)"))
            fleet_df = st.data_editor(
                fleet_table, key=f"fleet_editor_{orders_key}_{_upload_key(fleet_file)}",
                num_rows="dynamic", hide_index=True,
                column_config={
                    "Vehicle ID": st.column_config.TextColumn(required=True),
                    "Capacity (kg)": st.column_config.NumberColumn(min_value=0, required=True),
                    "Type": st.column_config.SelectboxColumn(
                        options=sorted(t.capitalize() for t in KNOWN_VEHICLE_TYPES),
                        default="Normal"),
                    **{s: st.column_config.CheckboxColumn(default=False) for s in VALID_SLOTS},
                },
            )

            edited_orders, order_errors = orders_from_table(orders_df, first_row=1)
            edited_fleet, fleet_errors = fleet_from_table(fleet_df, first_row=1)
            if order_errors or fleet_errors:
                st.error("Please fix the tables:\n- " + "\n- ".join(order_errors + fleet_errors))
                orders_raw = None
            else:
                orders_raw, fleet_raw = edited_orders, edited_fleet
                st.download_button(
                    "Download these tables as Excel", data=build_template(orders_raw, fleet_raw, is_example=False),
                    file_name="dispatch_data.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )

if (run_plan or run_compare or run_replan) and not orders_raw:
    st.error("Fix the order and fleet data above before planning.")

# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

if run_plan and orders_raw:
    with st.spinner("Translating and solving…"):
        orders, raw_json, result, exp, err = _run_pipeline(
            orders_raw, fleet_raw, request, provider, model
        )
    if err:
        _show_pipeline_error(err)
    else:
        st.subheader("Result — LLM + Z3")
        if exp.feasible:
            st.success(exp.summary)
            st.dataframe(exp.plan_table)
        else:
            _show_infeasible(exp)

        with st.expander("LLM translated constraints"):
            st.json(raw_json)

# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------

if run_compare and orders_raw:
    from referee.checker import check_plan
    col_z3, col_alone = st.columns(2)

    with col_z3:
        st.subheader("LLM + Z3")
        with st.spinner("Solving…"):
            orders, raw_json, result, exp, err = _run_pipeline(
                orders_raw, fleet_raw, request, provider, model
            )
        if err:
            _show_pipeline_error(err)
        elif exp.feasible:
            st.success(exp.summary)
            st.dataframe(exp.plan_table)
        else:
            _show_infeasible(exp, show_conflict=False)

    translated = raw_json if not err else None
    orders_v, vehicles_v, ref_constraints, ref_label = _referee_constraints(
        orders_raw, fleet_raw, request, translated
    )

    with col_z3:
        if result and result.feasible and ref_constraints is not None:
            check = check_plan(result.plan, orders_v, vehicles_v, ref_constraints)
            st.caption(f"Referee checked against: {ref_label}")
            if check.valid:
                st.success("Referee: VALID plan")
            else:
                st.error(f"Referee: INVALID\n{check}")

    with col_alone:
        st.subheader("LLM-alone (baseline)")
        with st.spinner("LLM planning…"):
            parsed, err = _run_llm_alone(orders_raw, fleet_raw, request, provider, model)
        if err:
            st.error(err)
        elif parsed and parsed.get("feasible"):
            st.success("Feasible (LLM-alone)")
            st.json(parsed.get("plan", {}))
            if ref_constraints is None:
                st.caption("Referee skipped: no reference constraints for this request.")
            else:
                try:
                    plan = {oid: (v["vehicle"], v["slot"]) for oid, v in parsed["plan"].items()}
                    check = check_plan(plan, orders_v, vehicles_v, ref_constraints)
                    st.caption(f"Referee checked against: {ref_label}")
                    if check.valid:
                        st.success("Referee: VALID")
                    else:
                        st.error(f"Referee: INVALID\n{check}")
                except Exception as e:
                    st.error(f"Referee: LLM-alone plan is malformed ({e})")
        else:
            st.error("Infeasible (LLM-alone)")
            st.json(parsed or {})

# ---------------------------------------------------------------------------
# Replan
# ---------------------------------------------------------------------------

if run_replan and orders_raw:
    with st.spinner("Replanning…"):
        orders, raw_json, result, exp, err = _run_pipeline(
            orders_raw, fleet_raw, change_request, provider, model, base_request=request
        )
    if err:
        _show_pipeline_error(err)
    else:
        st.subheader("Replan result")
        if exp.feasible:
            st.success(exp.summary)
            st.dataframe(exp.plan_table)
        else:
            _show_infeasible(exp)
        with st.expander("Translated constraints for change"):
            st.json(raw_json)
