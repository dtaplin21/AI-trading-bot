# Trading AI Model

Monorepo for the multi-agent futures trading system.

| Directory | Stack | Purpose |
|-----------|-------|---------|
| [`backend/`](backend/) | Python 3.11–3.13, FastAPI, LightGBM | Agents, API, ML pipeline, TimescaleDB |
| [`frontend/`](frontend/) | React, Vite | Trading dashboard UI |

## Quick Start

### Backend

```bash
cd backend
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
pytest
uvicorn api.main:app --reload
```

API runs at http://127.0.0.1:8000

### Frontend

```bash
cd frontend
npm install
npm run dev
```

UI runs at http://localhost:5173 (proxies `/api` → backend on port 8000).

### Database

```bash
cd backend
docker compose up -d
```

See [`backend/README.md`](backend/README.md) for architecture, retraining, and LLM setup.
