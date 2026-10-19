"""
Plotting utilities for the experiment report.
"""

from __future__ import annotations
from pathlib import Path
from typing import Sequence

import matplotlib.pyplot as plt

from experiments.sweeps import SweepResult
from experiments.ablation import AblationRow


def plot_feasibility_curve(
    results: list[SweepResult],
    out: Path,
    title: str = "Feasibility rate vs parameter",
) -> None:
    xs = [r.param_value for r in results]
    ys = [r.feasibility_rate for r in results]
    plt.figure()
    plt.plot(xs, ys, marker="o")
    plt.xlabel(results[0].param_name if results else "parameter")
    plt.ylabel("Feasibility rate")
    plt.ylim(0, 1.05)
    plt.title(title)
    plt.tight_layout()
    plt.savefig(out, dpi=150)
    plt.close()


def plot_ablation_flip_rate(
    rows: list[AblationRow],
    out: Path,
    title: str = "Ablation: flip rate per rule",
) -> None:
    from collections import defaultdict
    flip_counts: dict[str, int] = defaultdict(int)
    total_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        flip_counts[row.rule_removed] += int(row.flipped)
        total_counts[row.rule_removed] += 1

    rules = sorted(flip_counts)
    rates = [flip_counts[r] / total_counts[r] for r in rules]

    plt.figure()
    plt.bar(rules, rates)
    plt.xlabel("Rule removed")
    plt.ylabel("Flip rate (UNSAT ↔ SAT)")
    plt.ylim(0, 1.05)
    plt.title(title)
    plt.tight_layout()
    plt.savefig(out, dpi=150)
    plt.close()


def plot_core_frequency(
    core_labels_per_case: list[list[str]],
    out: Path,
    title: str = "UNSAT core: rule family frequency",
) -> None:
    import re
    from collections import Counter
    counts: Counter = Counter()
    for labels in core_labels_per_case:
        families = set()
        for lbl in labels:
            m = re.match(r"(R\d+|USER)", lbl)
            if m:
                families.add(m.group(1))
        for f in families:
            counts[f] += 1

    if not counts:
        return

    labels_sorted = sorted(counts, key=lambda k: -counts[k])
    vals = [counts[k] for k in labels_sorted]

    plt.figure()
    plt.bar(labels_sorted, vals)
    plt.xlabel("Rule family")
    plt.ylabel("# UNSAT cases it appears in")
    plt.title(title)
    plt.tight_layout()
    plt.savefig(out, dpi=150)
    plt.close()
