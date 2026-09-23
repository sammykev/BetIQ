# Moving the BetIQ API to Oracle Cloud (Always Free)

Oracle's Always Free tier includes an ARM server with up to 4 CPUs and 24 GB
of memory that never sleeps. The API runs there in Docker, behind Caddy,
which gets an HTTPS certificate by itself. The trained model and data live on
a Docker volume, so restarts load the saved model instead of retraining. The
server checks GitHub every 5 minutes and redeploys when `main` changes.

Keep Render running until step 8 works.

## 1. Create the account

1. Sign up at <https://signup.cloud.oracle.com>. A card is needed for
   identity verification; Always Free resources are never charged.
2. **Home region:** pick one near your users that has ARM capacity, e.g.
   *UK South (London)* or *South Africa Central (Johannesburg)*. It can't be
   changed later.

## 2. Create the server

Menu → Compute → Instances → **Create instance**.

| Setting | Value |
|---|---|
| Image | **Canonical Ubuntu 24.04** |
| Shape | Change shape → Ampere → **VM.Standard.A1.Flex**, 2 OCPUs, 12 GB memory (free up to 4 / 24) |
| Networking | Keep the defaults; make sure **Assign a public IPv4 address** is on |
| SSH keys | **Generate a key pair for me** → download the private key |
| Boot volume | Default (≈50 GB) is enough |

Create it. Copy its **Public IP address** from the instance page.

"Out of capacity" is common for the free ARM shape. Try another availability
domain or try again later. Upgrading the account to *Pay As You Go* (Billing →
Upgrade) usually removes the problem. Always Free resources are still free on
that plan, but set a budget alert to be safe.

## 3. Open the web ports

Instance page → the **Subnet** link → **Default Security List** →
**Add Ingress Rules**, twice:

| Source CIDR | IP protocol | Destination port |
|---|---|---|
| `0.0.0.0/0` | TCP | `80` |
| `0.0.0.0/0` | TCP | `443` |

## 4. Log in

On your computer (use the key you downloaded):

```bash
chmod 400 ~/Downloads/ssh-key-*.key
ssh -i ~/Downloads/ssh-key-*.key ubuntu@YOUR_PUBLIC_IP
```

On Windows, use PowerShell with the same `ssh` command.

## 5. Get the code

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

## 6. Set up and add the settings

```bash
bash ~/betiq/deploy/oracle/setup.sh
```

This installs Docker, opens the firewall and sets the web address to
`YOUR-IP.sslip.io`. It then asks you to fill in the settings:

```bash
nano ~/betiq/deploy/oracle/.env
```

Copy each value from Render (dashboard → backend service → **Environment**).
Save with Ctrl+O, Enter, then Ctrl+X. Run the script again to start:

```bash
bash ~/betiq/deploy/oracle/setup.sh
```

The first build takes about 5 minutes.

## 7. Check it

Open `https://YOUR-IP.sslip.io/api/health` (the address is in `.env` as
`DOMAIN`). The first start trains the model, which takes a minute or two.

```bash
cd ~/betiq/deploy/oracle
sudo docker compose logs -f api      # live logs (Ctrl+C to stop)
sudo docker compose ps               # both containers "Up"
```

## 8. Point the website at it

Vercel → the frontend project → Settings → Environment Variables →
`NEXT_PUBLIC_API_URL` = `https://YOUR-IP.sslip.io` → **Redeploy**.

Check that the site loads predictions and that the `/betiq-hq` checks pass,
then suspend the Render service.

## Everyday

- **Deploys:** automatic within 5 minutes of a push to `main`
  (log: `~/betiq-deploy.log`). Deploy now: `bash ~/betiq/deploy/oracle/update.sh --force`.
- **Change a setting:** edit `.env`, then `cd ~/betiq/deploy/oracle && sudo docker compose up -d`.
- **Restart:** `sudo docker compose restart api`.

## If something is wrong

- **The page doesn't load:** step 3's ingress rules are missing, or the
  certificate is still being issued. Check with `sudo docker compose logs caddy`.
- **Certificate errors mentioning sslip.io:** get a free name at
  <https://www.duckdns.org> pointing to your IP. Put it in `.env` as
  `DOMAIN=yourname.duckdns.org`, run `sudo docker compose up -d`, and use that
  address in Vercel.
- **Oracle stopped the server for being idle:** this can happen to Always Free
  servers that barely use their CPU. The daily retraining normally prevents it.
