/**
 * TradeResultsDashboard.jsx
 * Trading AI Model — Frontend
 *
 * Shows per-trade results: symbol, entry (start limit),
 * exit (stop limit), and P&L (dollars + ticks).
 */

import { useState, useMemo } from "react";

const TICK_VALUE = {
  MES: 1.25,
  ES: 12.5,
  NQ: 5.0,
  MNQ: 0.5,
  RTY: 5.0,
  YM: 5.0,
};

const fmt = (n) =>
  new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(n);

const fmtPrice = (n) => n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const fmtDate = (iso) => {
  const d = new Date(iso);
  return (
    d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) +
    " · " +
    d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false })
  );
};

const pnlColor = (n) => (n > 0 ? "#27500A" : n < 0 ? "#A32D2D" : "#5F5E5A");
const pnlBg = (n) => (n > 0 ? "#EAF3DE" : n < 0 ? "#FCEBEB" : "#F1EFE8");

const exitLabel = {
  target: "Target hit",
  stop: "Stop hit",
  manual: "Manual exit",
  timeout: "Timed out",
};

const exitColor = {
  target: { bg: "#EAF3DE", text: "#27500A" },
  stop: { bg: "#FCEBEB", text: "#A32D2D" },
  manual: { bg: "#FAEEDA", text: "#633806" },
  timeout: { bg: "#F1EFE8", text: "#5F5E5A" },
};

function StatCard({ label, value, sub, color }) {
  return (
    <div
      style={{
        background: "var(--color-background-secondary)",
        borderRadius: "var(--border-radius-md)",
        padding: "14px 16px",
        minWidth: 0,
      }}
    >
      <p style={{ margin: 0, fontSize: 12, color: "var(--color-text-secondary)", letterSpacing: "0.04em" }}>
        {label}
      </p>
      <p
        style={{
          margin: "4px 0 0",
          fontSize: 22,
          fontWeight: 500,
          color: color || "var(--color-text-primary)",
          lineHeight: 1.2,
        }}
      >
        {value}
      </p>
      {sub && <p style={{ margin: "2px 0 0", fontSize: 12, color: "var(--color-text-tertiary)" }}>{sub}</p>}
    </div>
  );
}

