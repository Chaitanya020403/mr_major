"""
LLM translator: natural-language dispatcher request → validated constraint JSON.

The LLM only fills a fixed, whitelisted schema. Its output is never executed;
it is always checked by json_validator before reaching Z3.
"""

from __future__ import annotations
import json
import os
import logging
from typing import Any

from core.schema import Order, Vehicle, VALID_SLOTS, CONSTRAINT_CLASSES

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a constraint extractor for a warehouse dispatch planner.
Your ONLY job is to convert a natural-language dispatcher instruction into a JSON list
of constraint objects that match the schema below. Do not plan or assign orders yourself.
If a request is ambiguous, return a single object of type "AMBIGUOUS" with a "question" field.
If the request adds no constraints (e.g. "plan the delivery", "make today's plan"),
return an empty list []. The base rulebook (capacity, availability, order deadlines)
is always enforced separately, so plain planning requests are NOT ambiguous.

Constraint types and their required fields:
- DEADLINE:            order, slot
- TIME_WINDOW:         order, earliest, latest
- URGENT_DELIVERY:     order, deadline
- PRECEDENCE:          before, after
- FORBIDDEN_VEHICLE:   order, vehicle
- REQUIRED_VEHICLE:    order, vehicle
- FORBIDDEN_SLOT:      order, slot
- VEHICLE_UNAVAILABLE: vehicle, slot
- CHANGE_DEADLINE:     order, new_deadline
- PREFERRED_EARLY:     order

Valid slots: {slots}
Valid order IDs: {order_ids}
Valid vehicle IDs: {vehicle_ids}

Every object must also have a "source" field with the verbatim phrase from the request
that caused this constraint. Output only valid JSON — no prose, no markdown fences.
"""


def build_user_message(request: str) -> str:
    return f'Request: "{request}"'


def _call_openai(system: str, user: str, model: str = "gpt-4o") -> str:
    import openai
    client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    )
    return response.choices[0].message.content or ""


def _call_groq(system: str, user: str, model: str = "openai/gpt-oss-120b") -> str:
    import openai
    client = openai.OpenAI(
        api_key=os.environ["GROQ_API_KEY"],
        base_url="https://api.groq.com/openai/v1",
    )
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    )
    return response.choices[0].message.content or ""


def _call_anthropic(system: str, user: str, model: str = "claude-sonnet-4-6") -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    message = client.messages.create(
        model=model,
        max_tokens=1024,
        temperature=0,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return message.content[0].text


def translate(
    request: str,
    orders: list[Order],
    vehicles: list[Vehicle],
    provider: str = "groq",
    model: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """
    Returns (raw_json_list, raw_llm_text).
    Raises ValueError if the response is not parseable JSON.
    """
    system = SYSTEM_PROMPT.format(
        slots=VALID_SLOTS,
        order_ids=[o.id for o in orders],
        vehicle_ids=[v.id for v in vehicles],
    )
    user = build_user_message(request)

    if provider == "openai":
        raw = _call_openai(system, user, model or "gpt-4o")
    elif provider == "groq":
        raw = _call_groq(system, user, model or "openai/gpt-oss-120b")
    elif provider == "anthropic":
        raw = _call_anthropic(system, user, model or "claude-sonnet-4-6")
    else:
        raise ValueError(f"Unknown provider '{provider}'")

    logger.debug("LLM raw output: %s", raw)

    # Strip markdown fences if present
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM returned non-JSON: {e}\n\nRaw output:\n{raw}") from e

    if not isinstance(parsed, list):
        parsed = [parsed]

    return parsed, raw
