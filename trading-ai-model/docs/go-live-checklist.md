# Go-Live Checklist

Use this before trusting the dashboard or enabling live execution.  
**Vercel = frontend only.** The chart watcher runs on a **separate Render worker** and shares state via **Postgres**.

---

## 1. Postgres & migrations

- [ ] `DATABASE_URL` set on **Render web** and **Render worker** (same database, `sslmode=require` for Render)
- [ ] Migrations applied (`007_runtime_controls.sql` creates `runtime_controls` for kill switch + watcher heartbeat)
- [ ] Web service reports DB connected:

```bash
curl -s https://ai-trading-app-m22o.onrender.com/dashboard | jq '.system_status.db_connected'
# expect: true
```

- [ ] TimescaleDB service row shows **Connected** (not “Set DATABASE_URL”)

---

## 2. Render web service (API)

Set on **AI-trading-App** (or your API service):

| Variable | Value |
|----------|--------|
| `DATABASE_URL` | Render Postgres external or internal URL |
| `WATCHER_SYMBOLS` | All symbols, comma-separated (23 default) |
| `WATCHER_MODE` | `live` (for display; worker does the watching) |
| `WATCHER_TIMEFRAMES` | `1m,5m,15m,1h` |
| `WATCHLIST_PRIMARY_TF` | `5m` |
| `WATCHER_TZ` | `America/New_York` |
| `CORS_ORIGINS` | Include `https://ai-trading-bot-alpha.vercel.app` and localhost |
| `POLYGON_API_KEY` | Set (market data) |
| `RISK_KILL_SWITCH` | `false` unless intentionally halting |

**Do not use `CHART_WATCHLIST`** unless you want the dashboard to show fewer symbols than the worker. Leave it commented out so UI matches `WATCHER_SYMBOLS`.

- [ ] Redeploy web service after env changes
- [ ] `GET /dashboard` returns `"source": "live"` and 23 watched charts

---

## 3. Render background worker (chart watcher)

Separate service — start command:

```bash
python main.py --mode worker
```

Worker needs **the same env as web** for watcher + DB, plus:

| Variable | Value | Notes |
|----------|--------|--------|
| `DATABASE_URL` | Same as web | Required for heartbeat + bar storage |
| `WATCHER_MODE` | `live` | |
| `WATCHER_SYMBOLS` | Same list as web | |
| `BROKER` | `polygon` | Or rely on auto-detect when `POLYGON_API_KEY` is set via `main.py` |
| `TICK_STREAM_MODE` | `websocket` or `rest` | Recommended for 24/7 crypto; default `broker` polls `/prev` and goes **Stale** quickly |
| `POLYGON_API_KEY` | Set | |
| `PAPER_TRADING_ENABLED` | `false` only when ready for live orders | |
| `COINBASE_LIVE_ENABLED` | `true` for crypto live | |
| `OANDA_LIVE_ENABLED` | `true` for forex live | |
| `OANDA_PRACTICE` | `true` for demo, `false` for real money | |

- [ ] Worker service is **Running** (not suspended; free tier may sleep)
- [ ] Logs show: `ChartWatchRunner: STARTING | mode=live | ...`
- [ ] Logs show: `ChartWatchRunner: LIVE mode | broker=polygon` (not `broker=none`)
- [ ] No repeated `PolygonBrokerAdapter[...] HTTP error` or `no tick loaders`

---

## 4. Vercel frontend

- [ ] Root directory: `trading-ai-model/frontend`
- [ ] `VITE_API_URL=https://ai-trading-app-m22o.onrender.com` (in `.env.production` or Vercel dashboard)
- [ ] Site loads `/dashboard` with **200** in Network tab
- [ ] Summary bar shows **Watcher online** (not offline)

---

## 5. Dashboard status — what “good” looks like

| Badge | Meaning |
|-------|---------|
| **Feeding** | Worker reported a recent bar for this symbol |
| **Stale** | Worker online, session open, but no recent bar (check `BROKER`, `TICK_STREAM_MODE`, Polygon) |
| **Session closed** | Market hours closed (expected for futures/forex/equities overnight) |
| **Offline** | No worker heartbeat — worker down, wrong DB, or heartbeat expired |
| **No broker** | Feeding but no live execution path (e.g. MES → Webull stub) |

Production API check:

```bash
curl -s https://ai-trading-app-m22o.onrender.com/dashboard | jq '.watcher_status'
```

Expect:

```json
{
  "online": true,
  "running": true,
  "mode": "live",
  "updated_at": "<recent ISO timestamp>",
  "feeding": <N>,
  "offline": 0
}
```

If `"online": false` and `"updated_at": null` → worker not writing to Postgres or web service missing `DATABASE_URL`.

---

## 6. Local pre-flight (optional, before prod)

```bash
# Terminal 1 — API
cd trading-ai-model/backend && source .venv/bin/activate
uvicorn api.main:app --reload

# Terminal 2 — worker
cd trading-ai-model/backend && source .venv/bin/activate
python main.py --mode worker

# Terminal 3 — frontend
cd trading-ai-model/frontend && npm run dev
```

- [ ] Local dashboard: **Watcher online**
- [ ] Crypto (24/7): **Feeding** when `TICK_STREAM_MODE=websocket` and Polygon key valid
- [ ] Kill switch toggle works (Postgres `runtime_controls`)

---

## 7. Execution readiness (what can actually trade)

| Asset | Broker | Live orders today? |
|-------|--------|-------------------|
| Crypto (BTCUSD, …) | Coinbase | Yes, if `COINBASE_LIVE_ENABLED=true` |
| Forex (EURUSD, …) | OANDA | Yes, if `OANDA_LIVE_ENABLED=true` |
| Futures (MES, NQ, …) | Webull (router) | **No** — `place_order` not implemented |
| Equities (TSLA, …) | Webull | **No** — same stub |

- [ ] Confirm `execution_mode` on dashboard matches intent (`coinbase` / `oanda` / `live`, not `paper`)
- [ ] Start with `OANDA_PRACTICE=true` or paper mode before real money
- [ ] Kill switch tested: enable → positions flatten (if any) + pipeline halts

---

## 8. Quick troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| All symbols **Offline** on Vercel | Render web missing `DATABASE_URL` and/or worker not running |
| **Offline** locally | Worker not started or different `DATABASE_URL` than API |
| Crypto **Stale** (worker online) | `BROKER=none`, or poll mode without live ticks; set `BROKER=polygon` + `TICK_STREAM_MODE=websocket` |
| Only **4 charts** on dashboard | `CHART_WATCHLIST` set — remove it |
| **Session closed** for futures Fri night | Correct — CME closed until Sun 6 PM ET |
| `db_connected: false` on prod | Set `DATABASE_URL` on Render web and redeploy |
| Watcher **online** but 0 feeding | Worker up but no bars; check Polygon logs and tick stream mode |

---

## 9. Sign-off

- [ ] Production `/dashboard`: `watcher_status.online === true`
- [ ] Production `/dashboard`: `system_status.db_connected === true`
- [ ] Vercel site matches production API (not localhost)
- [ ] Crypto feeding during 24/7 session
- [ ] Kill switch off; risk caps reviewed
- [ ] Team knows only crypto + forex can execute live today
