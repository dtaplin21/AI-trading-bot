# Trading AI Model

Monorepo for the multi-agent futures trading system.

| Directory | Stack | Purpose |
|-----------|-------|---------|
| [`backend/`](backend/) | Python 3.11–3.13, FastAPI, LightGBM | Agents, API, ML pipeline, TimescaleDB |
| [`frontend/`](frontend/) | React, Vite | Trading dashboard UI |

## Quick Start

### One command — API + dashboard (recommended)

```bash
cd backend
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp ../../.env.example .env   # or copy BACKEND section into backend/.env

cd ../frontend && npm install && cd ../backend

python main.py --mode dev
```

- **API:** http://127.0.0.1:8000  
- **Dashboard:** http://localhost:5173 (Vite proxies `/api` → backend)

From the monorepo root you can also run: `npm run dev` (after `npm run install:frontend`).

### Backend only

```bash
cd backend
source .venv/bin/activate
python main.py --mode api
```

### Frontend only

```bash
cd frontend
npm install
npm run dev
```

Requires the API on port 8000 or you will see Vite proxy `ECONNREFUSED` errors.

### Database

```bash
cd backend
docker compose up -d
```

See [`backend/README.md`](backend/README.md) for architecture, retraining, and LLM setup.
