#!/usr/bin/env bash
# Live view of the "Auto Scaling Group": replica count, CPU and autoscaler decisions.
cd "$(dirname "$0")/.." || exit 1

while true; do
  clear
  echo "=== Auto Scaling Group: app replicas ($(date +%T)) ==="
  docker compose ps app --format "table {{.Name}}\t{{.Status}}"
  echo
  echo "=== CPU usage (limit: 0.5 CPU per replica) ==="
  docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" \
    $(docker compose ps app -q) 2>/dev/null
  echo
  echo "=== Autoscaler decisions (latest) ==="
  docker logs --tail 8 autoscaler 2>&1
  sleep 3
done
