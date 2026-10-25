# LLM-Assisted SMT-Based Warehouse Delivery Planning and Replanning

**AID-851 Machine Reasoning** · Chaitanya Nemade (MT2025032), Pankaj Deopa (MT2025081), Parag Piprewar (MT2025083)

---

## Overview

A warehouse dispatch planner that separates *interpretation* from *reasoning*:

- An **LLM** translates natural-language dispatcher instructions into a validated JSON constraint schema.
- The **Z3 SMT solver** encodes all rules as formal constraints and decides feasibility.
- When a plan exists (SAT), Z3 returns it. When none does (UNSAT), Z3 returns a labelled conflict core, which an explainer converts to plain language.
- The system **replans** after operational changes (breakdowns, new orders, deadline extensions).

We evaluate this pipeline against an **LLM-alone baseline** (same model, same inputs, but LLM plans directly) using an independent referee written in plain Python.

---

## Problem

A dispatcher assigns each order to a vehicle and a dispatch time slot subject to:

| Rule | Description |
|------|-------------|
| R1 | Every order assigned exactly once |
| R2 | Capacity per vehicle per slot |
| R3 | Vehicle availability per slot |
| R4 | Delivery window per order |
| R5 | Special-handling compatibility (e.g. refrigeration) |
| R6 | Urgent orders dispatched by their deadline |
| R7 | Precedence between orders |
| R8 | User-supplied restrictions (forbid vehicle, forbid slot) |
| R9 | Fragile handling: a vehicle carrying a fragile order is loaded to at most 60% of capacity in that slot and carries no oversized order then |

Instructions arrive daily as natural-language messages. LLMs parse such messages well but are unreliable at multi-constraint reasoning and can silently break rules or hallucinate feasibility.

---

## Repository Layout

```
me_major/
├── app.py                  # Streamlit demo
├── requirements.txt
├── core/
│   ├── schema.py           # Constraint type definitions (dataclasses + JSON schema)
│   ├── data_validator.py   # Validate orders.json and fleet.json
│   ├── translator.py       # LLM: natural language → constraint JSON
│   ├── json_validator.py   # Validate LLM output against schema
│   ├── solver.py           # Z3 encoding + solve + UNSAT core extraction
│   ├── explainer.py        # Core/plan → plain-language explanation
│   └── llm_alone.py        # LLM-alone baseline (same inputs, LLM plans directly)
├── referee/
│   ├── checker.py          # Plan checker: validates a plan against gold rules
│   └── oracle.py           # Ground truth: backtracking search + OR-Tools CP-SAT
├── experiments/
│   ├── generator.py        # Seeded scenario generator
│   ├── run.py              # Main experiment runner (A–F)
│   ├── ablation.py         # Rule-removal ablation (Experiment F)
│   ├── sweeps.py           # Parameter sweeps (Experiment E)
│   └── plots.py            # Matplotlib figures for the report
└── data/
    ├── fleet_default.json  # Default fleet (T1, T2, T3)
    └── cases/
        └── case_000/       # Worked example from the design document
            ├── orders.json
            ├── fleet.json
            ├── request.txt
            ├── gold_constraints.json
            ├── label.json
            └── change.txt
```

---

## Quick Start

```bash
# 1. Create and activate a virtual environment
python -m venv .venv && source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set your LLM API key
export OPENAI_API_KEY="sk-..."          # or ANTHROPIC_API_KEY / OLLAMA_HOST

# 4. Run the Streamlit demo
streamlit run app.py

# 5. Run a single planning scenario from the command line
python -m experiments.run --case data/cases/case_000 --method both

# 6. Experiment B: replanning after a vehicle outage (no LLM needed)
python -m experiments.replan --n 20 --out results/exp_b.jsonl
```

---

## Inputs

| Input | Who provides it | Format |
|-------|-----------------|--------|
| `orders.json` | Uploaded each run | See schema below |
| `fleet.json` | Default file, user may replace | See schema below |
| Natural-language request | Typed by dispatcher | Free text |

