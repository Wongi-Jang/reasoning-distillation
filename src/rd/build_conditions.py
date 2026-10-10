"""Write every SFT condition to data/conditions/<name>.jsonl and print size / length stats.

uv run python -m rd.build_conditions
Needs data/traces/teacher.jsonl (teacher, n=4) and data/traces/student_pass8.jsonl (student base model, n=8).
"""
import statistics
from collections import Counter

from transformers import AutoTokenizer

from rd import conditions as C
from rd.data import DATA, read_jsonl, write_jsonl
from rd.passk import difficulty

STUDENT = "Qwen/Qwen3-1.7B-Base"
MAX_LEN = 16384  # sft.py drops longer examples; reported here so the loss is visible up front


def main():
    teacher = read_jsonl(DATA / "traces" / "teacher.jsonl")
    student = read_jsonl(DATA / "traces" / "student_pass8.jsonl")
    stu = {r["id"]: r["correct"] for r in student}
    print("difficulty buckets:", dict(Counter(difficulty(stu[r["id"]], r["correct"]) for r in teacher)))

    sets = {
        "E1_answer_only": C.answer_only(teacher),
        "E2_full": C.full(teacher),
        "E3_verified": C.verified(teacher),
        "E4_short": C.shortest(teacher),
        "E5_long": C.longest(teacher),
        "E6_difficulty": C.difficulty_aware(teacher, student),
    }
    tok = AutoTokenizer.from_pretrained(STUDENT)
    print(f"{'condition':16} {'n':>6} {'mean tok':>9} {'median':>7} {f'>{MAX_LEN}':>6}")
    for name, rows in sets.items():
        write_jsonl(DATA / "conditions" / f"{name}.jsonl", rows)
        lens = [len(ids) for ids in tok([r["response"] for r in rows], add_special_tokens=False)["input_ids"]]
        print(f"{name:16} {len(rows):6d} {statistics.mean(lens):9.0f} {statistics.median(lens):7.0f} "
              f"{sum(n > MAX_LEN for n in lens):6d}")


if __name__ == "__main__":
    main()
