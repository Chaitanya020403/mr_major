"""
LLM-alone baseline.

Receives the same inputs as the LLM + Z3 pipeline (orders, fleet, rulebook, request)
and returns a plan or an infeasibility claim directly — with no Z3 involved.

The prompt is designed to be as helpful as possible to the LLM so results are fair:
- Full rulebook in plain English
- Self-check step asking the LLM to verify its own plan
- Structured output schema identical to what the referee expects
"""

from __future__ import annotations
import json
import os
import logging
from typing import Any

from core.schema import Order, Vehicle, VALID_SLOTS, FRAGILE_MAX_LOAD

logger = logging.getLogger(__name__)

RULEBOOK = f"""
Rules that must ALL be satisfied:
R1. Every order assigned to exactly one vehicle and one slot.
R2. Total weight on a vehicle in a slot must not exceed its capacity.
R3. A vehicle can only be used in slots where it is available.
R4. An order can only be dispatched within its delivery window (or by its deadline).
R5. An order that needs refrigeration can only go on a refrigerated vehicle;
    an oversized order can only go on an oversized vehicle.
R6. An urgent order must leave by its stated deadline.
R7. If order X must go before order Y, then slot(X) < slot(Y).
R8. Any user-stated restrictions (forbidden vehicle, forbidden slot) must be respected.
R9. A vehicle carrying a fragile order in a slot may be loaded to at most {FRAGILE_MAX_LOAD:.0%} of its
    capacity in that slot, and may not carry any oversized order in that slot.
"""

SYSTEM_PROMPT = """You are a warehouse dispatch planner.
Given orders, a fleet and today's instructions, produce either a valid dispatch plan
or explain why no plan is possible.

{rulebook}

Output format — choose exactly one:
A) If a valid plan exists:
{{"feasible": true, "plan": {{"<order_id>": {{"vehicle": "<vehicle_id>", "slot": "<slot>"}}, ...}}}}

B) If no valid plan exists:
{{"feasible": false, "conflict": {{"orders": [...], "vehicles": [...], "rules": ["R1",...]}}, "reason": "<short explanation>"}}

Think step by step, then output only the JSON — no prose before or after.
After producing the JSON, re-read your plan and verify every rule holds.
If you find a violation, output a corrected JSON instead.

Valid slots: {slots}
"""


def _format_orders(orders: list[Order]) -> str:
    rows = []
    for o in orders:
        parts = [f"id={o.id}", f"weight={o.weight}kg"]
        if o.deadline:
            parts.append(f"deadline={o.deadline}")
        if o.time_window:
            parts.append(f"window={o.time_window[0]}-{o.time_window[1]}")
        if o.priority == "urgent":
            parts.append("URGENT")
        if o.needs:
            parts.append(f"needs={o.needs}")
        rows.append("  " + ", ".join(parts))
    return "\n".join(rows)


def _format_fleet(vehicles: list[Vehicle]) -> str:
    rows = []
    for v in vehicles:
        rows.append(
            f"  id={v.id}, capacity={v.capacity}kg, type={v.type}, "
            f"available={v.available_slots}"
        )
    return "\n".join(rows)


def _call_llm(system: str, user: str, provider: str, model: str) -> str:
    if provider == "openai":
        import openai
        client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        resp = client.chat.completions.create(
            model=model, temperature=0,
            messages=[{"role": "system", "content": system},
                      {"role": "user",   "content": user}],
        )
        return resp.choices[0].message.content or ""
    elif provider == "groq":
        import openai
        client = openai.OpenAI(
            api_key=os.environ["GROQ_API_KEY"],
            base_url="https://api.groq.com/openai/v1",
        )
        resp = client.chat.completions.create(
            model=model, temperature=0,
            messages=[{"role": "system", "content": system},
                      {"role": "user",   "content": user}],
        )
        return resp.choices[0].message.content or ""
    elif provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        msg = client.messages.create(
            model=model, max_tokens=2048, temperature=0,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return msg.content[0].text
    raise ValueError(f"Unknown provider '{provider}'")


def plan_with_llm_alone(
    request: str,
    orders: list[Order],
    vehicles: list[Vehicle],
    provider: str = "groq",
    model: str | None = None,
) -> tuple[dict[str, Any], str]:
    """
    Returns (parsed_response_dict, raw_llm_text).
    parsed_response_dict has keys: feasible, plan (if SAT), conflict + reason (if UNSAT).
    """
    system = SYSTEM_PROMPT.format(rulebook=RULEBOOK, slots=VALID_SLOTS)
    user = (
        f"Orders:\n{_format_orders(orders)}\n\n"
        f"Fleet:\n{_format_fleet(vehicles)}\n\n"
        f"Today's instruction: {request}"
    )

    default_models = {"openai": "gpt-4o", "groq": "openai/gpt-oss-120b", "anthropic": "claude-sonnet-4-6"}
    raw = _call_llm(system, user, provider, model or default_models.get(provider, "openai/gpt-oss-120b"))

    logger.debug("LLM-alone raw output: %s", raw)

    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM-alone returned non-JSON: {e}\n\nRaw:\n{raw}") from e

    return parsed, raw