### `orders.json` schema

```json
[
  {
    "id": "A",
    "weight": 60,
    "deadline": "9AM",
    "time_window": ["9AM", "9AM"],
    "priority": "normal",
    "needs": []
  }
]
```

### `fleet.json` schema

```json
[
  {
    "id": "T1",
    "capacity": 100,
    "type": "normal",
    "available_slots": ["9AM", "11AM"]
  }
]
```

### LLM constraint schema (output)

```json
[
  {"type": "PRECEDENCE",        "before": "B", "after": "C",  "source": "B must go before C"},
  {"type": "FORBIDDEN_VEHICLE", "order": "A",  "vehicle": "T2", "source": "don't put A on T2"}
]
```

Supported constraint types: `DEADLINE`, `TIME_WINDOW`, `URGENT_DELIVERY`, `PRECEDENCE`, `FORBIDDEN_VEHICLE`, `REQUIRED_VEHICLE`, `FORBIDDEN_SLOT`, `VEHICLE_UNAVAILABLE`, `CHANGE_DEADLINE`.

---

## Worked Example

**Orders:** A (60 kg, due 9AM), B (50 kg, due 11AM), C (30 kg, due 11AM, needs refrigeration).  
**Fleet:** T1 normal 100 kg (9AM, 11AM), T2 refrigerated 80 kg (11AM only).  
**Request:** "B must go before C, and don't put A on T2."

Without the request: **SAT** — A on T1@9AM, B+C on T2@11AM (80 kg, exactly full).

With the request: **UNSAT** through a five-step conflict:
1. C must go on T2@11AM (refrigeration + availability).
2. B must leave at 9AM (precedence B < C).
3. Only T1 is available at 9AM.
4. A is also due by 9AM, so A and B both need T1@9AM.
5. 60 + 50 = 110 kg > 100 kg capacity.

**Fix:** "extend A's deadline to 11AM" → **SAT** again.

---

## Evaluation

Six experiments map onto five research questions:

| Exp | RQ | What is measured |
|-----|-----|-----------------|
| A | RQ1 Reliability | Correct SAT/UNSAT rate; % of plans passing the checker |
| B | RQ3 Replanning | Same after a change; assignments moved; solve time |
| C | RQ4 Translation | Field-level match with gold JSON; validator acceptance |
| D | RQ5 Scaling | A+B as orders (10–50) and tightness (0.5–0.95) increase |
| E | RQ2 Critical rules | Deciding scenario per rule; feasibility curves |
| F | RQ2 Critical rules | Ablation flip rate; UNSAT core frequency |

Plans are checked against **gold constraints**, not the LLM's translation, so a mistranslation cannot hide behind a correct solve.

---

## Fairness Rules

- Same LLM model in both pipelines.
- LLM-alone receives the full rulebook, a careful prompt, and a self-check step.
- Both are judged by the same referee.
- Temperature 0, fixed model version recorded before experiments run.
- Results reported honestly, including cases where LLM-alone performs well.

---

## Dependencies

```
z3-solver
streamlit
openai          # or anthropic / ollama
ortools
pandas
matplotlib
pydantic
```

See `requirements.txt` for pinned versions.

---

## Team

| Name | Roll number | Ownership |
|------|-------------|-----------|
| Chaitanya Nemade | MT2025032 | Formal model & Z3 solver |
| Pankaj Deopa | MT2025081 | LLM translation & validation |
| Parag Piprewar | MT2025083 | Scenarios, integration, experiments & demo |

---

## References

- [Z3 Guide](https://microsoft.github.io/z3guide/)
- [Z3Py API](https://z3prover.github.io/api/html/namespacez3py.html)
- [Z3 Repository](https://github.com/Z3Prover/z3)
- [Google OR-Tools](https://developers.google.com/optimization)
- [OpenAI Structured Outputs](https://platform.openai.com/docs/guides/structured-outputs)
