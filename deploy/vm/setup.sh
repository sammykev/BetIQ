#!/usr/bin/env bash
# One-time setup for the BetIQ API on an Ubuntu server (Google Cloud,
# Oracle Cloud or any other). Safe to run again:
#   bash deploy/vm/setup.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
cd "$HERE"

echo "==> Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo usermod -aG docker "$USER"
sudo systemctl enable --now docker

echo "==> Firewall: allow web traffic (ports 80 and 443)"
# Oracle's Ubuntu images reject everything except SSH at the OS level, on
# top of the cloud firewall. Other clouds (Google) filter only in the cloud.
reject=$(sudo iptables -L INPUT --line-numbers -n 2>/dev/null | awk '$2 == "REJECT" {print $1; exit}')
if [ -n "$reject" ]; then
  for port in 443 80; do
    sudo iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null \
      || sudo iptables -I INPUT "$reject" -p tcp --dport "$port" -j ACCEPT
  done
  command -v netfilter-persistent >/dev/null \
    || sudo DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent
  sudo netfilter-persistent save
fi

echo "==> Swap (only on small machines)"
mem_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if [ "$mem_mb" -lt 4000 ] && [ ! -f /swapfile ]; then
  sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "==> Settings (.env)"
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
fi
if ! grep -q '^DOMAIN=.\+' .env; then
  ip=$(curl -fsS https://api.ipify.org)
  sed -i "s|^DOMAIN=.*|DOMAIN=${ip//./-}.sslip.io|" .env
fi
grep '^DOMAIN=' .env

echo "==> Auto-deploy: check GitHub for new commits every 5 minutes"
line="*/5 * * * * bash $HERE/update.sh >> $HOME/betiq-deploy.log 2>&1"
# (A new server has no crontab yet: `crontab -l` fails, which must not stop the script)
{ crontab -l 2>/dev/null | grep -v 'deploy/vm/update.sh' || true; echo "$line"; } | crontab -

echo "==> Daily Redis backup (04:10 UTC, kept 14 days in /root/betiq-backups)"
line="10 4 * * * bash $HERE/backup-redis.sh >> $HOME/betiq-backup.log 2>&1"
{ crontab -l 2>/dev/null | grep -v 'deploy/vm/backup-redis.sh' || true; echo "$line"; } | crontab -
if ! grep -q '^REDIS_PASSWORD=.\+' .env; then
  if grep -q '^REDIS_PASSWORD=' .env; then
    sed -i "s|^REDIS_PASSWORD=.*|REDIS_PASSWORD=$(openssl rand -hex 24)|" .env
  else
    echo "REDIS_PASSWORD=$(openssl rand -hex 24)" >> .env
  fi
  echo "Made a Redis password (REDIS_PASSWORD in .env)"
fi

if grep -q '^FOOTBALL_DATA_API_KEY=$' .env; then
  echo
  echo "Now fill in $HERE/.env (nano $HERE/.env), then run this script again."
  exit 0
fi

echo "==> Starting (the first build takes a few minutes)"
sudo docker compose up -d --build
domain=$(grep '^DOMAIN=' .env | cut -d= -f2)
echo
echo "Done. In a minute or two: https://$domain/api/health"
