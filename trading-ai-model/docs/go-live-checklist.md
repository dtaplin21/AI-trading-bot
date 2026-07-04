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
| `MARKET_DATA_PRIMARY` | `coinbase,oanda,polygon` | Crypto → Coinbase, forex → OANDA, futures/equities → Polygon; demotes Polygon forex/crypto |
| `POLYGON_API_KEY` | Set | Futures/equities (and crypto/forex only if `MARKET_DATA_PRIMARY=polygon,...`) |
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
- [ ] Crypto (24/7): **Feeding** when `TICK_STREAM_MODE=websocket` and Coinbase creds set (or Polygon-only primary)
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
| Crypto **Stale** (worker online) | Missing Coinbase creds with default `MARKET_DATA_PRIMARY`; or `TICK_STREAM_MODE=broker` without live ticks — set `TICK_STREAM_MODE=websocket` + Coinbase keys |
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

---

## 10. Market-data rollout (Coinbase / OANDA / Polygon split)

Use when migrating off Polygon forex/crypto feeds.

### Deploy sequence

1. **OANDA forex fix + purge bad bars** — verify `MARKET_DATA_PRIMARY=coinbase,oanda,polygon`, `TICK_STREAM_MODE=websocket`, API keys on worker + web.
2. **Deploy Coinbase adapter** — worker logs should show `TickDataLoader: coinbase → … crypto` and Polygon loaders with only `futures` / `stocks` asset types.
3. **Monitor 24h** — zero-close bars should stay at 0 for forex/crypto:

```sql
SELECT symbol,
       COUNT(*) AS bars,
       COUNT(*) FILTER (WHERE close <= 0) AS zero_close
FROM ohlcv
WHERE timeframe = '1m'
  AND time > NOW() - INTERVAL '24 hours'
GROUP BY symbol
ORDER BY zero_close DESC, symbol;
```

4. **Confirm Polygon demotion** — no `asset_type=forex` or `asset_type=crypto` Polygon WS subscriptions when Coinbase/OANDA creds are set.
5. **Dashboard** — summary bar: `Feeds: Crypto: Coinbase | Forex: OANDA | Futures: Polygon`; per-chart feed source column populated.

### Optional: delete historical zero bars

```sql
DELETE FROM ohlcv
WHERE close <= 0
  AND symbol IN (
    'EURUSD','GBPUSD','USDJPY','USDCHF','AUDUSD',
    'BTCUSD','ETHUSD','SOLUSD','BNBUSD','XRPUSD'
  );
```

Run only after new feeds are stable.

---

## 11. Future — futures & equities (Phase 7, not blocking crypto/forex)

Coinbase and OANDA do **not** cover MES, ES, NQ, TSLA, etc. The Phases 5–6 switch only demotes Polygon for **forex/crypto**; futures and equities stay on Polygon today.

| Symbol class | Market data today | Live execution today |
|--------------|-------------------|----------------------|
| Crypto | Coinbase (tick/poll) | Coinbase |
| Forex | OANDA (stream/poll) | OANDA |
| Futures (MES, NQ, …) | Polygon WS / REST | Webull stub → **REJECTED** |
| Equities (TSLA, …) | Polygon WS / REST | Webull stub → **REJECTED** |

Your current `.env` (`WATCHER_SYMBOLS` = futures + crypto only) is the right split: crypto on Coinbase, futures on Polygon, no forex until you add pairs back.

### Options when you need more than Polygon charts

1. **Keep Polygon for MES/TSLA (simplest)** — already wired via `TickDataLoader` `asset_type=futures|stocks`. No change required for watching; execution remains a separate Phase 7 item.
2. **Tradovate / IBKR market-data adapters** — add new `BrokerAdapter` + optional tick loaders; register in `MARKET_DATA_PRIMARY` after `polygon` or replace Polygon for futures only.
3. **Webull chart API** — only if they expose stable OHLCV/stream endpoints; today `WebullBroker` is execution-only stub with no market-data adapter.

### Do not block on Phase 7

- Ship crypto/forex on Coinbase/OANDA without waiting for futures execution.
- Level discovery, pipeline, and dashboard work for futures **bars** from Polygon even when live orders are unavailable.
- Track futures **execution** (Tradovate, IBKR, Webull live API) as a separate epic from market-data routing.
