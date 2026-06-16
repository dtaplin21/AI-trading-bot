import { useEffect, useState } from "react";
import TradeResultsDashboard from "./components/TradeResultsDashboard.jsx";
import SystemStatusPanel from "./components/SystemStatusPanel.jsx";
import ProgressPanel from "./components/ProgressPanel.jsx";
import { fetchTrades } from "./api/trades.js";
import { fetchDashboard } from "./api/dashboard.js";
import { fetchProgress } from "./api/progress.js";

const REFRESH_MS = 30_000;

export default function App() {
  const [tab, setTab] = useState("dashboard");
  const [trades, setTrades] = useState([]);
  const [dashboard, setDashboard] = useState(null);
  const [progress, setProgress] = useState(null);
  const [loadingTrades, setLoadingTrades] = useState(true);
  const [loadingDashboard, setLoadingDashboard] = useState(true);
  const [loadingProgress, setLoadingProgress] = useState(false);
  const [error, setError] = useState(null);

  const handlePollingChange = (updatedPolling) => {
    setDashboard((prev) =>
      prev ? { ...prev, news_polling: updatedPolling } : prev
    );
  };

  const handleKillSwitchChange = (updated) => {
    setDashboard((prev) => (prev ? { ...prev, kill_switch: updated } : prev));
  };

  const handleOrderSizingChange = (updated) => {
    setDashboard((prev) =>
      prev
        ? {
            ...prev,
            order_sizing: updated,
            risk_limits: prev.risk_limits
              ? {
                  ...prev.risk_limits,
                  coinbase_order_usd: updated.coinbase_order_usd,
                  oanda_order_usd: updated.oanda_order_usd,
                  order_sizing_limits: updated.limits,
                }
              : prev.risk_limits,
          }
        : prev
    );
  };

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [tradesData, dashData] = await Promise.all([fetchTrades(), fetchDashboard()]);
        if (!cancelled) {
          setTrades(tradesData);
          setDashboard(dashData);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err.message);
      } finally {
        if (!cancelled) {
          setLoadingTrades(false);
          setLoadingDashboard(false);
        }
      }
    }

    load();
    const timer = setInterval(load, REFRESH_MS);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (tab !== "progress") return;

    setLoadingProgress(true);
    fetchProgress()
      .then(setProgress)
      .catch(() => setProgress(null))
      .finally(() => setLoadingProgress(false));

    const interval = setInterval(() => {
      fetchProgress().then(setProgress).catch(() => {});
    }, REFRESH_MS);

    return () => clearInterval(interval);
  }, [tab]);

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: "2rem 1.25rem" }}>
      <header style={{ marginBottom: "1.5rem" }}>
        <h1 style={{ margin: 0, fontSize: 24, fontWeight: 500 }}>Trading Dashboard</h1>
        <p style={{ margin: "6px 0 0", fontSize: 14, color: "var(--color-text-secondary)" }}>
          Live broker connections, open positions, watched charts, and closed trade history
        </p>
      </header>

      <nav style={{ display: "flex", gap: 16, marginBottom: "1.5rem" }}>
        {["dashboard", "progress"].map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            style={{
              background: tab === t ? "#3b82f6" : "transparent",
              color: tab === t ? "#fff" : "#9ca3af",
              border: "1px solid",
              borderColor: tab === t ? "#3b82f6" : "#374151",
              borderRadius: 6,
              padding: "6px 18px",
              cursor: "pointer",
              fontWeight: tab === t ? 700 : 400,
              textTransform: "capitalize",
            }}
          >
            {t === "dashboard" ? "Dashboard" : "Progress"}
          </button>
        ))}
      </nav>

      {error && tab === "dashboard" && (
        <div
          style={{
            marginBottom: "1rem",
            padding: "10px 14px",
            borderRadius: "var(--border-radius-md)",
            background: "#FAEEDA",
            color: "#633806",
            fontSize: 13,
          }}
        >
          API unavailable ({error}). Data below may be incomplete or stale.
        </div>
      )}

      {tab === "dashboard" ? (
        <>
          <SystemStatusPanel
            dashboard={dashboard}
            loading={loadingDashboard}
            onPollingChange={handlePollingChange}
            onKillSwitchChange={handleKillSwitchChange}
            onOrderSizingChange={handleOrderSizingChange}
          />

          <h2 style={{ margin: "0 0 1rem", fontSize: 18, fontWeight: 500 }}>Closed trades</h2>
          <TradeResultsDashboard trades={trades} loading={loadingTrades} />
        </>
      ) : (
        <ProgressPanel data={progress} loading={loadingProgress} />
      )}
    </div>
  );
}
