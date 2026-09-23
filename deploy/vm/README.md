# Hosting the BetIQ API on a free Google Cloud server

Google Cloud's Always Free tier includes one small server (e2-micro: 2 shared
CPUs, 1 GB memory, 30 GB disk) that never sleeps. The API runs there in
Docker, behind Caddy, which gets an HTTPS certificate by itself. The trained
model and data stay on disk, so restarts load the saved model instead of
retraining. The server checks GitHub every 5 minutes and redeploys when `main`
changes.

The same scripts work on Oracle Cloud or any Ubuntu server (see the end).

**Staying free:** use exactly the settings in step 2: e2-micro, a US region
(`us-east1`, `us-central1` or `us-west1`) and a **Standard** 30 GB disk. The
other disk types are charged. The free tier includes 1 GB of outbound traffic
a month. BetIQ's responses are compressed, but if the site gets busy, Google
charges about $0.12 per extra GB. The budget alert in step 1 warns you first.

Keep Render running until step 7 works.

## 1. Account, project and budget alert

1. Go to <https://console.cloud.google.com> and sign in with a Google account.
   Accept the terms. **Start free** asks for a card to verify you; you aren't
   charged. New accounts also get trial credit.
2. Top bar → project picker → **New project** → name `betiq` → **Create**.
   Select it in the project picker.
3. ☰ → **Billing** → **Budgets & alerts** → **Create budget** → amount
   **$1** → keep the email alerts → **Finish**. You'll get an email if anything
   ever costs money.

## 2. Create the server

☰ → **Compute Engine** → **VM instances**. The first visit asks you to
**Enable** the Compute Engine API; wait a minute. Then **Create instance**:

| Section | Setting |
|---|---|
| Machine configuration | Name `betiq-api`. Region **us-east1 (South Carolina)**. Zone: any. Series **E2**, machine type **e2-micro** |
| OS and storage → **Change** | Operating system **Ubuntu**, version **Ubuntu 24.04 LTS** (x86/64, *not* "Minimal", *not* Arm). Boot disk type **Standard persistent disk**, size **30** GB → **Select** |
| Networking | Tick **Allow HTTP traffic** and **Allow HTTPS traffic** |
| Observability | Untick **Install Ops Agent** (it uses memory the app needs) |

The price estimate on the right should mention the free tier (e.g. "744 hours
free"). Click **Create**. When the status turns green, copy the
**External IP**.

## 3. Open a terminal on the server

On the VM instances list, click **SSH** next to `betiq-api`. A terminal
opens in your browser; click **Authorize** if asked. Every command below goes
there. Paste with Ctrl+V, or Shift+Insert on some keyboards.

## 4. Get the code

**Public repository:**

```bash
git clone https://github.com/sammykev/BetIQ.git ~/betiq
```

**Private repository:** give the server a read-only deploy key first:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/github -N ""
cat ~/.ssh/github.pub
```

Copy the line it prints. On GitHub: repository → Settings → Deploy keys →
**Add deploy key**, paste it and leave write access off. Then:

```bash
printf 'Host github.com\n  IdentityFile ~/.ssh/github\n' >> ~/.ssh/config
ssh -o StrictHostKeyChecking=accept-new -T git@github.com   # "successfully authenticated"
git clone git@github.com:sammykev/BetIQ.git ~/betiq
```

## 5. Set up and add the settings

```bash
bash ~/betiq/deploy/vm/setup.sh
```

This installs Docker, adds swap memory and sets the web address to
`YOUR-IP.sslip.io`. It then asks you to fill in the settings:

```bash
nano ~/betiq/deploy/vm/.env
```

Copy each value from Render (dashboard → backend service → **Environment**).
Save with Ctrl+O, Enter, then Ctrl+X. Run the script again to start:

```bash
bash ~/betiq/deploy/vm/setup.sh
```

The first build takes about 10 minutes on this small server.

## 6. Check it

Open `https://YOUR-IP-WITH-DASHES.sslip.io/api/health` (the exact address is
the `DOMAIN` line the script prints). The first start trains the model, which
takes a few minutes on the e2-micro. The site answers during training.

```bash
cd ~/betiq/deploy/vm
sudo docker compose logs -f api      # live logs; wait for "Predictor ready" (Ctrl+C to stop)
sudo docker compose ps               # both containers "Up"
```

## 7. Point the website at it

Vercel → the frontend project → **Settings** → **Environment Variables** →
set `NEXT_PUBLIC_API_URL` to `https://YOUR-IP-WITH-DASHES.sslip.io` →
**Deployments** → latest → **⋯** → **Redeploy**.

Check that the site loads predictions and that the `/betiq-hq` checks pass.
Then suspend the Render service.

## Everyday

- **Deploys:** automatic within 5 minutes of a push to `main`
  (log: `~/betiq-deploy.log`). Deploy now: `bash ~/betiq/deploy/vm/update.sh --force`.
- **Change a setting:** edit `.env`, then `cd ~/betiq/deploy/vm && sudo docker compose up -d`.
- **Restart:** `sudo docker compose restart api`.

## If something is wrong

- **The page doesn't load:** check that *Allow HTTP/HTTPS traffic* is ticked
  (VM instance → **Edit** → Networking). The certificate can take a minute
  on first start: `sudo docker compose logs caddy`.
- **Certificate errors mentioning sslip.io:** get a free name at
  <https://www.duckdns.org> pointing to your IP. Put it in `.env` as
  `DOMAIN=yourname.duckdns.org`, run `sudo docker compose up -d`, and use that
  address in Vercel.
- **Google says the trial ended:** click **Activate full account** so the
  server keeps running. The e2-micro, its disk and its IP stay free.

## Oracle Cloud or another server instead

Any Ubuntu 22.04/24.04 server works. Create it with a public IP, allow
incoming TCP 80 and 443 in the provider's firewall, SSH in, then follow steps
4–7. On Oracle Cloud: the ports are added under the instance's subnet →
**Default Security List** → **Add Ingress Rules** (source `0.0.0.0/0`, TCP 80,
then 443). The script also opens them in Oracle's Ubuntu firewall.
