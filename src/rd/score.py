"""Per-token log-probs of existing responses under a model (teacher or student), via vLLM prompt_logprobs.

uv run python -m rd.score --model Qwen/Qwen3-30B-A3B-FP8 --input data/scores/step0.jsonl --out data/scores/step0_teacher.jsonl
Input rows need problem + response (other keys pass through, response dropped).
Output: input keys + token_ids (response tokens, tokenized exactly like rd.sft.encode) + logprobs (same length).
"""
import argparse
import json
import os
from pathlib import Path

from rd.data import PROMPT, read_jsonl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-len", type=int, default=17408)
    args = ap.parse_args()

    os.environ.setdefault("VLLM_USE_FLASHINFER_SAMPLER", "0")
    from vllm import LLM, SamplingParams

    llm = LLM(args.model, max_model_len=args.max_len, gpu_memory_utilization=0.92)
    tok = llm.get_tokenizer()
    rows = read_jsonl(args.input)
    prompts, splits = [], []
    for r in rows:
        p = tok(tok.apply_chat_template([{"role": "user", "content": PROMPT.format(problem=r["problem"])}],
                                        tokenize=False, add_generation_prompt=True, enable_thinking=True),
                add_special_tokens=False)["input_ids"]
        resp = tok(r["response"], add_special_tokens=False)["input_ids"]
        prompts.append({"prompt_token_ids": (p + resp)[: args.max_len - 1]})
        splits.append(len(p))
    outs = llm.generate(prompts, SamplingParams(max_tokens=1, prompt_logprobs=0))

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w") as f:
        for r, s, o in zip(rows, splits, outs):
            ids = o.prompt_token_ids[s:]
            lps = [d[t].logprob for t, d in zip(ids, o.prompt_logprobs[s:])]
            keep = {k: v for k, v in r.items() if k != "response"}  # problem kept: rd.opd rebuilds the prompt
            f.write(json.dumps(keep | dict(token_ids=ids, logprobs=lps)) + "\n")
    print(f"scored {len(rows)} responses -> {args.out}")


if __name__ == "__main__":
    main()
