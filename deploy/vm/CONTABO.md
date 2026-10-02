# Moving the API to a Contabo VPS

The API has outgrown its 1 GB server: it spills into swap and pauses, so
every page waits. Contabo's smallest **Cloud VPS** (around 4 cores and 8 GB of
memory for about €5–6 a month; the name changes, e.g. "Cloud VPS 10") runs it
with lots of room. Pay by card or PayPal.

Nothing needs migrating: all data lives in Upstash Redis. Keep the old server
running until step 7. Time: about 30 minutes plus Contabo's setup (minutes to
a few hours).

## 1. Order

<https://contabo.com> → **VPS** → the smallest **Cloud VPS** → **Select**:

| Option | Choose |
|---|---|
| Term | **1 month** (longer terms are cheaper; 1 month may add a one-time setup fee) |
| Region | **European Union** (Germany). It's the one without a surcharge |
| Storage | The default NVMe |
| Image | **Ubuntu 24.04** (plain OS, no panel or app) |
| Login | Set a strong **root password** and write it down. If there's an **SSH key** field, paste your key (step 2) |
| Networking, add-ons | Leave the defaults (one IPv4 address is included; no extras) |

Check out, pay, and wait for the email **"Your login data"** with the server's
**IP address**. Contabo sometimes asks new customers to verify; answer that
email if it comes.

## 2. An SSH key (on your own computer)

Password logins get attacked constantly, so use a key.

**Windows (PowerShell):**

```powershell
mkdir -Force $HOME\.ssh
ssh-keygen -t ed25519 -f $HOME\.ssh\contabo
type $HOME\.ssh\contabo.pub
```

Press Enter twice when it asks for a passphrase (PowerShell drops an empty
`-N ""`, so leave `-N` out). Later, log in with `ssh -i $HOME\.ssh\contabo root@NEW-IP`.

**Mac or Linux (Terminal):**

```bash
ssh-keygen -t ed25519 -f ~/.ssh/contabo -N ""
cat ~/.ssh/contabo.pub
```

Copy the line it prints (it starts with `ssh-ed25519`).

## 3. Log in and lock it down

```bash
ssh root@NEW-IP            # the root password from step 1
```

On the server, add your key (paste the line from step 2 between the quotes):

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo 'ssh-ed25519 AAAA...paste... ' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys
```

**In a new terminal window**, check the key works (no password asked):

```bash
ssh -i ~/.ssh/contabo root@NEW-IP
```

Only when it does, turn off password logins and open the firewall for SSH and
the website:

```bash
echo 'PasswordAuthentication no' > /etc/ssh/sshd_config.d/00-keys-only.conf
systemctl restart ssh
apt-get update && apt-get install -y git ufw
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable
```

From now on log in with `ssh -i ~/.ssh/contabo root@NEW-IP`.

## 4. Get the code

Public repository:

```bash
git clone https://github.com/sammykev/BetIQ.git ~/betiq
```

Private repository: give the server a read-only deploy key first:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/github -N ""
cat ~/.ssh/github.pub
```

GitHub → **sammykev/BetIQ** → Settings → Deploy keys → **Add deploy key**,
title `contabo`, paste, leave write access off. Then:

```bash
printf 'Host github.com\n  IdentityFile ~/.ssh/github\n' >> ~/.ssh/config
ssh -o StrictHostKeyChecking=accept-new -T git@github.com   # "successfully authenticated"
git clone git@github.com:sammykev/BetIQ.git ~/betiq
```

## 5. Set up, then copy the settings from the old server

```bash
bash ~/betiq/deploy/vm/setup.sh
```

It installs Docker, writes `.env` with `DOMAIN=NEW-IP-WITH-DASHES.sslip.io`,
sets up auto-deploys, and stops to ask for the settings. (No swap is added:
the server has plenty of memory.)

In a **second** terminal, on the **old** server, print its settings without
the address:

```bash
grep -v '^DOMAIN=' ~/betiq/deploy/vm/.env
```

On the **new** server:

```bash
nano ~/betiq/deploy/vm/.env
```

Keep its `DOMAIN=` line (it must name the new IP, or HTTPS fails). Replace
everything else with what the old server printed. Save: Ctrl+O, Enter,
Ctrl+X. Start it:

```bash
bash ~/betiq/deploy/vm/setup.sh
```

The first build takes about 5 minutes; it prints the new address at the end.

## 6. Check it

```bash
cd ~/betiq/deploy/vm
docker compose ps                                      # api and caddy both "Up"
docker compose exec api python check_sportybet.py      # "SportyBet OK: … events"
```

Then open `https://NEW-IP-WITH-DASHES.sslip.io/api/health` in a browser; it
should answer within a second.

**If it says "SportyBet NOT reachable":** SportyBet refuses this server's
address. Stop here and tell me; the old server keeps working meanwhile.