function TradeRow({ trade }) {
  const isLong = trade.direction === "long";
  const exitC = exitColor[trade.exit_reason] || exitColor.manual;

  return (
    <div
      style={{
        background: "var(--color-background-primary)",
        border: "0.5px solid var(--color-border-tertiary)",
        borderRadius: "var(--border-radius-lg)",
        padding: "14px 16px",
        display: "grid",
        gridTemplateColumns: "90px 1fr 140px 140px 120px",
        gap: "0 12px",
        alignItems: "center",
      }}
    >
      <div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontWeight: 500, fontSize: 15, color: "var(--color-text-primary)" }}>{trade.symbol}</span>
          <span
            style={{
              fontSize: 11,
              fontWeight: 500,
              padding: "2px 6px",
              borderRadius: 4,
              background: isLong ? "#E6F1FB" : "#FAECE7",
              color: isLong ? "#185FA5" : "#993C1D",
            }}
          >
            {isLong ? "↑ long" : "↓ short"}
          </span>
        </div>
        <p style={{ margin: "3px 0 0", fontSize: 11, color: "var(--color-text-tertiary)" }}>{fmtDate(trade.timestamp)}</p>
      </div>

      <div>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
          <span style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>
            Entry <span style={{ color: "var(--color-text-primary)", fontWeight: 500 }}>{fmtPrice(trade.entry_price)}</span>
          </span>
          <span style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>
            Target <span style={{ color: "var(--color-text-primary)", fontWeight: 500 }}>{fmtPrice(trade.take_profit)}</span>
          </span>
        </div>
        <div style={{ position: "relative", height: 6, borderRadius: 3, background: "var(--color-background-secondary)" }}>
          {(() => {
            const lo = Math.min(trade.stop_loss, trade.take_profit);
            const hi = Math.max(trade.stop_loss, trade.take_profit);
            const range = hi - lo;
            const entryPct = ((trade.entry_price - lo) / range) * 100;
            const exitPct = ((trade.exit_price - lo) / range) * 100;
            const fillL = Math.min(entryPct, exitPct);
            const fillW = Math.abs(exitPct - entryPct);
            const isWin = trade.pnl_dollars > 0;
            return (
              <>
                <div
                  style={{
                    position: "absolute",
                    top: 0,
                    height: "100%",
                    left: `${fillL}%`,
                    width: `${fillW}%`,
                    background: isWin ? "#639922" : trade.pnl_dollars < 0 ? "#E24B4A" : "#888780",
                    borderRadius: 3,
                  }}
                />
                <div
                  style={{
                    position: "absolute",
                    top: -3,
                    width: 2,
                    height: 12,
                    borderRadius: 1,
                    background: "#E24B4A",
                    left: `${((trade.stop_loss - lo) / range) * 100}%`,
                  }}
                  title={`Stop: ${fmtPrice(trade.stop_loss)}`}
                />
                <div
                  style={{
                    position: "absolute",
                    top: -3,
                    width: 2,
                    height: 12,
                    borderRadius: 1,
                    background: "#378ADD",
                    left: `${entryPct}%`,
                  }}
                  title={`Entry: ${fmtPrice(trade.entry_price)}`}
                />
                <div
                  style={{
                    position: "absolute",
                    top: -3,
                    width: 2,
                    height: 12,
                    borderRadius: 1,
                    background: "#444441",
                    left: `${exitPct}%`,
                  }}
                  title={`Exit: ${fmtPrice(trade.exit_price)}`}
                />
              </>
            );
          })()}
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4 }}>
          <span style={{ fontSize: 11, color: "#A32D2D" }}>Stop {fmtPrice(trade.stop_loss)}</span>
          <span style={{ fontSize: 11, color: "var(--color-text-tertiary)" }}>Exit {fmtPrice(trade.exit_price)}</span>
        </div>
      </div>

      <div style={{ textAlign: "center" }}>
        <span
          style={{
            fontSize: 12,
            padding: "4px 10px",
            borderRadius: "var(--border-radius-md)",
            background: exitC.bg,
            color: exitC.text,
            fontWeight: 500,
            whiteSpace: "nowrap",
          }}
        >
          {exitLabel[trade.exit_reason]}
        </span>
        <p style={{ margin: "4px 0 0", fontSize: 11, color: "var(--color-text-tertiary)" }}>Rank {trade.signal_rank}</p>
      </div>

      <div style={{ textAlign: "right" }}>
        <span style={{ fontSize: 13, fontWeight: 500, color: pnlColor(trade.pnl_ticks) }}>
          {trade.pnl_ticks > 0 ? "+" : ""}
          {trade.pnl_ticks} ticks
        </span>
        <p style={{ margin: "2px 0 0", fontSize: 11, color: "var(--color-text-tertiary)" }}>
          {TICK_VALUE[trade.symbol] ? `$${TICK_VALUE[trade.symbol].toFixed(2)}/tick` : ""}
        </p>
      </div>

      <div style={{ textAlign: "right" }}>
        <span
          style={{
            display: "inline-block",
            padding: "4px 10px",
            borderRadius: "var(--border-radius-md)",
            background: pnlBg(trade.pnl_dollars),
            color: pnlColor(trade.pnl_dollars),
            fontSize: 15,
            fontWeight: 500,
          }}
        >
          {trade.pnl_dollars > 0 ? "+" : ""}
          {fmt(trade.pnl_dollars)}
        </span>
      </div>
    </div>
  );
}

