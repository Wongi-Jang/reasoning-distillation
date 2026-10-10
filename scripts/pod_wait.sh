#!/usr/bin/env bash
# Block until job <name> exits, then print the tail of its log (errors included).
set -uo pipefail
name=$1
miss=0
# exited = no such pid, or a zombie (setsid'd jobs are never reaped, and kill -0 succeeds on zombies)
until kubectl exec rd -- bash -c "s=\$(ps -o stat= -p \$(cat /app/reasoning-distillation/logs/$name.pid)); [[ -z \$s || \$s == Z* ]]" >/dev/null 2>&1; do
  # 3 misses in a row, so a transient OIDC/API error is not mistaken for a reclaimed pod
  kubectl get pod rd >/dev/null 2>&1 && miss=0 || miss=$((miss+1))
  (( miss < 3 )) || { echo "POD rd IS GONE (reclaimed?) - recreate and rerun; jobs are restartable"; exit 1; }
  sleep 30
done
kubectl exec rd -- bash -c "grep -vE '^\(EngineCore|INFO|WARNING|Loading|it/s\]' /app/reasoning-distillation/logs/$name.log | tail -8" 2>&1 | grep -v Flag
