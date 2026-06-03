# Trading AI Dashboard (Vite + React)

**Production API:** [https://ai-trading-app-m22o.onrender.com](https://ai-trading-app-m22o.onrender.com)

## What goes in which env?

| Where | Variables | Notes |
|-------|-----------|--------|
| **Local** `frontend/.env` | `VITE_API_URL=/api` | Vite proxy → `localhost:8000` |
| **Vercel** (optional) | `VITE_API_URL` | Only if overriding `.env.production` |
| **Render web** (`AI-trading-App`) | `CORS_ORIGINS`, `DATABASE_URL`, keys… | **Not** `VITE_*` — backend only |
| **Render worker / cron** | worker/cron vars | No frontend vars |

### Frontend env (Vercel / production build)

Only this variable is used by the React app:

```bash
VITE_API_URL=https://ai-trading-app-m22o.onrender.com
```

Already set in **`.env.production`** (committed). Vercel picks it up on `npm run build` unless you override in the dashboard.

**Do not put on Vercel:** `DATABASE_URL`, `POLYGON_API_KEY`, `ANTHROPIC_API_KEY`, etc. Those stay on Render.

### Render web env (after Vercel deploy)

When using **Mode A** (direct API URL above), add your Vercel site to CORS on Render:

```bash
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,https://YOUR-PROJECT.vercel.app
```

Redeploy the Render web service after changing `CORS_ORIGINS`.

---

## Local development

```bash
npm install
cp .env.example .env
npm run dev
```

## Deploy to Vercel

1. Import repo — **Root Directory** = `trading-ai-model/frontend`
2. Framework: **Vite** — Build `npm run build`, Output `dist`
3. Pick a wiring mode:

### Mode A — Direct API (default)

- `.env.production` sets `VITE_API_URL=https://ai-trading-app-m22o.onrender.com`
- Set `CORS_ORIGINS` on Render (include your Vercel URL)
- Optional: duplicate `VITE_API_URL` in Vercel dashboard (see `env.vercel.example`)

### Mode B — Vercel proxy (no CORS change on Render)

| Vercel env | Value |
|------------|--------|
| `VITE_API_URL` | `/api` |

`vercel.json` rewrites `/api/*` → `https://ai-trading-app-m22o.onrender.com/*`

## Verify

Open the deployed site → DevTools → Network. Requests should hit `/dashboard`, `/trades`, or the Render host and return **200**.
