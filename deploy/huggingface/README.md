# Hosting the BetIQ API on Hugging Face Spaces (free)

A free Space gives the API 2 CPUs and 16 GB of memory, far more than Render's
free plan, so retraining takes about a minute. Every push to `main` that
touches the backend runs the tests, then GitHub Actions uploads the backend
and the Space rebuilds itself.

Things to know:

- **The Space is public**, so anyone can read the backend code. Settings
  entered as *secrets* (API keys, passwords) stay private.
- **Its disk is wiped on restart**, so the model retrains after each deploy or
  restart (about a minute). Predictions history lives in Upstash Redis and is
  unaffected.
- **A free Space sleeps after 48 hours without visits.** Normal site traffic
  keeps it awake; a free monitor (step 6) makes sure.

Keep Render running until step 5 works.

## 1. Create a Hugging Face account and token

1. Sign up at <https://huggingface.co/join>. Your username is part of the
   API address.
2. Open <https://huggingface.co/settings/tokens> → **Create new token** →
   **Write** → name it `github-deploy` → **Create token** → copy it. You won't
   see it again.

## 2. Connect GitHub

On GitHub: the repository → **Settings** → **Secrets and variables** → **Actions**.

1. **Secrets** tab → **New repository secret**. Name `HF_TOKEN`, value: the
   token from step 1.
2. **Variables** tab → **New repository variable**. Name `HF_SPACE`, value
   `YOUR_HF_USERNAME/betiq-api`.

## 3. First deploy

GitHub → **Actions** → **Deploy API to Hugging Face** → **Run workflow** →
**Run workflow**. It runs the tests (about 2 minutes), creates the Space and
uploads the backend. After that, deploys happen by themselves on every push.

## 4. Add the settings to the Space

Open `https://huggingface.co/spaces/YOUR_HF_USERNAME/betiq-api` →
**Settings** → **Variables and secrets** → **New secret**, once for each
setting below. Copy the values from Render (dashboard → backend service →
**Environment**). Skip any that are empty on Render.

```
FOOTBALL_DATA_API_KEY     UPSTASH_REDIS_URL        ADMIN_SECRET
FRONTEND_URL              CLERK_ISSUER             CLERK_AUTHORIZED_PARTIES
ODDS_API_KEY              GROQ_API_KEY             DEEPSEEK_API_KEY
PAYSTACK_SECRET_KEY       VAPID_PUBLIC_KEY         VAPID_PRIVATE_KEY
VAPID_CLAIMS_EMAIL        API_BASKETBALL_KEY       BETSAPI_TOKEN
SPORTYBET_COUNTRY (ng)    SPORTYBET_PROXY
```

Then **Settings** → **Factory rebuild**, so the app starts with them. The
**Logs** tab (next to *App* at the top) shows it training; look for
`Predictor ready`.

Check: `https://YOUR_HF_USERNAME-betiq-api.hf.space/api/health` should show
`"status":"ok"`.

## 5. Point the website at it

Vercel → the frontend project → **Settings** → **Environment Variables** →
set `NEXT_PUBLIC_API_URL` to `https://YOUR_HF_USERNAME-betiq-api.hf.space` →
**Deployments** → the latest one → **⋯** → **Redeploy**.

Check that the site loads predictions and that the `/betiq-hq` checks pass.
Then suspend the Render service.

## 6. Keep it awake (recommended)

At <https://uptimerobot.com> (free), add an **HTTP(s)** monitor for
`https://YOUR_HF_USERNAME-betiq-api.hf.space/api/health` every 5 minutes.

## Everyday

- **Deploy:** push to `main`. GitHub → Actions shows progress; the Space's
  **Logs** tab shows the rebuild.
- **Change a setting:** Space → Settings → Variables and secrets. The Space
  restarts by itself.
- **Restart:** Space → Settings → **Restart this Space**.
