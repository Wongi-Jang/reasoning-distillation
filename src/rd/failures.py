"""Label every eval trace (correct / truncated / repetition / format) and break failures down per run.

uv run python -m rd.failures
Reads data/eval/<run>/<bench>.jsonl. Writes results/failures.csv (one row per trace, no text),
results/failures.md (category shares per run) and results/failures.png.
Look up a trace's text by (run, bench, id, k): data/eval/<run>/<bench>.jsonl -> row["responses"][k].
"""
import csv
from collections import Counter

import matplotlib.pyplot as plt

from rd.data import DATA, read_jsonl
from rd.grading import extract_boxed
from rd.report import BENCH, MUTED, OUT, SERIES

REPEAT_MIN = 5  # a >=30-char line seen this often counts as a loop. ponytail: picked by eye, check teacher false-positive rate
CATEGORIES = ["correct", "wrong_answer", "bad_format", "too_long", "loop"]
# one hand-picked MATH-500 trace per category (category, run, id, sample); notes come from reading the full trace
EXAMPLES = [
    ("correct", "E2_full", "test/prealgebra/1761.json", 3,
     "The teacher's format copied cleanly: think, close `</think>`, structured answer, `\\boxed{}`."),
    ("wrong_answer", "E2_full", "test/number_theory/72.json", 3,
     "Right reasoning and a correct list (4, 9, 25, 49, 121, ...), then miscounts: 49 is the 4th, not the 5th."),
    ("bad_format", "E0_base", "test/prealgebra/1423.json", 3,
     "Base model: no `<think>`, no `\\boxed{}`, a decimal instead of 12π; graded on the last number in the text."),
    ("too_long", "E2_full", "test/intermediate_algebra/582.json", 1,
     "No repetition, but a brute-force path with huge arithmetic (a √5 in an integer answer); cut off at 16k tokens."),
    ("loop", "E2_full", "test/precalculus/96.json", 1,
     "Finds b = 7/4 at 31% of the trace, then a wrong slope check (9/44 instead of -1/4) invents a contradiction; "
     "it re-derives 7/4 and re-runs the same check 32 times."),
]


def max_line_repeat(text):
    """How often the most frequent non-trivial line (>= 30 chars) occurs."""
    return max(Counter(s for line in text.split("\n") if len(s := line.strip()) >= 30).values(), default=0)


def tail_repeats(text):
    """Loops without newlines (digit runs, one endless line): the last 120 chars recur in the last 8k."""
    tail = text[-120:]
    return len(tail) == 120 and text[-8000:].count(tail) >= 3


def format_issue(response):
    """Why a FINISHED trace is badly formatted ("" if fine); mirrors grading.extract_answer's fallbacks."""
    if "<think>" in response and "</think>" not in response:
        return "unclosed_think"  # emitted end-of-turn while still thinking -> graded as no answer
    if extract_boxed(response.split("</think>")[-1]) is None:
        return "no_boxed"  # grading falls back to the last number in the answer
    return ""


def label(response, correct, finish):
    truncated = finish == "length"
    rep = max_line_repeat(response)
    repetition = rep >= REPEAT_MIN or tail_repeats(response)
    fmt = "" if truncated else format_issue(response)  # a cut-off trace has no format to judge
    if correct:
        category = "correct"
    elif truncated:
        category = "loop" if repetition else "too_long"
    else:
        category = "bad_format" if fmt else "wrong_answer"
    return dict(truncated=truncated, repetition=repetition, max_line_repeat=rep,
                wrong_format=bool(fmt), format_issue=fmt, category=category)


def write_examples():
    out = ["# Failure examples (MATH-500)", "",
           "One full trace per category, picked by hand. Category definitions: [failures.md](failures.md).", ""]
    for cat, run, id_, k, note in EXAMPLES:
        x = next(r for r in read_jsonl(DATA / "eval" / run / "math500.jsonl") if r["id"] == id_)
        out += [f"## {cat}: {run}, `{id_}` (level {x['level']}, sample {k})", "", note, "",
                f"- gold: `{x['answer']}` · pred: `{x['preds'][k]}` · tokens: {x['n_tokens'][k]} · "
                f"finish: {x['finish'][k]}", "",
                "**Problem**", "", "````", x["problem"], "````", "",
                "<details><summary>Full trace</summary>", "", "````", x["responses"][k], "````", "", "</details>", ""]
    (OUT / "failure_examples.md").write_text("\n".join(out))


