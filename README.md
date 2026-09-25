# BetIQ

**AI football predictions with SportyBet booking codes.** BetIQ forecasts
every major league and international match: 1X2, goals, BTTS, corners, cards
and more. It turns picks into ready-to-play SportyBet codes and grades every
prediction against the final result, so the track record stays honest.

**Live site:** <https://predict-withbetiq.vercel.app>

---

## Features

- **Predictions**: a date strip covering 7 days back to 14 days ahead, in the
  style of Flashscore. Each match shows the pre-match forecast next to the
  final score and its grading.
- **Markets**: match result, double chance, draw no bet, over/under, BTTS,
  team totals, clean sheet / win to nil, Asian handicap, corners and cards.
  Cards use referee ratings.
- **Optimizer**: build a slip for a target total odds, or paste a SportyBet
  code to check and refine it. The refine step returns three tickets: safe,
  conservative and risky.
- **Value bets**: model probability against bookmaker odds.
- **Booking codes**: slips convert to SportyBet codes. Every code an account
  generates is tracked and settled from the results.
- **Track record**: accuracy by market and day, graded after full time.
- **Dashboard and admin**: saved picks, tickets, premium via Paystack, and an
  admin console for data syncs, model status, traffic and revenue.
- **Other sports**: basketball, tennis and table tennis picks.
- **Mobile apps**: Android and iOS builds via Capacitor.

## How the model works

- **Algorithm**: XGBoost with calibrated probabilities. Inputs are Elo
  ratings, exponentially weighted form, attack-vs-defence matchups and
  bookmaker odds.
- **Model checks**: every model change is judged by a **walk-forward
  backtest** (`backend/backtest.py`). Each month is predicted by a model
  trained only on earlier matches, and a change ships only if log loss
  improves.
- **Training data**:
  - football-data.co.uk league CSVs, synced daily
  - international results, plus shots, corners and cards collected from ESPN
  - European competitions and domestic cups from ESPN. Each set is added only
    if a nightly check shows it helps.
  - referees from football-data.org
- **Corners and cards** have their own models with calibration checks.

## Architecture

| Layer | Stack | Hosting |
|---|---|---|
| Frontend | Next.js 14, Tailwind CSS, Clerk auth | Vercel |
| API | FastAPI, XGBoost, pandas, APScheduler | Render (or Docker: `deploy/`) |
| Storage | Upstash Redis (shared model, match days, tickets) | Upstash |
| Jobs | GitHub Actions (model training, stats and referee collection) | GitHub |
| Mobile | Capacitor wrapper of the web app | Codemagic / GitHub Actions |

## Repository layout

```
.
├── backend/              FastAPI app, models, data collectors
│   ├── main.py           API, scheduler and training pipeline
│   ├── predictor.py      Club model (XGBoost + Elo)
│   ├── backtest.py       Walk-forward backtest
│   ├── matchday.py       Match-day store: locked predictions and grading
│   ├── optimizer.py      Target-odds slip builder
│   ├── sportybet.py      SportyBet booking codes
│   ├── data/             League CSVs, stats and model metrics
│   └── tests/            pytest suite
├── frontend/             Next.js app (app/, components/, lib/)
├── data/                 Historical EPL and Champions League CSVs
├── deploy/               Docker deploys: vm/ (any Ubuntu server), huggingface/
├── legacy/               The original command-line predictor
├── .github/workflows/    Training, data collection, deploy and app builds
└── codemagic.yaml        iOS build
```

## Getting started

### Prerequisites

- Python 3.12
- Node.js 18+
- A free [football-data.org](https://www.football-data.org/client/register) API key

### Backend

```bash
cd backend
cp .env.example .env          # add FOOTBALL_DATA_API_KEY at minimum
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

On first start the API trains the models, which takes a few minutes, then
serves predictions at <http://localhost:8000/api/predictions>.

### Frontend

```bash
cd frontend
cp .env.local.example .env.local   # NEXT_PUBLIC_API_URL=http://localhost:8000
npm install
npm run dev
```

Open <http://localhost:3000>.

### Configuration

`backend/.env.example` and `frontend/.env.local.example` list the settings.
`backend/render.yaml` covers the rest. The main ones:

| Variable | Where | Purpose |
|---|---|---|
| `FOOTBALL_DATA_API_KEY` | backend | Fixtures and referees (football-data.org) |
| `UPSTASH_REDIS_URL` | backend, Actions | Shared model, match days, tickets |
| `ODDS_API_KEY` | backend | Bookmaker odds |
| `APIFOOTBALL_KEY` | backend, Actions | Referee appointments |
| `CLERK_ISSUER`, `CLERK_SECRET_KEY` | backend | Sign-in verification and premium checks |
| `ADMIN_USER_IDS` | backend, frontend | Who can open the admin console |
| `PAYSTACK_SECRET_KEY` | backend, frontend | Premium payments |
| `GROQ_API_KEY` | backend, frontend | AI chat and match analysis |
| `NEXT_PUBLIC_API_URL` | frontend | The API's URL |

## Tests

```bash
cd backend && python -m pytest -q     # API, models, grading, collectors
cd frontend && npm test                # components and helpers (Jest)
```

## Scheduled jobs (GitHub Actions)

| Workflow | When | What |
|---|---|---|
| `collect-international-stats.yml` | 02:30 UTC daily | International and European/cup match stats from ESPN, checked by walk-forward |
| `train-model.yml` | 04:30 UTC daily | Trains the models and publishes them to Redis for the API |
| `find-referees.yml` | Every 3 hours | Referee appointments for upcoming matches |
| `deploy-hf-space.yml` | On push to `main` | Optional Hugging Face Space deploy |
| `android-build.yml`, `ios-build.yml` | On frontend changes, or manually | Mobile app builds |

## Deployment

- **Frontend**: Vercel, with the root directory set to `frontend/`.
- **API**: Render, using `backend/render.yaml`. To self-host, the Docker image
  (`backend/Dockerfile`, built from the repository root) runs on any server;
  see [`deploy/vm`](deploy/vm/README.md) and
  [`deploy/huggingface`](deploy/huggingface/README.md).

## Disclaimer

BetIQ gives statistical forecasts, not guarantees. Bet only what you can
afford to lose, and only where betting is legal for you. 18+.
