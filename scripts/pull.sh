#!/usr/bin/env bash
# Copy results/ (summary.md, results.csv, figures) and the train logs back from the pod.
set -euo pipefail
cd "$(dirname "$0")/.."
kubectl exec rd -- tar czhf - -C /app/reasoning-distillation results \
  --exclude='*.safetensors' runs 2>/dev/null | tar xzf - --exclude='*.safetensors'
echo "pulled results/ and runs/*/train_log.jsonl"
