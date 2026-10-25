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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def _default_fleet_raw():
    return json.loads(Path("data/fleet_default.json").read_text())


def _load_json_upload(uploaded) -> list[dict] | None:
    if uploaded is None:
        return None
    try:
        return json.loads(uploaded.read())
    except Exception as e:
        st.error(f"Could not parse JSON: {e}")
        return None


CLARIFY_PREFIX = "The LLM needs clarification: "


def _show_pipeline_error(err: str) -> None:
    if err.startswith(CLARIFY_PREFIX):
        st.warning(err)
    else:
        st.error(err)


def _run_pipeline(orders_raw, fleet_raw, request, provider, model):
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
        raw_json, llm_text = translate(request, orders, vehicles, provider, model)
    except Exception as e:
        return None, None, None, None, f"LLM translation failed: {e}"

    clarify = [c for c in raw_json if isinstance(c, dict) and c.get("type") == "AMBIGUOUS"]
    if clarify:
        question = clarify[0].get("question") or "Please clarify your request."
        return None, raw_json, None, None, CLARIFY_PREFIX + question

    cv = validate_constraints(raw_json, orders, vehicles)
    if not cv.ok:
        return None, raw_json, None, None, f"Constraint validation error:\n{cv}"

    result = solve(orders, vehicles, cv.constraints)
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
    orders_file = st.file_uploader("orders.json", type="json")
    fleet_file  = st.file_uploader("fleet.json (optional — uses default T1/T2/T3)", type="json")

# Main area
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
            "R9 Soft preferences (minimise slot index)"
        )

with col_right:
    st.subheader("Replan")
    change_request = st.text_area("Change or fix", value="Extend A's deadline to 11AM.", height=80)
    run_replan = st.button("Replan (LLM + Z3)")

# ---------------------------------------------------------------------------
# Resolve inputs
# ---------------------------------------------------------------------------

orders_raw = _load_json_upload(orders_file)
fleet_raw  = _load_json_upload(fleet_file) or _default_fleet_raw()

if orders_file is None and orders_raw is None:
    # Use the worked example by default
    orders_raw = json.loads(Path("data/cases/case_000/orders.json").read_text())
    fleet_raw  = json.loads(Path("data/cases/case_000/fleet.json").read_text())
    st.info("Using worked example (orders A, B, C + fleet T1/T2). Upload your own files to override.")

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
            st.error(exp.summary)
            st.json(exp.conflict_json)

        with st.expander("LLM translated constraints"):
            st.json(raw_json)

# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------

if run_compare and orders_raw:
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
            st.error(exp.summary)

        if result:
            from referee.checker import check_plan
            from core.data_validator import validate_orders, validate_fleet
            from core.json_validator import validate_constraints
            gold_raw = json.loads(Path("data/cases/case_000/gold_constraints.json").read_text())
            orders_v, _ = validate_orders(orders_raw)
            vehicles_v, _ = validate_fleet(fleet_raw)
            gc = validate_constraints(gold_raw, orders_v, vehicles_v)
            if result.feasible:
                plan = result.plan
                check = check_plan(plan, orders_v, vehicles_v, gc.constraints)
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
            # Referee check
            try:
                from referee.checker import check_plan
                from core.data_validator import validate_orders, validate_fleet
                from core.json_validator import validate_constraints
                gold_raw = json.loads(Path("data/cases/case_000/gold_constraints.json").read_text())
                orders_v, _ = validate_orders(orders_raw)
                vehicles_v, _ = validate_fleet(fleet_raw)
                gc = validate_constraints(gold_raw, orders_v, vehicles_v)
                raw_plan = parsed["plan"]
                plan = {oid: (v["vehicle"], v["slot"]) for oid, v in raw_plan.items()}
                check = check_plan(plan, orders_v, vehicles_v, gc.constraints)
                if check.valid:
                    st.success("Referee: VALID")
                else:
                    st.error(f"Referee: INVALID\n{check}")
            except Exception:
                pass
        else:
            st.error("Infeasible (LLM-alone)")
            st.json(parsed or {})

# ---------------------------------------------------------------------------
# Replan
# ---------------------------------------------------------------------------

if run_replan and orders_raw:
    with st.spinner("Replanning…"):
        orders, raw_json, result, exp, err = _run_pipeline(
            orders_raw, fleet_raw, change_request, provider, model
        )
    if err:
        _show_pipeline_error(err)
    else:
        st.subheader("Replan result")
        if exp.feasible:
            st.success(exp.summary)
            st.dataframe(exp.plan_table)
        else:
            st.error(exp.summary)
            st.json(exp.conflict_json)
        with st.expander("Translated constraints for change"):
            st.json(raw_json)
