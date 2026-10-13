#!/usr/bin/env bash
# Send N requests to the load balancer and show how they were distributed.
set -euo pipefail

REQUESTS="${1:-30}"
URL="${LB_URL:-http://localhost:8080/}"

echo "Sending $REQUESTS requests to $URL"
echo "-----------------------------------------"

for i in $(seq 1 "$REQUESTS"); do
  response=$(curl -s "$URL" || echo '{"instance":"ERROR"}')
  instance=$(echo "$response" | grep -o '"instance":"[^"]*"' | cut -d'"' -f4)
  ip=$(echo "$response" | grep -o '"ip":"[^"]*"' | cut -d'"' -f4)
  printf "Request %3d -> %-6s (%s)\n" "$i" "$instance" "${ip:-n/a}"
  echo "$instance" >> /tmp/lb_results.$$
done

echo "-----------------------------------------"
echo "Distribution:"
sort /tmp/lb_results.$$ | uniq -c | sort -rn
rm -f /tmp/lb_results.$$
