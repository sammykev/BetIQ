# Moving the API to Oracle's free Ampere A1 server

The API now runs on a 1 GB server. That isn't enough: the app spills into
swap (disk), and while it swaps the whole process pauses, for up to several
minutes, so every page waits. Oracle's Always Free tier also includes an
**Ampere A1** server with up to **4 CPUs and 24 GB of memory**, free for as
long as you keep the account. This guide moves the API there.

Nothing needs migrating: all data lives in Upstash Redis, and the new server
trains or loads its own model. Keep the old server running until step 7.

Time: about 30 minutes, most of it waiting for the build.

## 1. Create the A1 server

Oracle Cloud console (<https://cloud.oracle.com>) → ☰ → **Compute** →
**Instances** → **Create instance**. Use your **home region** (top right);
Always Free A1 only runs there.

| Section | Setting |
|---|---|
| Name | `betiq-api-a1` |
| Placement | Any availability domain (see "Out of capacity" below) |
| Image and shape → **Edit** → **Change image** | **Canonical Ubuntu**, **24.04** (not "Minimal") |
| Image and shape → **Change shape** | **Virtual machine** → **Ampere** → **VM.Standard.A1.Flex**. Set **4 OCPUs** and **24 GB** memory (the whole free allowance; 2 OCPUs / 12 GB also works) |
| Networking | Pick the **same virtual cloud network and public subnet** as the current server, so ports 80 and 443 are already open. **Assign a public IPv4 address**: yes |
| Add SSH keys | **Generate a key pair for me** → **Save private key** (keep the file) |
| Boot volume | Leave the default (about 47 GB) |

The shape panel should say **Always Free-eligible**. Click **Create**. When
the state turns **Running**, copy the **Public IP address**.

**"Out of capacity for shape VM.Standard.A1.Flex":** Oracle often runs out
of free A1 servers. Try each availability domain in Placement, or try again
a few hours later (early morning UTC works best). Upgrading the account to
**Pay As You Go** usually fixes it at once. Always Free resources stay free
after the upgrade, so nothing is charged. Set a budget alert (☰ → Billing →
Budgets) of $1 to be sure.

## 2. Open a terminal on the new server

Easiest: the **Cloud Shell** icon (top right, `>_`) → upload the private key
you saved (Cloud Shell menu → Upload), then:

```bash
chmod 600 ~/ssh-key-*.key
ssh -i ~/ssh-key-*.key ubuntu@NEW-IP
```

(From your own computer, the same `ssh` command works in a terminal.) Every
command below runs on the new server.

## 3. Get the code

If the repository is **public**:

```bash
git clone https://github.com/sammykev/BetIQ.git ~/betiq
```

If it's **private**, give this server its own read-only deploy key first:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/github -N ""
cat ~/.ssh/github.pub
```

Copy the line it prints. GitHub → **sammykev/BetIQ** → Settings → Deploy keys
→ **Add deploy key**, title `betiq-api-a1`, paste, leave write access off.
Then:

```bash
printf 'Host github.com\n  IdentityFile ~/.ssh/github\n' >> ~/.ssh/config
ssh -o StrictHostKeyChecking=accept-new -T git@github.com   # "successfully authenticated"
git clone git@github.com:sammykev/BetIQ.git ~/betiq
```

## 4. Set up, then copy the settings from the old server

```bash
bash ~/betiq/deploy/vm/setup.sh
```

This installs Docker, opens ports 80 and 443 in Ubuntu's firewall, and writes
`.env` with `DOMAIN=NEW-IP-WITH-DASHES.sslip.io`. It stops and asks for the
settings. (It skips swap: the server has plenty of memory.)

Now copy the settings. In a **second** terminal, on the **old** server:

```bash
grep -v '^DOMAIN=' ~/betiq/deploy/vm/.env
```

Back on the **new** server, open the file:

```bash
nano ~/betiq/deploy/vm/.env
```

Keep its `DOMAIN=` line as it is (it must name the new IP, or the HTTPS
certificate fails). Replace everything else with what the old server printed.
Save with Ctrl+O, Enter, then Ctrl+X. Then start it:

```bash
bash ~/betiq/deploy/vm/setup.sh
```

The first build takes about 5 minutes. The script prints the new address.

## 5. Check it

Open `https://NEW-IP-WITH-DASHES.sslip.io/api/health` in a browser. It
should answer within a second.

```bash
cd ~/betiq/deploy/vm
sudo docker compose ps                # api and caddy both "Up"
sudo docker compose logs -f api       # wait for "Predictor ready" (Ctrl+C to stop)
```

## 6. Point the website at it

Vercel → the frontend project → **Settings** → **Environment Variables** →
`NEXT_PUBLIC_API_URL` → set it to `https://NEW-IP-WITH-DASHES.sslip.io` →
save → **Deployments** → latest → **⋯** → **Redeploy**.

When it's done, open the site: predictions should appear quickly, and the
tennis and basketball tabs should show live scores and stats.

## 7. Stop the old server's app

Do this right after step 6. Both servers run the same background jobs
against the same Redis, so don't leave both on for long. On the **old**
server:

```bash
crontab -r                                  # stop its auto-deploys
cd ~/betiq/deploy/vm && sudo docker compose down
```

Keep the old instance itself (stopped or running, it's free) for a week in
case you need to switch back. To switch back: `sudo docker compose up -d`
there, put its address back in Vercel, and redeploy.

## After the move

- Deploys stay automatic: the new server checks `main` every 5 minutes.
- The live timings (read by the **Basketball data** workflow, job
  `read_probe`) should show `memory_mb` with no swap and `loop_lag` p95 in
  milliseconds.
- The site's address for the API is the only thing that changed. Clerk,
  Paystack, Telegram and Upstash settings stay as they are.
