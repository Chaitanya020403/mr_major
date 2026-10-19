# LLM-Assisted SMT-Based Warehouse Delivery Planning and Replanning: Project Design

Oct 16, 2026 · @Chaitanya

AID-851 Machine Reasoning · Chaitanya Nemade (MT2025032), Pankaj Deopa (MT2025081), Parag Piprewar (MT2025083)

## 1. Overview

We build a warehouse dispatch planner in which an LLM only translates natural-language instructions into formal constraints, and the Z3 SMT solver does all the reasoning; we then show experimentally that this split is more reliable than letting an LLM plan on its own.

**Problem.** A dispatcher must assign each order to a vehicle and a dispatch time slot while respecting capacity, vehicle availability, delivery windows, special handling (e.g. refrigeration), precedence and day-specific instructions. Instructions arrive as short natural-language messages that change daily. LLMs read such messages well but are unreliable at multi-step constraint reasoning: they can produce plans that quietly break a rule, or invent a plan when none exists.

**Idea.** Separate interpretation from reasoning:

- the LLM maps the message to a fixed JSON constraint schema;
- deterministic validators check the uploaded data and the LLM's JSON;
- Z3 decides satisfiability, returns a plan (SAT) or a conflicting subset of constraints (UNSAT core);
- an explainer turns the result into plain language, and the system re-solves when conditions change.

**Scope.** Dispatch-batch assignment only. Routing, maps, traffic, multi-day inventory and real fleet integration are out of scope. All data is synthetic.

## 2. Aim, objectives and research questions

**Aim:** show that an LLM + SMT pipeline produces more reliable dispatch plans than an LLM alone, while giving formal explanations and supporting replanning.

**Objectives:**

1. Model dispatch planning formally: rules stated in first-order logic, encoded as propositional and linear-arithmetic constraints for Z3.
2. Build an LLM translator from natural language to a fixed constraint schema, with validation.
3. Use Z3 to decide feasibility, produce a plan, or return an UNSAT core naming the conflicting rules.
4. Replan after operational changes and report the new plan or why none exists.
5. Test the practicality of the chosen constraints and identify the critical rules.
6. Compare LLM + Z3 against LLM-alone with an independent referee.

**Research questions:**

| # | Question |
| --- | --- |
| RQ1 Reliability | Does routing reasoning through Z3 reduce rule violations and wrong feasible/infeasible answers compared with an LLM planning alone? |
| RQ2 Critical rules | Which rules most often decide feasibility or change the plan? |
| RQ3 Replanning | After a change, does each method find a valid new plan or correctly report that none exists? |
| RQ4 Translation | How accurately does the LLM convert requests into the constraint schema? |
| RQ5 Scaling | How do both methods behave as problems grow larger and tighter? |

## 3. Problem size and inputs

The core problem is deliberately small, roughly 60 to 220 Boolean decision variables, with a separate scaling experiment up to about 50 orders.

| Element | Core size | Scaling experiment | Attributes |
| --- | --- | --- | --- |
| Orders | 8 to 12 | up to about 50 | ID, weight, deadline or time window, priority, special needs |
| Vehicles | 2 to 3 | up to about 6 | ID, capacity, type (e.g. refrigerated), availability per slot |
| Dispatch slots | 4 to 6 | 4 to 6 | e.g. 9 AM, 11 AM, 1 PM, 3 PM |
| Rule types | about 9 (R1 to R9) | same | see section 4 |
| Decision variables | orders × vehicles × slots, about 64 to 216 | up to about 1,800 | one Boolean per order-vehicle-slot triple |

**Inputs and who provides them:**

| Input | Provided by | How LLM-alone receives it | How LLM + Z3 receives it |
| --- | --- | --- | --- |
| Rulebook R1 to R9 | System (fixed, not typed by the user) | In its fixed prompt | Hard-coded Z3 constraints |
| orders.json | User uploads each run | In the prompt | Validated, loaded by code into Z3 |
| fleet.json | Default file, user may replace | In the prompt | Validated, loaded by code into Z3 |
| Natural-language request | User types it | In the prompt | Translated by the LLM to JSON, then validated |

