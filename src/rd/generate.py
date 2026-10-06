"""Sample n completions per problem with vLLM, then grade them. Used for teacher traces, student pass@k, and eval.

uv run python -m rd.generate --model Qwen/Qwen3-30B-A3B-FP8 --input data/pool.jsonl \
    --out data/traces/teacher.jsonl --n 4 --max-tokens 16384 [--limit 20]

Output: one row per problem = input row + responses / preds / correct / n_tokens / finish (lists of length n).
Resumable: problems already present in --out are skipped, and results are appended chunk by chunk.
"""
import argparse
import json
import os
import statistics
from pathlib import Path

from rd.data import PROMPT, read_jsonl
from rd.grading import extract_answer, is_correct


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="HF id or local checkpoint dir")
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=4)
    ap.add_argument("--max-tokens", type=int, default=16384)
    ap.add_argument("--temperature", type=float, default=0.6)  # Qwen3 thinking-mode recommendation
    ap.add_argument("--no-think", action="store_true", help="Qwen3 chat template with enable_thinking=False")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--chunk", type=int, default=512, help="problems per vLLM call; results appended after each")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    # FlashInfer's sampler JIT-compiles with nvcc, which the pytorch runtime image lacks -> use vLLM's torch sampler.
    os.environ.setdefault("VLLM_USE_FLASHINFER_SAMPLER", "0")
    from vllm import LLM, SamplingParams  # GPU box only

    out = Path(args.out)
    done = {r["id"] for r in read_jsonl(out)} if out.exists() else set()
    rows = [r for r in read_jsonl(args.input)[: args.limit] if r["id"] not in done]
    print(f"{len(done)} already done, {len(rows)} to generate")

    if rows:
        llm = LLM(args.model, max_model_len=args.max_tokens + 1024, gpu_memory_utilization=0.92, seed=args.seed)
        tok = llm.get_tokenizer()
        sp = SamplingParams(n=args.n, temperature=args.temperature, top_p=0.95, top_k=20, max_tokens=args.max_tokens)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "a") as f:
            for i in range(0, len(rows), args.chunk):
                batch = rows[i : i + args.chunk]
                prompts = [tok.apply_chat_template([{"role": "user", "content": PROMPT.format(problem=r["problem"])}],
                                                   tokenize=False, add_generation_prompt=True,
                                                   enable_thinking=not args.no_think) for r in batch]
                for r, o in zip(batch, llm.generate(prompts, sp)):
                    texts = [c.text for c in o.outputs]
                    preds = [extract_answer(t) for t in texts]
                    f.write(json.dumps(r | dict(
                        responses=texts, preds=preds, correct=[is_correct(p, r["answer"]) for p in preds],
                        n_tokens=[len(c.token_ids) for c in o.outputs], finish=[c.finish_reason for c in o.outputs],
                    ), ensure_ascii=False) + "\n")
                f.flush()
                print(f"  {min(i + args.chunk, len(rows))}/{len(rows)}", flush=True)

    res = read_jsonl(out)
    flat = lambda key: [x for r in res for x in r[key]]
    print(f"{len(res)} problems | acc {statistics.mean(flat('correct')):.3f} | "
          f"tokens mean {statistics.mean(flat('n_tokens')):.0f} median {statistics.median(flat('n_tokens')):.0f} | "
          f"truncated {statistics.mean(f == 'length' for f in flat('finish')):.3f}")


if __name__ == "__main__":
    main()
