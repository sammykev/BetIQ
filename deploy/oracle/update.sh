#!/usr/bin/env bash
# Redeploy when main has new commits. setup.sh runs this every 5 minutes.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

git fetch -q origin main
if [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] && [ "${1:-}" != "--force" ]; then
  exit 0
fi

echo "$(date -u +%FT%TZ) deploying $(git rev-parse --short origin/main)"
# The server never edits tracked files (data lives in a Docker volume,
# settings in the untracked .env), so a hard reset is safe
git reset -q --hard origin/main
cd "$HERE"
sudo docker compose up -d --build
sudo docker image prune -f >/dev/null