Natural language carries what changes daily: extra restrictions ("B before C, keep A off T2"), priorities ("C is urgent, by 9 AM"), disruptions ("T1 broke down after 10 AM") and fixes ("extend A's deadline to 11 AM"). A new order arrives as an updated orders.json.

## 4. Formal model and rulebook

Each rule is first written in first-order logic over orders, vehicles and slots, then grounded over the concrete data into Boolean and linear-integer constraints for Z3.

**Decision variables.** For every order o, vehicle v and slot t, a Boolean x(o,v,t) is true when o is dispatched on v in slot t. The slot of an order is the derived integer term:

```latex
\mathrm{slot}(o) = \sum_{v,t} t \cdot x_{o,v,t}
```

| ID | Rule | Formal statement |
| --- | --- | --- |
| R1 | Every order assigned exactly once | for all o: sum over v,t of x(o,v,t) = 1 |
| R2 | Capacity per vehicle and slot | for all v,t: sum over o of weight(o) × x(o,v,t) ≤ cap(v) |
| R3 | Vehicle availability | for all o,v,t: not avail(v,t) → not x(o,v,t) |
| R4 | Delivery window | for all o,v,t: t outside window(o) → not x(o,v,t) |
| R5 | Compatibility | for all o,v,t: needs(o,c) and not has(v,c) → not x(o,v,t) |
| R6 | Urgent orders by deadline | urgent(o) → slot(o) ≤ deadline(o) |
| R7 | Precedence | before(a,b) → slot(a) < slot(b) |
| R8 | User restriction | forbid(o,v) → not x(o,v,t) for all t; forbid(o,t) → not x(o,v,t) for all v |
| R9 | Preference (optional) | minimise vehicles used or total slot index, with Z3 Optimize, only after hard rules work |

**Why SMT and not plain SAT.** R1, R3, R4, R5 and R8 are propositional. R2, R6 and R7 compare weighted sums and integer slot terms, which belong to linear integer arithmetic. Z3 handles both natively, without hand-encoding arithmetic into clauses.

**Named constraints.** Every grounded constraint is added with a label (e.g. `R2_cap_T1_9AM`, `USER_precedence_B_before_C`) so an UNSAT core can be mapped back to rules and to the user's own instructions.

**Modelling note.** Capacity applies per vehicle per slot (one batch per trip). Multiple trips per day are not modelled separately; if added later, capacity semantics must be revised explicitly.

## 5. LLM translation and validation

The LLM never chooses assignments and its output is never executed; it only fills a fixed, whitelisted schema that ordinary code checks before anything reaches Z3.

**Constraint types in the schema:** DEADLINE, TIME\_WINDOW, URGENT\_DELIVERY, PRECEDENCE, FORBIDDEN\_VEHICLE, REQUIRED\_VEHICLE, FORBIDDEN\_SLOT, VEHICLE\_UNAVAILABLE, CHANGE\_DEADLINE, PREFERRED\_EARLY. ADD\_ORDER is an optional extension.

**What the LLM receives:** the user's request, short descriptions of the constraint types, the JSON schema, and the list of valid order, vehicle and slot IDs. It is told to flag ambiguity rather than guess.

**Example output:**

```json
[{"type":"PRECEDENCE","before":"B","after":"C","source":"B must go before C"},
 {"type":"FORBIDDEN_VEHICLE","order":"A","vehicle":"T2","source":"don't put A on T2"}]
```

**Two validators:**

| Validator | Checks | On failure |
| --- | --- | --- |
| Data validator (orders.json, fleet.json) | Unique IDs; positive weights and capacities; slots that exist; known vehicle types and order needs | Clear error, nothing is solved |
| JSON validator (LLM output) | Valid JSON; known constraint types; IDs exist in the data; slots exist; required fields present | Reject or ask the user to clarify |

An order heavier than every vehicle is valid data but an infeasible problem; it passes validation and Z3 reports UNSAT with capacity in the core.

**Logging.** Every run logs the request, the LLM's JSON, validation results, solver outcome and explanation, for evaluation.

## 6. System architecture

The demo app and the experiment runner call the same backend code, so the logic is built once and tested at scale before it is demonstrated.

&#91;embedded content: System architecture · two entry points, one shared core\]

Both validators gate what reaches Z3. The LLM-alone baseline sits beside our pipeline, and both feed the same referee. Everything is Python; there is no separate web server.

