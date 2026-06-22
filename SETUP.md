# BetIQ — Setup Guide

## What was built

| Layer | Stack | Purpose |
|-------|-------|---------|
| Backend | **FastAPI** + **XGBoost** | ML predictions, REST API |
| ML Engine | **XGBoost** + **Elo ratings** | Better accuracy than RandomForest |
| Data | **football-data.org** API | Live fixtures for 9 leagues |
| Frontend | **Next.js 14** + **Tailwind CSS** | Dark dashboard UI |
| Deployment | **Render** (backend) + **Vercel** (frontend) | Free hosting |

## Accuracy improvements over original

- **XGBoost** instead of RandomForest — better calibrated probabilities
- **Elo rating system** — dynamic team strength that evolves match-by-match
- **Exponentially weighted form** — recent games count more than old ones
- **Goals-against feature** — models defensive strength
- **Attack vs Defence matchup** — home attack vs away defence & vice versa
- **Goal variance** — consistency metric (erratic vs consistent scorers)

## Leagues supported

Premier League · La Liga · Bundesliga · Serie A · Ligue 1 · Champions League · Europa League · Primeira Liga · Eredivisie

---

## Quick Start (Local Development)

### 1. Get a free API key
Register at https://www.football-data.org/client/register (free, instant)

### 2. Backend setup

```bash
cd backend

# Copy and fill in the env file
cp .env.example .env
# Edit .env and add your API key

# Install dependencies
pip install -r requirements.txt

# Run the server
uvicorn main:app --reload --port 8000
```

The backend will:
1. Load your EPL and UCL CSV files
2. Train the XGBoost models (~30 seconds)
3. Fetch upcoming fixtures from the API
4. Serve predictions at http://localhost:8000/api/predictions

### 3. Frontend setup

```bash
cd frontend

# Copy env file
cp .env.local.example .env.local
# NEXT_PUBLIC_API_URL=http://localhost:8000 (default is fine for local)

# Install dependencies
npm install

# Run dev server
npm run dev
```

Open http://localhost:3000 to see the dashboard.

---

## Cloud Deployment (Free)

### Backend → Render

1. Push your code to GitHub
2. Go to https://render.com → New → Web Service
3. Connect your repo, select the `backend/` directory
4. Set environment variables:
   - `FOOTBALL_DATA_API_KEY` = your API key
   - `FRONTEND_URL` = your Vercel URL (e.g. https://betiq.vercel.app)
5. Build command: `pip install -r requirements.txt`
6. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`

Note: Copy your CSV files to the repo root (they're referenced as `../epl-final.csv` from `backend/`).

### Frontend → Vercel

1. Go to https://vercel.com → New Project
2. Import your GitHub repo, set **Root Directory** to `frontend/`
3. Add environment variable:
   - `NEXT_PUBLIC_API_URL` = your Render URL (e.g. https://sport-bet-predictions-api.onrender.com)
4. Deploy

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | Server status + last update time |
| `/api/leagues` | GET | List of supported leagues |
| `/api/predictions` | GET | Get predictions (filter by `league`, `min_confidence`) |
| `/api/refresh` | POST | Trigger manual data refresh |

Example:
```
GET /api/predictions?league=PL&min_confidence=0.7&limit=20
```

---

## File Structure

```
Sport-bet-predictions-main/
├── backend/
│   ├── main.py          # FastAPI app + scheduler
│   ├── predictor.py     # XGBoost + Elo ML engine
│   ├── data_fetcher.py  # football-data.org API client
│   ├── requirements.txt
│   ├── render.yaml      # Render deployment config
│   └── .env.example
├── frontend/
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx     # Main dashboard
│   │   └── globals.css
│   ├── components/
│   │   ├── PredictionCard.tsx
│   │   ├── LeagueTabs.tsx
│   │   └── ConfidenceBar.tsx
│   ├── lib/api.ts       # API client
│   ├── package.json
│   └── vercel.json
├── epl-final.csv        # EPL historical data (existing)
├── epl-2025.csv         # EPL current season (existing)
└── champions-league-*.csv  # UCL data (existing)
```