**If the page doesn't load:** `docker compose logs caddy` shows whether the
HTTPS certificate was issued; `ufw status` should list 80 and 443 as ALLOW.

## 7. Switch the website, then stop the old server's app

1. Vercel → the frontend project → **Settings** → **Environment Variables** →
   `NEXT_PUBLIC_API_URL` = `https://NEW-IP-WITH-DASHES.sslip.io` → save →
   **Deployments** → latest → **⋯** → **Redeploy**.
2. Right away, on the **old** server (both servers run the same background
   jobs against the same Redis, so don't leave both on):

   ```bash
   crontab -r
   cd ~/betiq/deploy/vm && sudo docker compose down
   ```

Keep the old server for a week in case you need to switch back: start it
with `sudo docker compose up -d`, put its address back in Vercel, redeploy.

## Everyday

- Deploys stay automatic: the server checks `main` every 5 minutes (log:
  `~/betiq-deploy.log`). Deploy now: `bash ~/betiq/deploy/vm/update.sh --force`.
- Restart: `cd ~/betiq/deploy/vm && docker compose restart api`.
- Clerk, Paystack and Telegram settings don't change; only the API address did.
- Redis runs on this server too (next section). Backups: `/root/betiq-backups`
  (log: `~/betiq-backup.log`).

## Redis on this server

Everything the site stores (accounts, tickets, match days, models) used to
be on Upstash, whose free plan stops at 10 GB of traffic a month. Here it
runs next to the API: no traffic limit, and much faster. It's saved to disk
every second, a snapshot is copied to `/root/betiq-backups` every night
(kept 14 days), and it's reachable only from this machine. GitHub's nightly
jobs reach it through SSH, as a user that can do nothing else.

All commands are on the server, as root. Never paste a password or key from
these steps into a chat; copy them only into the server or into GitHub.

### 1. Start Redis

```bash
cd ~/betiq && git fetch -q origin main && git reset -q --hard origin/main
bash deploy/vm/setup.sh
```

`setup.sh` makes a Redis password (`REDIS_PASSWORD` in `.env`), adds the
nightly backup, and starts Redis next to the API (the API still uses Upstash
for now). Check: `cd ~/betiq/deploy/vm && sudo docker compose ps` lists
`redis` as running.

### 2. Copy the data from Upstash

Upstash has to answer for a few minutes: in the Upstash console open the
`betiq` database and turn on **Auto Upgrade** (or upgrade it). Then:

```bash
cd ~/betiq/deploy/vm
sudo docker compose exec api python redis_copy.py --dry-run   # what would be copied
sudo docker compose exec api python redis_copy.py             # copy
sudo docker compose exec api python redis_copy.py --check     # should end "0 differ"
```

(Skipping this starts with an empty store: the nightly GitHub jobs rebuild
the models and history over a day or two, but accounts, tickets and past
match days are lost.)

### 3. Switch the API to it

```bash
cd ~/betiq/deploy/vm
cp .env .env.before-redis
pw=$(grep '^REDIS_PASSWORD=' .env | cut -d= -f2)
sed -i "s|^UPSTASH_REDIS_URL=.*|UPSTASH_REDIS_URL=redis://:$pw@redis:6379/0|" .env
unset pw
sudo docker compose up -d
sudo docker compose logs --tail 30 api
```

The name `UPSTASH_REDIS_URL` stays; it now points at this server's Redis.
If something's wrong, go back with `cp .env.before-redis .env && sudo docker compose up -d`.

### 4. Let GitHub's jobs in

```bash
bash ~/betiq/deploy/vm/redis-tunnel.sh
```

It prints a private key. On GitHub → the repository → **Settings → Secrets
and variables → Actions**:

- **New repository secret** `REDIS_TUNNEL_KEY`: everything between the
  lines it printed.
- **New repository secret** `VM_HOST`: `13.140.188.212`.
- Edit `UPSTASH_REDIS_URL`: `redis://:PASSWORD@127.0.0.1:6379/0`, with
  PASSWORD from `grep '^REDIS_PASSWORD=' ~/betiq/deploy/vm/.env`.

Test it: **Actions → Basketball data → Run workflow**, job `fair_odds`. It
should print "Tunnel to the server's Redis is up" and then its results.

### 5. Upstash

Once the site and a night of GitHub jobs have run fine (a day or two), turn
**Auto Upgrade** off in Upstash and delete the database, so it can't bill.

### Backups

- Nightly at 04:10 UTC to `/root/betiq-backups/redis-DATE.rdb.gz`; now:
  `bash ~/betiq/deploy/vm/backup-redis.sh`.
- They're on this server only. For a copy elsewhere, download one now and
  then from your computer:
  `scp root@13.140.188.212:/root/betiq-backups/redis-DATE.rdb.gz .`
- Restore one (replaces everything stored now):
  `bash ~/betiq/deploy/vm/restore-redis.sh /root/betiq-backups/redis-DATE.rdb.gz`
