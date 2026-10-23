"""Round driver for GRPO (rd.grpo): rollouts (rd.generate, n per problem) -> correctness reward -> PPO steps.

uv run python -m rd.grpo_run --init runs/E7_opd --out runs/E8_grpo [--rounds 30 --problems 64 --n 8]
Same process layout as rd.opd_run (fp32 student + AdamW kept across rounds, in CPU RAM while vLLM samples) minus
the teacher. Writes <out>/grpo_log.jsonl (one row per round) and the latest bf16 weights to <out> every round
(resume: --init <out> --start-round <next round>, fresh AdamW).
"""
import argparse
import copy
import json
import random
import statistics
import time
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from rd.data import DATA, read_jsonl, write_jsonl
from rd.failures import label
from rd.grpo import advantages, encode, train_round
from rd.opd_run import move, run


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rounds", type=int, default=30)
    ap.add_argument("--problems", type=int, default=64, help="problems per round")
    ap.add_argument("--n", type=int, default=8, help="samples per problem (one GRPO group)")
    ap.add_argument("--max-tokens", type=int, default=8192, help="cap; a sample that hits it gets reward 0")
    ap.add_argument("--lr", type=float, default=1e-6)
    ap.add_argument("--seqs-per-step", type=int, default=32)
    ap.add_argument("--work", default="/tmp/grpo")
    ap.add_argument("--start-round", type=int, default=0)
    ap.add_argument("--seed", type=int, default=1)  # != rd.opd_run's 0: a different problem order than E7 saw
    args = ap.parse_args()

    random.seed(args.seed)
    work, out = Path(args.work), Path(args.out)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    device = torch.device("cuda")
    tok = AutoTokenizer.from_pretrained(args.init)
    model = AutoModelForCausalLM.from_pretrained(args.init, dtype=torch.float32).to(device)
    model.gradient_checkpointing_enable()
    model.config.use_cache = False
    model.train()
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.0)
    pool = read_jsonl(DATA / "pool.jsonl")
    random.shuffle(pool)
    log = open(out / "grpo_log.jsonl", "a")

    for r in range(args.start_round, args.rounds):
        t0 = time.time()
        move(model, opt, "cpu")
        (out / "model.safetensors").unlink(missing_ok=True)  # room for one 3.4GB copy only
        copy.deepcopy(model).to(torch.bfloat16).save_pretrained(out)
        tok.save_pretrained(out)
        write_jsonl(work / f"r{r}_problems.jsonl", [pool[(r * args.problems + i) % len(pool)] for i in range(args.problems)])
        run("rd.generate", "--model", out, "--input", work / f"r{r}_problems.jsonl", "--out", work / f"r{r}_rollouts.jsonl",
            "--n", args.n, "--max-tokens", args.max_tokens, "--seed", r)
        rolls = read_jsonl(work / f"r{r}_rollouts.jsonl")
        t_gen = time.time() - t0

        data, rewards, mixed = [], [], 0
        for x in rolls:
            rw = [float(c and f == "stop") for c, f in zip(x["correct"], x["finish"])]
            rewards += rw
            mixed += 0 < sum(rw) < len(rw)
            for resp, c, f, a in zip(x["responses"], x["correct"], x["finish"], advantages(rw)):
                # uniform groups carry no signal. Truncated samples still count as 0 in the group mean, but their
                # ~8k tokens stay out of the loss (DAPO overlong filtering): with them in, token-level negative
                # updates dominated and run 1 drifted longer (rounds 7-9: clip 0.2% -> 4.4%, truncation 39% -> 56%).
                # Exception: truncated LOOPS stay in. Filtering them too left nothing pushing against repetition
                # and run 2's loop rate rose 6.8% -> 14.6% in 6 rounds while the student grew more confident.
                loop = f == "length" and label(resp, c, f)["category"] == "loop"
                if a and (f == "stop" or loop):
                    data.append((*encode(tok, x["problem"], resp, f == "stop"), a))
        move(model, opt, device)
        steps, mean_lp = train_round(model, opt, data, args.lr, args.seqs_per_step, device) if data else ([], 0.0)

        flat = [(t, c, f, n) for x in rolls for t, c, f, n in zip(x["responses"], x["correct"], x["finish"], x["n_tokens"])]
        cats = [label(t, c, f)["category"] for t, c, f, _ in flat]
        rec = dict(round=r, reward=statistics.mean(rewards), mixed_groups=mixed / len(rolls), trained_seqs=len(data),
                   steps=len(steps), loop=cats.count("loop") / len(cats),
                   truncated=statistics.mean(f == "length" for _, _, f, _ in flat),
                   tokens=statistics.mean(n for *_, n in flat),
                   clip_frac=statistics.mean(s["clip_frac"] for s in steps) if steps else 0.0,
                   gnorm=max((s["gnorm"] for s in steps), default=0.0), mean_logprob=mean_lp,
                   sec_rollout=round(t_gen), sec=round(time.time() - t0))
        log.write(json.dumps(rec) + "\n")
        log.flush()
        print(f"round {r}: {rec}", flush=True)

    (out / "model.safetensors").unlink(missing_ok=True)
    model.to(torch.bfloat16).save_pretrained(out)
    tok.save_pretrained(out)


if __name__ == "__main__":
    main()
