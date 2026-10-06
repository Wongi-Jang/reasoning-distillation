#!/usr/bin/env bash
# Push code + small data files (pool, benchmarks) to the pod at /app/reasoning-distillation.
set -euo pipefail
cd "$(dirname "$0")/.."
kubectl exec rd -- mkdir -p /app/reasoning-distillation
COPYFILE_DISABLE=1 tar czf - --no-xattrs --exclude .venv --exclude .git --exclude __pycache__ --exclude runs --exclude data/traces \
  --exclude data/conditions . | kubectl exec -i rd -- tar xzf - -C /app/reasoning-distillation
echo synced