## 7. The two methods compared

Both methods receive identical information; the only difference is whether the LLM or Z3 does the reasoning.

&#91;embedded content: LLM-alone vs LLM + Z3 · same inputs, same referee\]

|  | LLM-alone (baseline) | LLM + Z3 (our system) |
| --- | --- | --- |
| Data files | In the prompt | Validated, loaded by code into Z3 |
| Rulebook | Plain English in a fixed prompt | Hard-coded Z3 constraints |
| LLM's job | Understand, reason and plan | Translate the request only |
| Who decides feasibility | The LLM | Z3 |
| Output when infeasible | "Impossible" + a structured reason | UNSAT core in the same structure |

## 8. Worked example

One short instruction turns a feasible problem into an infeasible one through a five-step chain that Z3 finds and explains; we verified this by running the encoding below.

**Data:** orders A (60 kg, due 9 AM), B (50 kg, due 11 AM), C (30 kg, due 11 AM, needs refrigeration). T1: normal, 100 kg, available 9 AM and 11 AM. T2: refrigerated, 80 kg, available 11 AM only.

**Request:** "B must go before C, and don't put A on T2."

```python
from z3 import *
O = {"A":60, "B":50, "C":30}; deadline = {"A":0, "B":1, "C":1}   # 0=9AM, 1=11AM
V = {"T1":100, "T2":80}; needs_fridge = {"C"}; fridge = {"T2"}
available = {("T1",0), ("T1",1), ("T2",1)}; T = [0, 1]
x = {(o,v,t): Bool(f"x_{o}_{v}_{t}") for o in O for v in V for t in T}
s = Solver(); s.set(unsat_core=True)
for o in O:
    s.assert_and_track(PbEq([(x[o,v,t],1) for v in V for t in T], 1), f"R1_once_{o}")
for v in V:
    for t in T:
        s.assert_and_track(Sum([If(x[o,v,t], O[o], 0) for o in O]) <= V[v], f"R2_cap_{v}_{t}")
for o in O:
    for v in V:
        for t in T:
            if (v,t) not in available: s.assert_and_track(Not(x[o,v,t]), f"R3_avail_{o}_{v}_{t}")
            if t > deadline[o]:        s.assert_and_track(Not(x[o,v,t]), f"R4_deadline_{o}_{v}_{t}")
            if o in needs_fridge and v not in fridge:
                s.assert_and_track(Not(x[o,v,t]), f"R5_fridge_{o}_{v}_{t}")
slot = lambda o: Sum([If(x[o,v,t], t, 0) for v in V for t in T])
s.assert_and_track(slot("B") < slot("C"), "USER_precedence_B_before_C")   # from LLM JSON
for t in T: s.assert_and_track(Not(x["A","T2",t]), f"USER_forbid_A_T2_{t}")
print(s.check(), s.unsat_core())
```

**Result: UNSAT.** C must go on T2 at 11 AM (refrigeration, availability); so B must leave at 9 AM (precedence); only T1 runs at 9 AM (availability); A is also due at 9 AM, so A and B share T1 (deadline); 60 + 50 = 110 kg exceeds 100 kg (capacity). The returned core contained these rule families, R1 (exactly once) and the user's precedence constraint, and did not include "A not on T2", which the conflict does not need.

**Without the request:** SAT, e.g. A on T1 at 9 AM, B and C on T2 at 11 AM (80 kg, exactly full). **Fix:** "extend A's deadline to 11 AM" makes the problem SAT again.

## 9. Evaluation method: the referee

Every output from both methods is judged by an independent referee written in plain Python, with no LLM and no Z3, because Z3 is one of the systems being evaluated.

| A method claims... | How the referee checks it |
| --- | --- |
| A plan (SAT) | Plan checker evaluates every hard rule on the plan; result is valid, or invalid with the broken rules listed |
| Infeasible (UNSAT) | Compared with independent ground truth (below) |
| A translation | LLM JSON compared field by field with a hand-written gold JSON |

Plans are always checked against the **gold constraints**, not against the LLM's translation, so a mistranslation cannot hide behind a correct solve.

**Ground truth for infeasibility.** Verifying a plan is cheap; proving that no plan exists is not (10 orders with 15 vehicle-slot choices give 15^10, about 5.8 × 10^11 assignments). We therefore use:

| Method | Used for |
| --- | --- |
| Planted conflicts | Most UNSAT cases: the conflict is constructed, so the label and true cause are known |
| Reason checking | Any UNSAT claim: brute-force only the orders, vehicles and rules named in the stated conflict; if that subset is infeasible, the whole problem is |
| OR-Tools CP-SAT | Independent second solver for large random cases |
| Backtracking search | Small cases (up to about 12 to 15 orders), to confirm the oracles agree |

**Structured reasons.** Both methods state UNSAT reasons in the same format, so they are checked identically:

```json
{"feasible": false, "conflict": {"orders": ["F"], "vehicles": ["T2"], "rules": ["R3","R5","R6"]}}
```

A valid stated conflict confirms UNSAT. An invalid one shows the reason is wrong; the UNSAT label itself is then settled by the planted label or OR-Tools.

## 10. Scenario generation and replanning

A seeded generator creates many scenarios by altering and adding constraints, and each scenario carries a scripted change that tests replanning.

**Generator settings:** number of orders, vehicles and slots; tightness (total weight / total capacity, from about 0.5 to 0.95); share of orders needing refrigeration; precedence chain length; random seed. The same seed always gives the same scenario.

**Scenario families:**

| Family | Purpose |
| --- | --- |
| Feasible | Valid plan exists; checks plan quality |
| Planted infeasible | A known conflict is injected (e.g. the only refrigerated vehicle is unavailable before an urgent refrigerated order's deadline) |
| Barely feasible | Feasible, but removing one vehicle or slot makes it infeasible |
| Hidden conflict | Infeasible only through 3 or more interacting rules |
| Ambiguous request | Tests that the system asks for clarification instead of guessing |

**Natural-language requests** come from templates (always consistent with the gold constraints), LLM paraphrases spot-checked by the team, and a small hand-written set.

**Each test case:**

```
case_042/
  fleet.json  orders.json  request.txt
  gold_constraints.json   label.json   change.txt (+ gold JSON and label for round 2)
```

**Replanning.** Every change is handled as "modify the problem, solve again":

| After round 1 | Change | Example |
| --- | --- | --- |
| Feasible | Disruption | Vehicle breakdown, tighter deadline, new order (updated orders.json) |
| Infeasible | Relaxation | Add a vehicle, extend a deadline |

In the replanning experiment both methods start from the same correct plan, so we measure replanning ability rather than carried-over round-1 errors. We record SAT/UNSAT after the change, validity of the new plan, the number of assignments that moved, and solve time. An optional variant uses Z3 Optimize to find the valid plan closest to the old one (fewest changes).

## 11. Constraint practicality and critical rules

We test that each chosen rule is practical, meaning it is realistic and actually influences outcomes, and we rank rules by how much they govern feasibility across all scenarios.

**Practicality checks:**

1. **One deciding scenario per rule.** For every rule we build a scenario where that rule alone flips the outcome. A rule with no such scenario is redundant in our model.
2. **Parameter sweeps.** We tighten one parameter at a time (capacity, deadlines, vehicle availability, share of refrigerated orders) and plot the share of feasible scenarios. A sharp but explainable transition from feasible to infeasible shows the model behaves sensibly; a rule that makes nearly everything infeasible, or never matters, is flagged.
3. **Activity rate.** For each rule, the percentage of scenarios where it is active (binding in the plan or present in a conflict).

**Finding critical rules:**

| Analysis | Method | What it shows |
| --- | --- | --- |
| Rule removal (ablation) | Remove one rule at a time across all scenarios and re-solve | How often each rule changes feasibility (UNSAT to SAT) or changes assignments |
| UNSAT core frequency | Count how often each rule family appears in cores across all infeasible scenarios | Which rules most often cause conflicts |
| Minimal conflicts (optional) | Shrink cores to minimal unsatisfiable subsets | Which rule combinations are the root causes |

**Output:** a ranked table of rules per scenario family, for example how often capacity versus refrigeration compatibility appears in conflicts as tightness increases. Following good practice, a rule is reported as critical for a scenario family, not as universally critical from one example. Unsat cores are presented as conflicting subsets that are not necessarily minimal.

Both analyses reuse the scenario generator and solver, so they add about 4 to 5 days of work.

## 12. Experiments and metrics

Six experiments map onto the research questions; A is the central comparison.

| Experiment | RQ | Metrics |
| --- | --- | --- |
| A. One-shot planning, LLM-alone vs LLM + Z3 | RQ1 | Correct SAT/UNSAT rate; % of plans passing the checker; % of UNSAT reasons that are valid conflicts |
| B. Replanning | RQ3 | Same as A after a change, from the same correct start; assignments moved; solve time |
| C. Translation quality | RQ4 | Field-level match with gold JSON; validator acceptance rate; ambiguous requests correctly flagged |
| D. Scaling | RQ5 | A and B as orders (about 10 to 50) and tightness increase; solver and end-to-end time |
| E. Constraint practicality | RQ2 | Deciding scenario per rule; feasibility curves from parameter sweeps; activity rate |
| F. Critical rules | RQ2 | Ablation flip rate per rule; UNSAT core frequency per rule family |

**Fairness rules for A and B:** the same LLM in both pipelines; LLM-alone gets the full rulebook, a careful prompt and a self-check step; both are judged by the same referee; temperature 0 and fixed model versions; results reported honestly, including cases where the LLM does well.

**Models:** one strong hosted LLM and one small local open model (run through Ollama), to show how reliability depends on model strength. Exact models will be fixed and recorded before the experiments run.

## 13. Demo

The demo is a single Streamlit page that shows a feasible case, an infeasible case and a replanning case on the worked example.

| Screen area | Contents |
| --- | --- |
| Inputs | Upload orders.json; choose the default fleet or upload fleet.json; type the day's instructions |
| Actions | Plan; Compare with LLM-alone; Show rules |
| Result | Feasible or not, with solve time; a plan table with vehicle loads, or the conflicting rules in plain sentences; the LLM's translated JSON |
| Replan | Type a change or fix, or upload an updated orders.json, then re-solve |

**Demo script:**

1. Plan with no instructions: feasible, valid plan shown.
2. Add "B must go before C, and don't put A on T2": infeasible, five-step conflict explained.
3. Compare with LLM-alone: its answer is shown beside ours, marked valid or invalid by the referee.
4. Type "extend A's deadline to 11 AM" and replan: feasible again, with the changed assignments highlighted.

## 14. Plan

We have about 8 weeks and build the solver before the LLM, so a complete solver-only system exists by the end of week 3 even if later work slips.

&#91;embedded content: 8-week plan · 5 phases, 3 gates\]

Work is split by ownership: one member on the formal model and solver, one on the LLM translation and validation, one on scenarios, integration, experiments and the demo. Optional extensions (fewest-change replanning with Z3 Optimize, adding orders by natural language, CSV import) are attempted only in the week 7 buffer.

## 15. Tools, limitations and references

**Tools:** Python; z3-solver; Streamlit; an LLM provider SDK and Ollama; OR-Tools CP-SAT (ground truth only); pandas and matplotlib.

**Code layout:**

```
core/        schema, data_validator, translator, json_validator, solver, explainer, llm_alone
referee/     checker (plan checker), oracle (backtracking + OR-Tools)
experiments/ generator, run, ablation, sweeps, plots
data/        fleet_default.json, cases/
app.py       Streamlit demo
```

**Limitations:**

- A wrong UNSAT reason shows the reason is wrong, not that UNSAT is wrong; we rely on planted labels and OR-Tools for that case.
- Agreement between two solvers is strong evidence, not a formal proof certificate.
- LLM + Z3 is only as good as its translation; a mistranslation the validator misses leads Z3 to solve the wrong problem correctly.
- Unsat cores are conflicting subsets and are not guaranteed minimal.
- Synthetic data only; template requests may be easier than real dispatcher messages.

**References:**

- [Z3 Guide](https://microsoft.github.io/z3guide/)
- [Z3Py API documentation](https://z3prover.github.io/api/html/namespacez3py.html)
- [Z3 repository and examples](https://github.com/Z3Prover/z3)
- [Google OR-Tools](https://developers.google.com/optimization)
- [OpenAI Structured Outputs guide](https://platform.openai.com/docs/guides/structured-outputs) (one way to request schema-conforming output; the design is provider-independent)
