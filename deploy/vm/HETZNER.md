# Moving the API to a Hetzner CX23 (2 CPUs, 4 GB)

The API has outgrown its 1 GB server: it spills into swap and pauses, so
every page waits. A Hetzner **CX23** (2 CPUs, 4 GB memory, 40 GB disk) runs
it comfortably for about €4–5 a month with its IPv4 address. Billing is by
the hour, capped at the monthly price, and you can delete it any time.

Nothing needs migrating: all data lives in Upstash Redis. Keep the old server
running until step 7. Time: about 30 minutes, plus Hetzner's account check.

## 1. Account

Sign up at <https://accounts.hetzner.com/signUp>. Hetzner checks new accounts
(card, PayPal, or ID); this can take from minutes to a day. Then open the
Cloud console <https://console.hetzner.cloud> → **New project** → `BetIQ`.

## 2. An SSH key (on your own computer)

Windows (PowerShell), Mac or Linux (Terminal):

```bash
ssh-keygen -t ed25519 -f ~/.ssh/hetzner -N ""
cat ~/.ssh/hetzner.pub
```

(Windows PowerShell: `ssh-keygen -t ed25519 -f $HOME\.ssh\hetzner` and
`type $HOME\.ssh\hetzner.pub`.) Copy the line it prints.

## 3. Create the server

In the project → **Add Server** (or **Create resource → Servers**):

| Setting | Choose |
|---|---|
| Location | **Nuremberg**, **Falkenstein** or **Helsinki** (all fine for Nigeria) |
| Image | **Ubuntu 24.04** |
| Type | **Shared vCPU** → **x86** → **CX23** (2 vCPU, 4 GB, 40 GB) |
| Networking | **Public IPv4** on, **Public IPv6** on |
| SSH keys | **Add SSH key** → paste the line from step 2 → name `my-computer` |
| Firewalls | **Create Firewall** → inbound rules TCP **22**, **80** and **443** from Any IPv4 and Any IPv6 → name `web` → select it |
| Backups, volumes, placement groups | Leave off |
| Name | `betiq-api` |

**Create & Buy now**. After a minute the server shows its **IPv4 address**.

## 4. Log in and get the code

```bash
ssh -i ~/.ssh/hetzner root@NEW-IP
```

(Answer `yes` to the fingerprint question.) Every command below runs on the
new server, as root:

```bash
apt-get update && apt-get install -y git
```

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
title `hetzner`, paste, leave write access off. Then:

```bash
printf 'Host github.com\n  IdentityFile ~/.ssh/github\n' >> ~/.ssh/config
ssh -o StrictHostKeyChecking=accept-new -T git@github.com   # "successfully authenticated"
git clone git@github.com:sammykev/BetIQ.git ~/betiq
```

## 5. Set up, then copy the settings from the old server

```bash
bash ~/betiq/deploy/vm/setup.sh
```

It installs Docker, adds swap as a safety net, writes `.env` with
`DOMAIN=NEW-IP-WITH-DASHES.sslip.io`, sets up auto-deploys, and stops to ask
for the settings.

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
address. Stop here and tell me: the old server keeps working meanwhile, and
the fix is the `SPORTYBET_PROXY` setting or another Hetzner location
(delete the server and create it again elsewhere, a few cents).

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
- Clerk, Paystack, Telegram and Upstash settings don't change; only the API
  address did.
