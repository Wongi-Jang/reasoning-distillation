#!/usr/bin/env bash
# Block until job <name> exits, then print the tail of its log (errors included).
set -uo pipefail
name=$1
until kubectl exec rd -- bash -c "p=\$(cat /app/reasoning-distillation/logs/$name.pid) && ! kill -0 \$p" >/dev/null 2>&1; do sleep 30; done
kubectl exec rd -- bash -c "grep -vE '^\(EngineCore|INFO|WARNING|Loading|it/s\]' /app/reasoning-distillation/logs/$name.log | tail -8" 2>&1 | grep -v Flag
