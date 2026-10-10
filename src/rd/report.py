"""Aggregate every evaluated run into results/: summary.md, results.csv, and three figures.

uv run python -m rd.report
Reads data/eval/<run>/<bench>.jsonl (from scripts/eval.sh) and runs/<run>/train_log.jsonl (from rd.sft).
"""
import csv
import json
import math
import statistics
from pathlib import Path

import matplotlib
import matplotlib.ticker

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from rd.data import DATA, read_jsonl
from rd.passk import pass_at_k

BENCH = {"gsm8k": "GSM8K", "math500": "MATH-500", "aime24": "AIME24", "aime25": "AIME25"}
OUT = Path("results")
# validated categorical order (dataviz default palette, light mode); baseline E0 is neutral gray
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7"]
INK, MUTED, GRID, BASELINE = "#0b0b0b", "#898781", "#e1e0d9", "#c3c2b7"
plt.rcParams.update({"font.size": 10, "axes.edgecolor": BASELINE, "axes.labelcolor": INK, "text.color": INK,
                     "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False,
                     "axes.spines.right": False, "figure.facecolor": "#fcfcfb", "axes.facecolor": "#fcfcfb"})


def metrics(rows):
    n = len(rows[0]["correct"])
    flat = lambda k: [x for r in rows for x in r[k]]
    return dict(n=n, acc=statistics.mean(flat("correct")),
                pass_n=statistics.mean(pass_at_k(n, sum(r["correct"]), n) for r in rows),
                tokens=statistics.mean(flat("n_tokens")), trunc=statistics.mean(f == "length" for f in flat("finish")))


def main():
    runs = sorted(p.name for p in (DATA / "eval").iterdir() if p.is_dir())
    res = {run: {b: metrics(read_jsonl(f)) for b in BENCH if (f := DATA / "eval" / run / f"{b}.jsonl").exists()}
           for run in runs}
    OUT.mkdir(exist_ok=True)

    with open(OUT / "results.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run", "bench", "samples_per_problem", "acc", "pass_at_n", "mean_tokens", "truncated"])
        for run in runs:
            for b, m in res[run].items():
                w.writerow([run, b, m["n"], f"{m['acc']:.4f}", f"{m['pass_n']:.4f}", f"{m['tokens']:.0f}", f"{m['trunc']:.4f}"])

    pct = lambda run, b, k="acc": f"{100 * res[run][b][k]:.1f}" if b in res[run] else "–"
    train_n = lambda run: sum(1 for _ in open(p)) if (p := DATA / "conditions" / f"{run}.jsonl").exists() else 0
    lines = ["# Results", "", "Accuracy = mean over all samples (pass@1). GSM8K n=1, MATH-500 n=4, AIME n=16 per problem.", "",
             "| run | train n | GSM8K | MATH-500 | AIME24 | AIME25 | AIME24/25 pass@16 | MATH-500 tokens | MATH-500 truncated |",
             "|---|---|---|---|---|---|---|---|---|"]
    for run in runs:
        m5 = res[run].get("math500")
        lines.append(f"| {run} | {train_n(run) or '–'} | {pct(run, 'gsm8k')} | {pct(run, 'math500')} | {pct(run, 'aime24')} | "
                     f"{pct(run, 'aime25')} | {pct(run, 'aime24', 'pass_n')} / {pct(run, 'aime25', 'pass_n')} | "
                     f"{m5['tokens']:.0f} | {100 * m5['trunc']:.1f}% |" if m5 else f"| {run} | – | – | – | – | – | – | – | – |")
    lines += ["", "![accuracy](accuracy.png)", "", "![accuracy vs length](acc_vs_tokens.png)", "", "![training loss](train_loss.png)"]
    (OUT / "summary.md").write_text("\n".join(lines) + "\n")

    # 1) accuracy: one panel per benchmark (own x-scale), runs on a shared y-axis
    fig, axes = plt.subplots(1, len(BENCH), figsize=(13, 0.45 * len(runs) + 1.5), sharey=True)
    for ax, (b, name) in zip(axes, BENCH.items()):
        vals = [100 * res[r][b]["acc"] if b in res[r] else 0 for r in runs]
        colors = [MUTED if r.startswith("E0") else SERIES[0] for r in runs]
        ax.barh(runs, vals, color=colors, height=0.6, edgecolor="#fcfcfb", linewidth=2)
        for y, v in enumerate(vals):
            ax.text(v, y, f" {v:.1f}", va="center", fontsize=9, color=INK)
        ax.set_title(name, loc="left", fontsize=11)
        ax.set_xlim(0, max(vals + [1]) * 1.25)
        ax.grid(axis="x", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
    axes[0].invert_yaxis()  # shared y: E0 on top
    axes[0].set_ylabel("run")
    fig.supxlabel("accuracy (%), mean over samples", color=MUTED)
    fig.tight_layout()
    fig.savefig(OUT / "accuracy.png", dpi=150)
    plt.close(fig)

    # 2) MATH-500 accuracy vs mean response length, every point direct-labeled
    fig, ax = plt.subplots(figsize=(7, 4.5))
    label_y = math.inf  # walk top-down, pushing each label below the previous one so clustered runs don't overlap
    for r in sorted((r for r in runs if "math500" in res[r]), key=lambda r: -res[r]["math500"]["acc"]):
        x, y = res[r]["math500"]["tokens"], 100 * res[r]["math500"]["acc"]
        ax.scatter(x, y, s=64, color=MUTED if r.startswith("E0") else SERIES[0],
                   edgecolor="#fcfcfb", linewidth=2, zorder=3)
        label_y = min(y, label_y - 1.6)
        ax.annotate(r, (x, y), xytext=(x + 150, label_y + 0.5), fontsize=9)
    ax.set_xlabel("mean response tokens (MATH-500)")
    ax.set_ylabel("MATH-500 accuracy (%)")
    ax.grid(color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(OUT / "acc_vs_tokens.png", dpi=150)
    plt.close(fig)

    # 3) training loss vs epoch, smoothed (end labels collide when curves converge -> legend only)
    logs = {p.parent.name: read_jsonl(p) for p in sorted(Path("runs").glob("E*/train_log.jsonl"))}
    if logs:
        fig, ax = plt.subplots(figsize=(8, 4.5))
        for color, (run, log) in zip(SERIES, logs.items()):
            per_epoch = len(log) / (log[-1]["epoch"] + 1)
            x = [r["step"] / per_epoch for r in log]
            y = [statistics.mean(r["loss"] for r in log[max(0, i - 9): i + 1]) for i in range(len(log))]
            ax.plot(x, y, color=color, linewidth=2, label=run)
        ax.set_yscale("log")
        ax.yaxis.set_major_locator(matplotlib.ticker.FixedLocator([0.2, 0.3, 0.5, 1, 2, 4]))
        ax.yaxis.set_major_formatter(matplotlib.ticker.FormatStrFormatter("%.2g"))
        ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
        ax.set_xlabel("epoch")
        ax.set_ylabel("train loss per token (10-step mean)")
        ax.grid(color=GRID, linewidth=0.8)
        ax.legend(frameon=False, fontsize=8)
        fig.tight_layout()
        fig.savefig(OUT / "train_loss.png", dpi=150)
        plt.close(fig)
    print((OUT / "summary.md").read_text())


if __name__ == "__main__":
    main()
