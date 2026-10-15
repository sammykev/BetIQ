#!/usr/bin/env bash
# Redeploy when main has new commits. setup.sh runs this every 5 minutes.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

git fetch -q origin main
if [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] && [ "${1:-}" != "--force" ]; then
  exit 0
fi

# Redeploying stops the API, which cancels its running jobs: wait while a
# long one runs (the API marks them in Redis, betiq:job:running:<id>), but
# no more than MAX_WAIT seconds
MAX_WAIT=1800
waiting="$HOME/.betiq-deploy-waiting"
busy=$(cd "$HERE" && sudo docker compose exec -T redis sh -c \
  'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning --scan --pattern "betiq:job:running:*"' 2>/dev/null |
  sed 's/^betiq:job:running://' | tr '\n' ' ' || true)
if [ -n "${busy// /}" ] && [ "${1:-}" != "--force" ]; then
  [ -f "$waiting" ] || date +%s > "$waiting"
  waited=$(( $(date +%s) - $(cat "$waiting") ))
  if [ "$waited" -lt "$MAX_WAIT" ]; then
    echo "$(date -u +%FT%TZ) waiting to deploy: running ${busy}(${waited}s)"
    exit 0
  fi
  echo "$(date -u +%FT%TZ) waited ${waited}s for ${busy}: deploying anyway"
fi
rm -f "$waiting"

echo "$(date -u +%FT%TZ) deploying $(git rev-parse --short origin/main)"
# The server never edits tracked files (data lives in a Docker volume,
# settings in the untracked .env), so a hard reset is safe
git reset -q --hard origin/main
cd "$HERE"
sudo docker compose up -d --build --remove-orphans
sudo docker image prune -f >/dev/null
