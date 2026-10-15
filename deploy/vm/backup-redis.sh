#!/usr/bin/env bash
# A daily copy of the Redis snapshot, kept 14 days in /root/betiq-backups
# (setup.sh runs it from cron). Restore: CONTABO.md, "Redis on this server".
# With BACKUP_REMOTE in .env, the copy is also encrypted (BACKUP_PASSPHRASE)
# and uploaded off this server with rclone (RCLONE_CONFIG_* in .env), kept
# BACKUP_REMOTE_DAYS there. When it ran goes to Redis (betiq:backup:last),
# which /api/health shows and the site monitor checks.
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

# A setting from .env (without surrounding quotes)
setting() { { grep -E "^$1=" .env || true; } | tail -1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//'; }

upload() {
  local remote="$1" days pass enc conf rc=0
  days="$(setting BACKUP_REMOTE_DAYS)"; days="${days:-60}"
  pass="$(setting BACKUP_PASSPHRASE)"
  [ -n "$pass" ] || { echo "BACKUP_PASSPHRASE isn't set in .env: not uploading"; return 1; }
  enc="$out.gz.enc"
  BACKUP_PASSPHRASE="$pass" openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt \
    -pass env:BACKUP_PASSPHRASE -in "$out.gz" -out "$enc" || return 1
  conf="$(mktemp)"
  chmod 600 "$conf"
  grep -E '^RCLONE_CONFIG_' .env > "$conf" || true
  rclone() { sudo docker run --rm --env-file "$conf" -v "$dir":/backups rclone/rclone:1.68.2 "$@"; }
  rclone copy "/backups/$(basename "$enc")" "$remote" || rc=1
  [ $rc = 0 ] && { rclone delete --min-age "${days}d" "$remote" || true; }
  rm -f "$conf" "$enc"
  [ $rc = 0 ] && echo "$(date -u +%FT%TZ) uploaded to $remote"
  return $rc
}

offsite=off
remote="$(setting BACKUP_REMOTE)"
if [ -n "$remote" ]; then
  if upload "$remote"; then offsite=ok; else offsite=failed; fi
fi
printf '{"at":"%s","bytes":%s,"offsite":"%s"}' "$(date -u +%FT%TZ)" "$(stat -c %s "$out.gz")" "$offsite" |
  sudo docker compose exec -T redis sh -c 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning -x SET betiq:backup:last' >/dev/null
[ "$offsite" != failed ]
