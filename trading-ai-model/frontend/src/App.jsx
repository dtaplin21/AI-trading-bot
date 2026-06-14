import { useEffect, useState } from "react";
import TradeResultsDashboard from "./components/TradeResultsDashboard.jsx";
import SystemStatusPanel from "./components/SystemStatusPanel.jsx";
import { fetchTrades } from "./api/trades.js";
import { fetchDashboard } from "./api/dashboard.js";

const REFRESH_MS = 30_000;

export default function App() {
  const [trades, setTrades] = useState([]);
  const [dashboard, setDashboard] = useState(null);
  const [loadingTrades, setLoadingTrades] = useState(true);
  const [loadingDashboard, setLoadingDashboard] = useState(true);
  const [error, setError] = useState(null);

  const handlePollingChange = (updatedPolling) => {
    setDashboard((prev) =>
      prev ? { ...prev, news_polling: updatedPolling } : prev
    );
  };

  const handleKillSwitchChange = (updated) => {
    setDashboard((prev) => (prev ? { ...prev, kill_switch: updated } : prev));
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

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: "2rem 1.25rem" }}>
      <header style={{ marginBottom: "1.5rem" }}>
        <h1 style={{ margin: 0, fontSize: 24, fontWeight: 500 }}>Trading Dashboard</h1>
        <p style={{ margin: "6px 0 0", fontSize: 14, color: "var(--color-text-secondary)" }}>
          Live broker connections, open positions, watched charts, and closed trade history
        </p>
      </header>

      {error && (
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

      <SystemStatusPanel
        dashboard={dashboard}
        loading={loadingDashboard}
        onPollingChange={handlePollingChange}
        onKillSwitchChange={handleKillSwitchChange}
      />

      <h2 style={{ margin: "0 0 1rem", fontSize: 18, fontWeight: 500 }}>Closed trades</h2>
      <TradeResultsDashboard trades={trades} loading={loadingTrades} />
    </div>
  );
}
