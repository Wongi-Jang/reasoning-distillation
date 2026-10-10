#!/usr/bin/env bash
# The whole Phase-1 experiment (runs ON the pod). Restartable: finished SFT runs are skipped
# (tokenizer_config.json is the last file sft.py saves) and evals resume where they stopped.
#   scripts/pod_run.sh run_all bash scripts/run_all.sh
set -euo pipefail
# 16k-token traces put the fp32 logits right at the 80GB edge; expandable segments avoid fragmentation OOMs
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
bash scripts/eval.sh Qwen/Qwen3-1.7B-Base E0_base
for c in E1_answer_only E2_full E3_verified E4_short E5_long E6_difficulty; do
  [ -f "runs/$c/tokenizer_config.json" ] || uv run python -m rd.sft --data "data/conditions/$c.jsonl" --out "runs/$c"
  bash scripts/eval.sh "runs/$c" "$c"
done
uv run python -m rd.report
