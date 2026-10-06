#!/usr/bin/env bash
# Start a long job on the pod, detached from the kubectl session (survives dropped connections).
#   scripts/pod_run.sh <name> <command...>   ->  logs/<name>.log, logs/<name>.pid on the pod
set -euo pipefail
name=$1; shift
kubectl exec rd -- bash -c "cd /app/reasoning-distillation && mkdir -p logs && \
  (setsid nohup $* > logs/$name.log 2>&1 < /dev/null & echo \$! > logs/$name.pid)" < /dev/null
echo "started $name"
