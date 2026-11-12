#!/usr/bin/env python3
"""Reproduce the static figures for the LeasePath v1 manuscript.

Figures 2 and 3 read only the completed November 9 natural experiment:
results/cache_policy/kv_distribution_natural_2026-11-09/natural-comparison.json
Figure 1 is the explicitly constructed example in the November 10 outline.
No model inference, experimental execution, fitting, or uncertainty estimation
is performed. Run this file with Python; PDF, PNG, and provenance outputs are
written beside the script by default.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D


REPO = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE = REPO / (
    "results/cache_policy/kv_distribution_natural_2026-11-09/"
    "natural-comparison.json"
)
POLICIES = [
    ("A", "retired_first_lru", "Retired-first LRU"),
    ("B", "gbdt_point", r"Fixed-window $F_U(3\,\mathrm{s})$"),
    ("C", "gbdt_distribution", "Distribution, equal cost"),
    ("D", "gbdt_cost", "Distribution × cost"),
]
COLORS = {"A": "#5B6470", "B": "#D58A2D", "C": "#007F73", "D": "#7C5AA6"}
OUTPUT_NAMES = [
    "figure1_distribution_example",
    "figure2_preliminary_results",
    "figure3_paired_cycles",
]


def configure_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.titlesize": 11,
            "axes.labelsize": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.edgecolor": "#626970",
            "axes.linewidth": 0.7,
            "xtick.color": "#343A40",
            "ytick.color": "#343A40",
            "text.color": "#20262D",
            "grid.color": "#D9DDE2",
            "grid.linewidth": 0.6,
            "grid.alpha": 0.75,
            "axes.axisbelow": True,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "savefig.facecolor": "white",
        }
    )


def save_figure(fig: plt.Figure, output: Path, name: str) -> None:
    for extension in ("pdf", "png"):
        fig.savefig(output / f"{name}.{extension}", dpi=250, bbox_inches="tight")
    plt.close(fig)


def figure1(output: Path) -> dict:
    edges = np.array([1.0, 10.0, 30.0])
    mass_a = np.array([0.2, 0.6, 0.0])
    mass_b = np.array([0.2, 0.0, 0.6])
    tau = 10.0
    discounted_a = np.exp(-edges / tau) * mass_a
    discounted_b = np.exp(-edges / tau) * mass_b
    color_a, color_b = COLORS["C"], COLORS["D"]
    fig, axes = plt.subplots(1, 2, figsize=(10.6, 3.6))
    fig.subplots_adjust(left=0.07, right=0.98, bottom=0.25, top=0.83, wspace=0.30)
    fig.suptitle("A fixed-window probability can hide different demand timing", y=0.99, fontsize=13)

    ax = axes[0]
    time = np.array([0.0, 1.0, 10.0, 30.0, 32.0])
    ax.step(time, [0, 0.2, 0.8, 0.8, 0.8], where="post", color=color_a,
            linewidth=2.4, label="Prefix A")
    ax.step(time, [0, 0.2, 0.2, 0.8, 0.8], where="post", color=color_b,
            linewidth=2.0, linestyle="--", label="Prefix B")
    ax.axvline(3, color="#8E959B", linewidth=1, linestyle=":")
    ax.plot(3, 0.2, marker="o", markersize=5, color="#20262D", zorder=5)
    ax.annotate(r"Both: $F_U(3\,\mathrm{s})=0.2$", xy=(3, 0.2), xytext=(14, 0.42),
                arrowprops={"arrowstyle": "-", "color": "#626970", "linewidth": 0.8}, fontsize=9)
    ax.set(xlim=(0, 32), ylim=(0, 1), xticks=[0, 3, 10, 20, 30],
           xlabel="Remaining time (s)", ylabel=r"Cumulative demand probability $F_U(r)$",
           title="(a) Constructed cumulative distributions")
    ax.grid(axis="y")
    ax.legend(frameon=False, loc="upper left", ncol=2, fontsize=9)

    ax = axes[1]
    x = np.arange(len(edges))
    width = 0.33
    bars_a = ax.bar(x - width / 2, discounted_a, width, color=color_a, label="Prefix A")
    bars_b = ax.bar(x + width / 2, discounted_b, width, color=color_b, label="Prefix B")
    ax.bar_label(bars_a, labels=[f"{v:.3f}" for v in discounted_a], padding=3, fontsize=9)
    ax.bar_label(bars_b, labels=[f"{v:.3f}" for v in discounted_b], padding=3, fontsize=9)
    ax.set(xticks=x, xticklabels=["1", "10", "30"], ylim=(0, 0.32),
           xlabel="Interval right endpoint (s)",
           ylabel=r"Discounted mass $e^{-r/\tau}\,\Delta F_U(r)$",
           title=r"(b) Finite-horizon score: $H=30$ s, $\tau=10$ s")
    ax.grid(axis="y")
    ax.text(0.5, 0.97, f"Total score:  A = {discounted_a.sum():.3f}     B = {discounted_b.sum():.3f}",
            transform=ax.transAxes, ha="center", va="top", fontsize=10)
    fig.text(0.5, 0.095, "Constructed illustration, not measured data.",
             ha="center", fontsize=10, fontweight="bold")
    fig.text(0.5, 0.035, "Both examples assign 0.8 probability to demand by 30 s; the remaining 0.2 has no assumed outcome.",
             ha="center", fontsize=9, color="#505963")
    save_figure(fig, output, OUTPUT_NAMES[0])
    return {"kind": "constructed_illustration_not_measured", "right_edges_s": edges.tolist(),
            "demand_mass_A": mass_a.tolist(), "demand_mass_B": mass_b.tolist(),
            "horizon_s": 30, "tau_s": tau, "score_A": float(discounted_a.sum()),
            "score_B": float(discounted_b.sum()), "remaining_mass_has_assumed_outcome": False}


def figure2(data: dict, output: Path) -> None:
    aggregate = {row["policy"]: row for row in data["aggregates"]}
    fig, axes = plt.subplots(1, 3, figsize=(11.7, 4.0))
    fig.subplots_adjust(left=0.07, right=0.985, bottom=0.29, top=0.79, wspace=0.34)
    fig.suptitle("Preliminary natural execution on three known development tasks", y=0.99, fontsize=13)
    fig.text(0.5, 0.90, "24 cache blocks · 3 concurrent sessions · 4 cycles per policy · 48/48 task executions correct",
             ha="center", fontsize=10, color="#505963")
    panels = [
        ("cached_tokens", "(a) Prefix reuse", "Cached tokens / batch", ",.0f"),
        ("prefill_s", "(b) Executed prefill", "Prefill time (s / batch)", ".4f"),
        ("mean_successful_jct_s", "(c) End-to-end completion", "Mean JCT (s / task)", ".4f"),
    ]
    for ax, (key, title, ylabel, fmt) in zip(axes, panels):
        values = [aggregate[policy][key] for _, policy, _ in POLICIES]
        bars = ax.bar([letter for letter, _, _ in POLICIES], values,
                      color=[COLORS[letter] for letter, _, _ in POLICIES], width=0.65)
        ax.bar_label(bars, labels=[format(value, fmt) for value in values], padding=4, fontsize=9)
        ax.set(title=title, ylabel=ylabel, ylim=(0, max(values) * 1.19))
        ax.grid(axis="y")
    legend = [Line2D([0], [0], marker="s", linestyle="", color=COLORS[letter], markersize=9,
                     label=f"{letter}: {label}") for letter, _, label in POLICIES]
    fig.legend(handles=legend, loc="lower center", bbox_to_anchor=(0.5, 0.10),
               ncol=2, frameon=False, fontsize=9, columnspacing=2)
    fig.text(0.5, 0.03, "Batch metrics average four three-session batches; JCT averages 12 task executions per policy. No uncertainty intervals shown.",
             ha="center", fontsize=8.6, color="#505963")
    save_figure(fig, output, OUTPUT_NAMES[1])


def figure3(data: dict, output: Path) -> None:
    comparisons = [
        ("retired_first_lru", "gbdt_distribution", "C − A\nDistribution − LRU", COLORS["C"]),
        ("gbdt_point", "gbdt_distribution", "C − B\nDistribution − fixed window", COLORS["C"]),
        ("gbdt_distribution", "gbdt_cost", "D − C\nCost extension − distribution", COLORS["D"]),
    ]
    lookup = {(row["baseline"], row["policy"]): row for row in data["paired"]}
    markers = ["o", "s", "^", "D"]
    offsets = [-0.24, -0.08, 0.08, 0.24]
    fig, ax = plt.subplots(figsize=(9.6, 4.5))
    fig.subplots_adjust(left=0.27, right=0.97, bottom=0.26, top=0.79)
    fig.suptitle("Observed paired changes across four cycles", y=0.99, fontsize=13)
    ax.axvline(0, color="#505963", linewidth=1.2, zorder=1)
    for row_index, (baseline, policy, _, color) in enumerate(comparisons):
        cycles = sorted(lookup[(baseline, policy)]["cycles"], key=lambda row: row["cycle"])
        assert len(cycles) == 4
        for cycle_index, cycle in enumerate(cycles):
            delta = cycle["mean_successful_jct_s_delta"]
            y = row_index + offsets[cycle_index]
            ax.scatter(delta, y, color=color, marker=markers[cycle_index], s=40, zorder=3)
            ax.annotate(f"{delta:+.3f}", (delta, y), xytext=(7, 0), textcoords="offset points",
                        va="center", ha="left", fontsize=9, color=color)
    ax.set(yticks=range(3), yticklabels=[row[2] for row in comparisons],
           xlim=(-2.35, 1.95), ylim=(2.45, -0.45),
           xlabel="Difference in mean JCT (s / task); negative = faster for the first policy")
    ax.grid(axis="x")
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0, pad=10)
    legend = [Line2D([0], [0], marker=marker, color="#505963", linestyle="", markersize=6,
                     label=f"Cycle {index}") for index, marker in enumerate(markers)]
    fig.legend(handles=legend, loc="upper center", bbox_to_anchor=(0.62, 0.91),
               ncol=4, frameon=False, fontsize=9)
    fig.text(0.5, 0.10, "Each point compares the mean of the same three known tasks within one cycle.",
             ha="center", fontsize=9, color="#505963")
    fig.text(0.5, 0.045, "Repeated tasks and cycles are correlated; points are not independent task samples or confidence intervals.",
             ha="center", fontsize=9, color="#505963")
    save_figure(fig, output, OUTPUT_NAMES[2])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    source_bytes = args.source.read_bytes()
    data = json.loads(source_bytes)
    if data.get("status") != "passed" or data.get("experiment_status") != "completed":
        raise ValueError("Expected the completed, audited natural comparison")
    expected = {policy for _, policy, _ in POLICIES}
    if {row["policy"] for row in data["aggregates"]} != expected:
        raise ValueError("Unexpected policy set")
    if any(row["batches"] != 4 for row in data["aggregates"]) or (data["tasks"], data["correct"]) != (48, 48):
        raise ValueError("Unexpected task or cycle counts; revise figure captions before plotting")
    args.output.mkdir(parents=True, exist_ok=True)
    configure_style()
    example = figure1(args.output)
    figure2(data, args.output)
    figure3(data, args.output)
    provenance = {
        "source": str(args.source.resolve()),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "matplotlib_version": matplotlib.__version__,
        "numpy_version": np.__version__,
        "constructed_figure": example,
        "measured_figure_fields": {
            OUTPUT_NAMES[1]: ["aggregates.cached_tokens", "aggregates.prefill_s", "aggregates.mean_successful_jct_s"],
            OUTPUT_NAMES[2]: ["paired[].cycles[].mean_successful_jct_s_delta"],
        },
        "scope": data["scope"],
        "uncertainty_intervals_computed": False,
        "new_experiments_performed": False,
        "outputs": [f"{name}.{ext}" for name in OUTPUT_NAMES for ext in ("pdf", "png")],
    }
    (args.output / "figure-provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n")
    for name in provenance["outputs"]:
        print(args.output.resolve() / name)


if __name__ == "__main__":
    main()
