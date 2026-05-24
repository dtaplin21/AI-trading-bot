import { useEffect, useState } from "react";
import TradeResultsDashboard from "./components/TradeResultsDashboard.jsx";
import { fetchTrades } from "./api/trades.js";

export default function App() {
  const [trades, setTrades] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;

    fetchTrades()
      .then((data) => {
        if (!cancelled) setTrades(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: "2rem 1.25rem" }}>
      <header style={{ marginBottom: "1.5rem" }}>
        <h1 style={{ margin: 0, fontSize: 24, fontWeight: 500 }}>Trade Results</h1>
        <p style={{ margin: "6px 0 0", fontSize: 14, color: "var(--color-text-secondary)" }}>
          Per-trade P&amp;L from paper trading — entry, exit, ticks, and SignalRank
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
          API unavailable ({error}). Showing mock data.
        </div>
      )}

      <TradeResultsDashboard trades={trades.length ? trades : undefined} loading={loading} />
    </div>
  );
}
