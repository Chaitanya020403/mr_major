# Changes summary (Oct 25–26, 2026)

The app now takes Excel uploads, has a real fragile rule and replans with the fewest changes. Experiment results are unchanged except Exp B and a new R9 ablation. All work is 18 commits on `main` after `b53daae`.

## App changes

All tested in a browser against the live Groq model.

| Change | What users see | Main files |
| --- | --- | --- |
| Excel / CSV upload | Orders (and an optional Fleet sheet) upload as `.xlsx` or `.csv`; JSON still works. Errors name the row, e.g. "Orders row 2 (order A): '10AM' is not a delivery time". | `core/excel_loader.py`, `app.py` |
| Excel template | "Download Excel template" button: Orders, Fleet and How-to sheets with dropdowns, pre-filled with the worked example. | `core/excel_loader.py` |
| Editable tables | Loaded data shows as editable tables (dropdowns, checkboxes, add/delete rows). Planning uses the tables as shown; they download as Excel. | `app.py` |
| Heavy → Oversized | The order need and vehicle type are now "Oversized". Files saying Heavy or Bulky still load. | `core/data_validator.py` |
| Fragile rule (new R9) | A truck carrying a fragile order is at most 60% full on that trip and carries no oversized order on it. The limit is `FRAGILE_MAX_LOAD` in `core/schema.py`. | `core/solver.py`, `referee/` |
| "Prefer early" removed | `PREFERRED_EARLY` and the old R9 optimisation are gone; "as early as possible" now gets a clarifying question. | `core/schema.py`, `core/translator.py` |
| 20 s solver limit | Very hard problems show a "could not decide in time" warning instead of hanging. Experiments run without a limit. | `app.py`, `core/solver.py` |
| Minimal-change replan | Replan keeps as many orders as possible where the last plan put them and marks moved ones ("moved from T2 @ 1PM"). | `core/solver.py`, `app.py` |
| Plain requests | "Plan the delivery" plans directly; truly unclear requests show the LLM's question as a warning. | `core/translator.py`, `app.py` |

## Bug fixes

| Bug | Effect before | Now |
| --- | --- | --- |
| Replan dropped the original request | A replan could break "B before C" from the first request. | Original request + change are both applied. |
| Compare graded against the worked example's answers | VALID/INVALID was wrong for any other request or data. | Uses gold only for the unchanged worked example, else the translated constraints, and says which. |
| Fragile orders | Could never be planned (no vehicle type "fragile"). | Handled by R9. |
| `TIME_WINDOW` instructions | "Deliver B between 11AM and 1PM" was silently ignored by solver, checker and oracle. | Enforced everywhere. |
| Checker ignored "vehicle unavailable" | Plans breaking it could be graded VALID. | Checked. |
| OR-Tools oracle missing rules | Ignored deadline changes, R6 urgent deadlines and vehicle-unavailable; rounded weights down. | Same rules as the solver; exact weights. |
| Repeated instruction crashed Z3 | "named assertion defined twice", e.g. a replan restating the request. | Handled. |
| Generator request text | `DEADLINE` constraints were missing from the generated request text. | Included (no stored case was affected). |

Z3, the plain-Python checker, brute force and OR-Tools agree on 600 random problems using all nine constraint types.

## Experiments

Ground truth for generated cases now comes from the OR-Tools oracle, cross-checked with Z3; generation stops if they disagree. Before, labels came from the Z3 solver being evaluated.

| Exp | What it measures | Result file changed? |
| --- | --- | --- |
| A | LLM + Z3 vs LLM-alone verdicts and plan validity | No. Labels re-verified with OR-Tools. |
| B | Replanning after "T1 is unavailable" | Yes: `assignments_moved` is now the minimum number of orders that had to move (was an arbitrary plan's count). Verdicts unchanged. Script added: `experiments/replan.py`. |
| C | LLM translation accuracy vs gold constraints | No. Runner added and re-run live: identical metrics. |
| D | Correctness and solve time as orders grow | No. Runner added; same verdicts, timings within noise. |
| E | Feasibility rate vs tightness / fridge fraction | No. Runner added; identical. |
| F | Rule ablation (flip rate per rule) | R9 rows added (0 flips, scenarios have no fragile orders). New: `exp_f_ablation_handling.jsonl` with 30% fragile and 25% oversized orders, where R9 flips 3/20. |

The scripts behind C–F and the plots were never committed before; `experiments/reproduce.py` recreates them and the plots are pixel-identical.

## How to run

```bash
pip install -r requirements.txt          # adds openpyxl for Excel
export GROQ_API_KEY=...                  # only for the app, Exp A and Exp C
streamlit run app.py

python -m experiments.run --generate --n 20 --method both --out results/exp_a.jsonl
python -m experiments.replan --n 20 --out results/exp_b.jsonl
python -m experiments.reproduce all      # C, D, E, F and plots
```

## Open items

- Rotate the Groq API key; it was pasted into a chat.
- Exp E curves are nearly flat: "tightness" is measured against one trip per truck while trucks make 3–4 trips. Either mention this as a limitation or change the sweep (measured: against full-day capacity the curve drops from 29/30 to 2/30 feasible).
- In Exp A, LLM-alone also scores 20/20, so the stored results do not yet show LLM + Z3 beating LLM-alone. Larger or tighter scenarios would be needed to show a gap.
