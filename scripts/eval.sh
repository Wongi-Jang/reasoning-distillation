#!/usr/bin/env bash
# Evaluate one model on all benchmarks (runs ON the pod; resumable).
#   bash scripts/eval.sh <hf-id-or-checkpoint-dir> <run-name>   ->  data/eval/<run-name>/<bench>.jsonl
set -euo pipefail
model=$1; name=$2
gen() { uv run python -m rd.generate --model "$model" --input "data/bench/$1.jsonl" --out "data/eval/$name/$1.jsonl" --n "$2" --max-tokens 16384; }
gen gsm8k 1
gen math500 4
gen aime24 16
gen aime25 16
