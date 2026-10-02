#!/usr/bin/env bash
# Put a backup from backup-redis.sh back into this server's Redis
# (everything stored now is replaced). Run as root:
#   bash deploy/vm/restore-redis.sh /root/betiq-backups/redis-YYYY-MM-DD.rdb.gz
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
backup="${1:?which backup? ls /root/betiq-backups}"
[ -f "$backup" ] || { echo "No such file: $backup"; exit 1; }
vol="$(basename "$HERE")_redis-data"
sudo docker volume inspect "$vol" >/dev/null

echo "==> Stopping the API and Redis"
sudo docker compose stop api redis
tmp=$(mktemp -d)
gunzip -c "$backup" > "$tmp/dump.rdb"
# With the append-only file on, Redis ignores a snapshot on its own: load it
# with that off, then turn it on (which writes the append-only file from it)
sudo docker run --rm -v "$vol":/data -v "$tmp":/restore redis:7-alpine \
  sh -c 'rm -rf /data/appendonlydir && cp /restore/dump.rdb /data/dump.rdb && chown -R redis:redis /data'
rm -rf "$tmp"
echo "==> Loading the backup"
sudo docker rm -f betiq-redis-restore >/dev/null 2>&1 || true
sudo docker run -d --name betiq-redis-restore -v "$vol":/data redis:7-alpine redis-server --appendonly no >/dev/null
cli() { sudo docker exec betiq-redis-restore redis-cli "$@"; }
for _ in $(seq 1 300); do cli dbsize 2>/dev/null | grep -qE '^[0-9]+$' && break; sleep 1; done
echo "    $(cli dbsize) keys"
cli config set appendonly yes >/dev/null
for _ in $(seq 1 300); do
  info=$(cli info persistence)
  echo "$info" | grep -q 'aof_enabled:1' && echo "$info" | grep -q 'aof_rewrite_in_progress:0' \
    && echo "$info" | grep -q 'aof_rewrite_scheduled:0' && break
  sleep 1
done
cli shutdown >/dev/null 2>&1 || true
sudo docker rm -f betiq-redis-restore >/dev/null 2>&1 || true
echo "==> Starting Redis and the API"
sudo docker compose up -d
sleep 3
echo "Redis now has $(sudo docker compose exec -T redis sh -c 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning dbsize') keys"