export default function TradeResultsDashboard({ trades = [], loading = false }) {
  const [filter, setFilter] = useState("all");
  const [sort, setSort] = useState("newest");

  const symbols = useMemo(() => ["all", ...Array.from(new Set(trades.map((t) => t.symbol))).sort()], [trades]);

  const filtered = useMemo(() => {
    let list = filter === "all" ? trades : trades.filter((t) => t.symbol === filter);
    if (sort === "newest") list = [...list].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
    if (sort === "oldest") list = [...list].sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
    if (sort === "pnl-hi") list = [...list].sort((a, b) => b.pnl_dollars - a.pnl_dollars);
    if (sort === "pnl-lo") list = [...list].sort((a, b) => a.pnl_dollars - b.pnl_dollars);
    if (sort === "rank") list = [...list].sort((a, b) => b.signal_rank - a.signal_rank);
    return list;
  }, [trades, filter, sort]);

  const stats = useMemo(() => {
    const wins = filtered.filter((t) => t.pnl_dollars > 0);
    const losses = filtered.filter((t) => t.pnl_dollars < 0);
    const totalPnl = filtered.reduce((s, t) => s + t.pnl_dollars, 0);
    const winRate = filtered.length ? (wins.length / filtered.length) * 100 : 0;
    const avgWin = wins.length ? wins.reduce((s, t) => s + t.pnl_dollars, 0) / wins.length : 0;
    const avgLoss = losses.length ? losses.reduce((s, t) => s + t.pnl_dollars, 0) / losses.length : 0;
    return { totalPnl, winRate, avgWin, avgLoss, count: filtered.length };
  }, [filtered]);

  if (loading) {
    return (
      <div style={{ padding: "2rem 0", color: "var(--color-text-tertiary)", fontSize: 14, textAlign: "center" }}>
        Loading trade results…
      </div>
    );
  }

  if (!trades.length) {
    return (
      <div style={{ padding: "2rem 0", color: "var(--color-text-tertiary)", fontSize: 14, textAlign: "center" }}>
        No closed trades yet. Results appear here after paper or live positions close.
      </div>
    );
  }

  return (
    <div style={{ padding: "1rem 0" }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
          gap: 12,
          marginBottom: "1.5rem",
        }}
      >
        <StatCard label="Total P&L" value={fmt(stats.totalPnl)} sub={`${stats.count} trades`} color={pnlColor(stats.totalPnl)} />
        <StatCard label="Win rate" value={`${stats.winRate.toFixed(0)}%`} sub={`of ${stats.count} trades`} />
        <StatCard label="Avg win" value={fmt(stats.avgWin)} color="#27500A" />
        <StatCard label="Avg loss" value={fmt(stats.avgLoss)} color="#A32D2D" />
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: "1rem", flexWrap: "wrap" }}>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {symbols.map((s) => (
            <button
              key={s}
              onClick={() => setFilter(s)}
              style={{
                fontSize: 13,
                padding: "4px 12px",
                borderRadius: "var(--border-radius-md)",
                border: filter === s ? "1.5px solid var(--color-border-primary)" : "0.5px solid var(--color-border-tertiary)",
                background: filter === s ? "var(--color-background-secondary)" : "transparent",
                color: "var(--color-text-primary)",
                cursor: "pointer",
                fontWeight: filter === s ? 500 : 400,
              }}
            >
              {s === "all" ? "All symbols" : s}
            </button>
          ))}
        </div>
        <div style={{ marginLeft: "auto" }}>
          <select value={sort} onChange={(e) => setSort(e.target.value)} style={{ fontSize: 13 }}>
            <option value="newest">Newest first</option>
            <option value="oldest">Oldest first</option>
            <option value="pnl-hi">Highest P&L</option>
            <option value="pnl-lo">Lowest P&L</option>
            <option value="rank">Signal rank</option>
          </select>
        </div>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "90px 1fr 140px 140px 120px",
          gap: "0 12px",
          padding: "0 16px 6px",
        }}
      >
        {["Symbol", "Entry → Exit range", "Result", "Ticks", "P&L"].map((h) => (
          <span key={h} style={{ fontSize: 11, color: "var(--color-text-tertiary)", letterSpacing: "0.05em" }}>
            {h.toUpperCase()}
          </span>
        ))}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {filtered.length === 0 ? (
          <div style={{ padding: "2rem", textAlign: "center", color: "var(--color-text-tertiary)", fontSize: 14 }}>
            No trades for this filter.
          </div>
        ) : (
          filtered.map((trade) => <TradeRow key={trade.id} trade={trade} />)
        )}
      </div>

      <div
        style={{
          marginTop: "1rem",
          display: "flex",
          gap: 16,
          fontSize: 11,
          color: "var(--color-text-tertiary)",
          flexWrap: "wrap",
        }}
      >
        {[
          { color: "#378ADD", label: "Entry" },
          { color: "#A32D2D", label: "Stop loss" },
          { color: "#444441", label: "Actual exit" },
          { color: "#639922", label: "Profitable move" },
          { color: "#E24B4A", label: "Loss move" },
        ].map(({ color, label }) => (
          <span key={label} style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <span style={{ width: 10, height: 3, borderRadius: 2, background: color, display: "inline-block" }} />
            {label}
          </span>
        ))}
      </div>
    </div>
  );
}
