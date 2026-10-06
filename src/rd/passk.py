from math import comb

def pass_at_k(n, c, k):
    assert k <= n, f"pass@{k} needs at least {k} samples, got {n}"
    if n-c<k:
        return 1.0
    return 1.0-comb(n-c,k)/comb(n,k)

def difficulty(student_correct:list[bool],teacher_correct:list[bool])->str:
    if not any(teacher_correct): return "unsolved"
    p1=pass_at_k(len(student_correct),sum(student_correct),1)
    if p1>=0.5: return "easy"
    return "medium" if p1>0 else "hard"
    