#!/usr/bin/env bash
# A daily copy of the Redis snapshot, kept 14 days in /root/betiq-backups
# (setup.sh runs it from cron). Restore: CONTABO.md, "Redis on this server".
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
dir=/root/betiq-backups
mkdir -p "$dir"
chmod 700 "$dir"
# A fresh snapshot, then wait for it to finish
before=$(sudo docker compose exec -T redis sh -c 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning LASTSAVE')
sudo docker compose exec -T redis sh -c 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning BGSAVE' >/dev/null
for _ in $(seq 1 120); do
  now=$(sudo docker compose exec -T redis sh -c 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning LASTSAVE')
  [ "$now" != "$before" ] && break
  sleep 2
done
out="$dir/redis-$(date -u +%F).rdb"
sudo docker compose cp redis:/data/dump.rdb "$out"
gzip -f "$out"
find "$dir" -name 'redis-*.rdb.gz' -mtime +14 -delete
echo "$(date -u +%FT%TZ) backed up $(du -h "$out.gz" | cut -f1) to $out.gz"
