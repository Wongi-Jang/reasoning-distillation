"""Round driver for on-policy distillation (rd.opd): rollouts (rd.generate) -> teacher scores (rd.score) -> train.

uv run python -m rd.opd_run --init runs/E4_short --out runs/E7_opd [--rounds 20 --problems 512]
One process keeps the fp32 student + AdamW state across rounds (a bf16 save/reload would erase 1e-6 updates).
While vLLM runs, they sit in CPU RAM. The bf16 copy vLLM samples from is saved to <out> every round, so <out>
always holds the latest weights (resume: --init <out> --start-round <next round>, fresh AdamW).
Per-round rollouts go to --work; the big scored files are deleted after use (pod disk limit is 10Gi).
Writes <out>/opd_log.jsonl (one row per round) and the final bf16 checkpoint to <out>.
"""
import argparse
import copy
import json
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from rd.data import DATA, read_jsonl, write_jsonl
from rd.failures import label
from rd.opd import encode, train_round


def move(model, opt, device):
    model.to(device)
    for s in opt.state.values():
        for k, v in s.items():
            if torch.is_tensor(v) and v.dim() > 0:  # AdamW's scalar "step" stays on CPU
                s[k] = v.to(device)
    torch.cuda.empty_cache()


def run(*cmd):
    subprocess.run([sys.executable, "-m", *map(str, cmd)], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", required=True, help="SFT checkpoint to start from")
    ap.add_argument("--out", required=True)
    ap.add_argument("--teacher", default="Qwen/Qwen3-30B-A3B-FP8")
    ap.add_argument("--rounds", type=int, default=20)
    ap.add_argument("--problems", type=int, default=512, help="rollouts per round (1 per problem)")
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument("--lr", type=float, default=5e-7)  # 1e-6 without a baseline: rollouts grew ~600 tokens/round
    ap.add_argument("--seqs-per-step", type=int, default=64)
    ap.add_argument("--work", default="/tmp/opd")
    ap.add_argument("--start-round", type=int, default=0, help="resume: picks up the pool slicing at this round")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    random.seed(args.seed)
    work, out = Path(args.work), Path(args.out)
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
    log = open(out / "opd_log.jsonl", "a")

    work.mkdir(parents=True, exist_ok=True)
    for r in range(args.start_round, args.rounds):
        t0 = time.time()
        move(model, opt, "cpu")
        # /cache has room for one 3.4GB copy, not two: drop the old one first (an eviction right here loses it)
        (out / "model.safetensors").unlink(missing_ok=True)
        copy.deepcopy(model).to(torch.bfloat16).save_pretrained(out)
        tok.save_pretrained(out)
        probs = [pool[(r * args.problems + i) % len(pool)] for i in range(args.problems)]
        write_jsonl(work / f"r{r}_problems.jsonl", probs)
        run("rd.generate", "--model", out, "--input", work / f"r{r}_problems.jsonl",
            "--out", work / f"r{r}_rollouts.jsonl", "--n", 1, "--max-tokens", args.max_tokens, "--seed", r)
        rolls = read_jsonl(work / f"r{r}_rollouts.jsonl")
        # vLLM drops the stop token from the text; put it back so the teacher also scores the decision to stop
        write_jsonl(work / f"r{r}_toscore.jsonl", [
            dict(id=x["id"], problem=x["problem"],
                 response=x["responses"][0] + ("<|im_end|>" if x["finish"][0] == "stop" else "")) for x in rolls])
        run("rd.score", "--model", args.teacher, "--input", work / f"r{r}_toscore.jsonl", "--out", work / f"r{r}_scored.jsonl")
        t_gen = time.time() - t0

        move(model, opt, device)
        data = [(*encode(tok, x["problem"], x["token_ids"]), x["logprobs"])
                for x in read_jsonl(work / f"r{r}_scored.jsonl") if x["token_ids"]]
        (work / f"r{r}_scored.jsonl").unlink()  # ~100MB/round of token ids + logprobs
        steps = train_round(model, opt, data, args.lr, args.seqs_per_step, device)
        cats = [label(x["responses"][0], x["correct"][0], x["finish"][0])["category"] for x in rolls]
        rec = dict(round=r, acc=statistics.mean(x["correct"][0] for x in rolls),
                   loop=cats.count("loop") / len(cats), truncated=statistics.mean(x["finish"][0] == "length" for x in rolls),
                   tokens=statistics.mean(x["n_tokens"][0] for x in rolls), kl=statistics.mean(s["kl"] for s in steps),
                   gnorm=max(s["gnorm"] for s in steps), sec_rollout=round(t_gen), sec=round(time.time() - t0))
        log.write(json.dumps(rec) + "\n")
        log.flush()
        print(f"round {r}: {rec}", flush=True)

    (out / "model.safetensors").unlink(missing_ok=True)
    model.to(torch.bfloat16).save_pretrained(out)
    tok.save_pretrained(out)


if __name__ == "__main__":
    main()
