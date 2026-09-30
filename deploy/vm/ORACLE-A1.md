# Moving the API to Oracle's free Ampere A1 server

The API now runs on a 1 GB server. That isn't enough: the app spills into
swap (disk), and while it swaps the whole process pauses, for up to several
minutes, so every page waits. Oracle's Always Free tier also includes an
**Ampere A1** server with up to **4 CPUs and 24 GB of memory**, free for as
long as you keep the account. This guide moves the API there.

Nothing needs migrating: all data lives in Upstash Redis, and the new server
trains or loads its own model. Keep the old server running until step 7.

Time: about 30 minutes, most of it waiting for the build.

## 0. A network, if the home region has none

Skip this if the current server is in your home region: the new one uses its
network. Otherwise (Networking → **Virtual cloud networks** shows none there):

1. ☰ → **Networking** → **Virtual cloud networks** → **Start VCN Wizard**
   (on newer consoles: **Actions** → **Start VCN Wizard**) →
   **Create VCN with Internet Connectivity** → **Start VCN Wizard**.
2. Name `betiq-vcn`, compartment: the root (your tenancy name). Leave the
   address ranges as they are → **Next** → **Create**. It makes a public
   subnet, a private one and an internet gateway. All free.
3. Open the web ports: **View VCN** → **Subnets** → **public subnet-betiq-vcn**
   → **Security** (or **Security Lists**) → **Default Security List for
   betiq-vcn** → **Add Ingress Rules**:
   - Source CIDR `0.0.0.0/0`, IP protocol **TCP**, destination port **80** →
     **+ Another Ingress Rule** → the same with port **443** → **Add Ingress Rules**.

   SSH (port 22) is already allowed.
4. For the retry script below, the subnet's OCID is on the public subnet's
   page: **OCID: Copy**.

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

### "Out of capacity for shape VM.Standard.A1.Flex"

Oracle often runs out of free A1 servers, and it doesn't publish where
there's room. Free A1 only runs in your **home region** (Profile → Tenancy →
Home region; it's fixed when the account is made). Another region would be
billed, about $55 a month for 4 CPUs / 24 GB. So the options are, best first:

1. **Upgrade to Pay As You Go** (☰ → Billing → Upgrade and manage payment).
   Paying accounts get A1 capacity far more readily, and Always Free resources
   stay free after the upgrade. Set a $1 budget alert (☰ → Billing → Budgets)
   to be sure nothing is charged.
2. **Try each availability domain** in Placement. Some home regions have
   three and capacity differs between them.
3. **Ask for less:** 2 OCPUs / 12 GB (or 1 / 6) is found more often, and is
   still plenty for BetIQ. You can resize to 4 / 24 later (Edit → shape).
4. **Let Cloud Shell retry for you** (below). It tries every availability
   domain once a minute until a server is free, often within hours.

**The retry script.** First copy the subnet's OCID: open the current server
(Compute → Instances → it) → **Primary VNIC** → the subnet link → **OCID:
Copy**. Then open **Cloud Shell** (`>_`, top right) and paste, with the
subnet's OCID in the first line:

```bash
SUBNET=ocid1.subnet.oc1...paste...
OCPUS=4; MEMORY=24          # or 2 and 12
ssh-keygen -t ed25519 -f ~/.ssh/betiq -N "" <<< n >/dev/null 2>&1 || true
IMAGE=$(oci compute image list -c "$OCI_TENANCY" --operating-system "Canonical Ubuntu" \
  --operating-system-version "24.04" --shape VM.Standard.A1.Flex --sort-by TIMECREATED \
  | jq -r '.data[0].id')
ADS=$(oci iam availability-domain list -c "$OCI_TENANCY" | jq -r '.data[].name')
echo "Image: $IMAGE"; echo "Availability domains: $ADS"
while true; do
  for AD in $ADS; do
    out=$(oci compute instance launch -c "$OCI_TENANCY" --availability-domain "$AD" \
      --shape VM.Standard.A1.Flex --shape-config "{\"ocpus\":$OCPUS,\"memoryInGBs\":$MEMORY}" \
      --image-id "$IMAGE" --subnet-id "$SUBNET" --assign-public-ip true \
      --display-name betiq-api-a1 --ssh-authorized-keys-file ~/.ssh/betiq.pub 2>&1)
    if echo "$out" | grep -q '"lifecycle-state"'; then echo "Created in $AD"; break 2; fi
    if ! echo "$out" | grep -qiE 'capacity|TooManyRequests'; then echo "$out"; break 2; fi   # another error: stop and show it
    echo "$(date -u +%H:%M) no room in $AD"
  done
  sleep 60
done
```

It prints "Created in …" when it gets a server. Then Compute → Instances →
`betiq-api-a1` shows its public IP, and from the same Cloud Shell:

```bash
ssh -i ~/.ssh/betiq ubuntu@NEW-IP
```

(This replaces the key upload in step 2.) Keep the Cloud Shell tab open while
it retries; if it disconnects, paste the script again. If it stops with an
error that isn't about capacity (for example "LimitExceeded"), the output
says why: usually the free allowance is already used by another A1 server.
If your servers are in a compartment other than the root, replace
`"$OCI_TENANCY"` after `launch -c` with that compartment's OCID.

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
