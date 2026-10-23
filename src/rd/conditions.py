"""One SFT dataset per experimental condition, built from teacher traces (+ student samples for difficulty).

Output rows: {"id", "problem", "response"}. sft.py wraps them in the chat template.
Truncated traces (finish == "length") are never used: they have no ending to learn from.
"""

import random
from rd.passk import difficulty

def _example(row, i):
    return {"id": row["id"], "problem": row["problem"], "response": row["responses"][i]}

def _correct(row):
    """Indices of finished, correct traces."""
    return [i for i, (c, f) in enumerate(zip(row["correct"],row["finish"])) if c and f == "stop"]

def answer_only(teacher):
    """E1: (problem, gold answer)"""
    return [{"id": r["id"], "problem":r["problem"],"response":f"\\boxed{{{r['answer']}}}"} for r in teacher]

def full(teacher, seed=0):
    """E2: One random finished trace per problem"""
    rng=random.Random(seed)
    out=[]
    for r in teacher:
        finished=[i for i,f in enumerate(r["finish"]) if f=="stop"]
        if finished:
            out.append(_example(r,rng.choice(finished)))
            
    return out

def verified(teacher, seed=0):
    """E3: One random correct trace per problem the teacher solved."""
    rng=random.Random(seed)
    return [_example(r, rng.choice(idx)) for r in teacher if (idx:=_correct(r))]

def shortest(teacher):
    """E4: Shortest correct trace per problem"""
    return [_example(r, min(idx, key=lambda i:r["n_tokens"][i])) for r in teacher if (idx:=_correct(r))]

def longest(teacher):
    """E5: Longest correct trace per problem"""
    return [_example(r, max(idx, key=lambda i:r["n_tokens"][i])) for r in teacher if (idx:=_correct(r))]

def difficulty_aware(teacher, student, seed=0):
    """E6: Restricted to problems the student finds medium/hard"""
    stu={r["id"]:r["correct"] for r in student}
    keep={r["id"] for r in teacher if difficulty(stu[r["id"]],r["correct"]) in ("medium","hard")}
    return [ex for ex in verified(teacher, seed) if ex["id"] in keep]

def random_matched(teacher, n, seed=0):
    """E6c: Size-matched control for E6"""
    return random.Random(seed).sample(verified(teacher,seed),n)