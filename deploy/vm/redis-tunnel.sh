#!/usr/bin/env bash
# Lets GitHub's jobs reach this server's Redis through SSH, and nothing else:
# a user "redis-tunnel" with no shell whose one key may only forward to
# 127.0.0.1:6379. Run once as root:
#   bash deploy/vm/redis-tunnel.sh
# then paste the private key it prints into GitHub (see CONTABO.md,
# "Redis on this server"). Safe to run again: it makes a new key each time.
set -euo pipefail

user=redis-tunnel
if ! id "$user" >/dev/null 2>&1; then
  useradd --create-home --shell /usr/sbin/nologin "$user"
fi
home=$(getent passwd "$user" | cut -d: -f6)
install -d -m 700 -o "$user" -g "$user" "$home/.ssh"

key=$(mktemp -u /root/redis-tunnel-key.XXXX)
ssh-keygen -q -t ed25519 -N "" -C "github-actions redis tunnel" -f "$key"
# restrict: no shell, no terminal, no other forwarding; only Redis on this machine
echo "restrict,port-forwarding,permitopen=\"127.0.0.1:6379\",command=\"/usr/sbin/nologin\" $(cat "$key.pub")" \
  > "$home/.ssh/authorized_keys"
chown "$user:$user" "$home/.ssh/authorized_keys"
chmod 600 "$home/.ssh/authorized_keys"

echo
echo "Copy everything between the lines into GitHub → Settings → Secrets and"
echo "variables → Actions → New repository secret, named REDIS_TUNNEL_KEY:"
echo "-----------------------------------------------------------------------"
cat "$key"
echo "-----------------------------------------------------------------------"
rm -f "$key" "$key.pub"
echo "(The key isn't kept on this server; run this again for a new one.)"
