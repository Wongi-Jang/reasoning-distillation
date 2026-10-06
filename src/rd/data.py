"""Load the training pool and benchmarks into one schema: {id, source, problem, answer, level}.

uv run python -m rd.data bench   # data/bench/{gsm8k,math500,aime24,aime25}.jsonl
uv run python -m rd.data pool    # data/pool.jsonl  (needs rd.grading.extract_boxed)
"""
import json
import random
import sys
from pathlib import Path

from datasets import load_dataset

DATA = Path("data")
BENCHMARKS = ["gsm8k", "math500", "aime24", "aime25"]
MATH_SUBJECTS = ["algebra", "counting_and_probability", "geometry", "intermediate_algebra",
                 "number_theory", "prealgebra", "precalculus"]
# One prompt for teacher sampling, SFT, and eval, so every condition sees the same input.
PROMPT = "{problem}\nPlease reason step by step, and put your final answer within \\boxed{{}}."


def read_jsonl(path):
    with open(path) as f:
        return [json.loads(line) for line in f]


def write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def gsm8k_gold(answer_field):
    return answer_field.split("####")[-1].strip().replace(",", "")


def load_benchmark(name):
    if name == "gsm8k":
        ds = load_dataset("openai/gsm8k", "main", split="test")
        return [dict(id=f"gsm8k/{i}", source="gsm8k", problem=r["question"], answer=gsm8k_gold(r["answer"]), level=None)
                for i, r in enumerate(ds)]
    if name == "math500":
        ds = load_dataset("HuggingFaceH4/MATH-500", split="test")
        return [dict(id=r["unique_id"], source="math500", problem=r["problem"], answer=r["answer"], level=r["level"])
                for r in ds]
    if name == "aime24":
        ds = load_dataset("HuggingFaceH4/aime_2024", split="train")
        return [dict(id=f"aime24/{r['id']}", source="aime24", problem=r["problem"], answer=str(r["answer"]), level=None)
                for r in ds]
    if name == "aime25":
        ds = load_dataset("math-ai/aime25", split="test")
        return [dict(id=f"aime25/{r['id']}", source="aime25", problem=r["problem"], answer=str(r["answer"]), level=None)
                for r in ds]
    raise ValueError(name)


def load_pool(n_gsm8k=2000, seed=0):
    """MATH train (7.5k, answer = last \\boxed{} of the reference solution) + a GSM8K train subset."""
    from rd.grading import extract_boxed

    rows = []
    for subj in MATH_SUBJECTS:
        for i, r in enumerate(load_dataset("EleutherAI/hendrycks_math", subj, split="train")):
            ans = extract_boxed(r["solution"])
            if ans is None:  # a handful of reference solutions have no \boxed{}
                continue
            level = int(r["level"].split()[-1]) if r["level"].split()[-1].isdigit() else None
            rows.append(dict(id=f"math/{subj}/{i}", source="math", problem=r["problem"], answer=ans, level=level))
    gsm = list(enumerate(load_dataset("openai/gsm8k", "main", split="train")))
    random.Random(seed).shuffle(gsm)
    rows += [dict(id=f"gsm8k_train/{i}", source="gsm8k", problem=r["question"], answer=gsm8k_gold(r["answer"]), level=None)
             for i, r in gsm[:n_gsm8k]]
    return rows


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "bench"
    if cmd == "bench":
        for name in BENCHMARKS:
            rows = load_benchmark(name)
            write_jsonl(DATA / "bench" / f"{name}.jsonl", rows)
            print(f"{name}: {len(rows)}")
    elif cmd == "pool":
        rows = load_pool()
        write_jsonl(DATA / "pool.jsonl", rows)
        print(f"pool: {len(rows)}  ({sum(r['source'] == 'math' for r in rows)} MATH, "
              f"{sum(r['source'] == 'gsm8k' for r in rows)} GSM8K)")
    else:
        sys.exit(__doc__)