def main():
    rows = []
    for run_dir in sorted(p for p in (DATA / "eval").iterdir() if p.is_dir()):
        for bench in BENCH:
            if not (path := run_dir / f"{bench}.jsonl").exists():
                continue
            for r in read_jsonl(path):
                for k, (resp, c, f, n, pred) in enumerate(
                        zip(r["responses"], r["correct"], r["finish"], r["n_tokens"], r["preds"])):
                    rows.append(dict(run=run_dir.name, bench=bench, id=r["id"], level=r.get("level"), k=k,
                                     correct=c, n_tokens=n, **label(resp, c, f), pred=pred, gold=r["answer"]))
    OUT.mkdir(exist_ok=True)
    with open(OUT / "failures.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    runs = sorted({r["run"] for r in rows})
    share = {}  # (run, bench) -> {category: fraction}, plus "repetition" over all traces
    for run in runs:
        for bench in BENCH:
            sel = [r for r in rows if r["run"] == run and r["bench"] == bench]
            if sel:
                c = Counter(r["category"] for r in sel)
                share[run, bench] = {k: c[k] / len(sel) for k in CATEGORIES} | {
                    "repetition": sum(r["repetition"] for r in sel) / len(sel)}

    lines = ["# Failure breakdown", "",
             "Every eval sample gets exactly one category: **correct**; **wrong_answer** (finished, well formatted, "
             "wrong); **bad_format** (finished but `<think>` never closed, or no `\\boxed{}`); **too_long** (hit the "
             "16k cap while still making progress); **loop** (hit the cap repeating itself). **repetition** counts "
             f"any trace with a >=30-char line repeated >= {REPEAT_MIN}x (or a repeating tail), finished or not.", "",
             "One annotated full trace per category: [failure_examples.md](failure_examples.md).", ""]
    for bench, name in BENCH.items():
        lines += [f"## {name}", "", "| run | " + " | ".join(CATEGORIES) + " | repetition |",
                  "|---|" + "---|" * (len(CATEGORIES) + 1)]
        lines += [f"| {run} | " + " | ".join(f"{100 * s[k]:.1f}" for k in CATEGORIES + ["repetition"]) + " |"
                  for run in runs if (s := share.get((run, bench)))]
        lines.append("")
    lines.append("![failure breakdown](failures.png)")
    (OUT / "failures.md").write_text("\n".join(lines) + "\n")

    # 100% stacked bars, one panel per benchmark; segments in fixed category order, 2px surface gap between them
    fig, axes = plt.subplots(1, len(BENCH), figsize=(13, 0.45 * len(runs) + 1.6), sharey=True)
    for ax, (bench, name) in zip(axes, BENCH.items()):
        left = [0.0] * len(runs)
        for color, cat in zip(SERIES, CATEGORIES):
            w = [100 * share.get((run, bench), {}).get(cat, 0) for run in runs]
            ax.barh(runs, w, left=left, height=0.6, color=color, edgecolor="#fcfcfb", linewidth=1,
                    label=cat if ax is axes[0] else None)
            left = [a + b for a, b in zip(left, w)]
        ax.set_title(name, loc="left")
        ax.set_xlim(0, 100)
        ax.set_xticks([0, 50, 100])  # full tick set collides "100" with the next panel's "0"
        ax.tick_params(axis="y", length=0)
    axes[0].invert_yaxis()  # shared y: inverting every panel would flip it back
    axes[0].set_ylabel("run")
    fig.supxlabel("share of samples (%)", color=MUTED)
    fig.legend(loc="upper center", ncol=len(CATEGORIES), frameon=False, bbox_to_anchor=(0.5, 1.0))
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(OUT / "failures.png", dpi=150)
    plt.close(fig)
    write_examples()
    print("\n".join(lines[:-1]))


if __name__ == "__main__":
    main()
