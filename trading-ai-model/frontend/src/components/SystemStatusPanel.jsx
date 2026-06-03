/**
 * SystemStatusPanel — connected platforms, open positions, watched charts.
 */

import NewsPollingToggle from "./NewsPollingToggle.jsx";

const STATUS_STYLE = {
  connected: { bg: "#EAF3DE", text: "#27500A", label: "Connected" },
  configured: { bg: "#E6F1FB", text: "#185FA5", label: "Configured" },
  disconnected: { bg: "#F1EFE8", text: "#5F5E5A", label: "Offline" },
  disabled: { bg: "#FAECE7", text: "#993C1D", label: "Disabled" },
  live: { bg: "#EAF3DE", text: "#27500A", label: "Live" },
  watching: { bg: "#FAEEDA", text: "#633806", label: "Watching" },
  closed: { bg: "#F1EFE8", text: "#5F5E5A", label: "Session closed" },
};

const CATEGORY_LABEL = {
  simulation: "Paper",
  retail: "Retail broker",
  futures: "Futures broker",
  professional: "Professional",
};

function fmtPrice(n) {
  if (n == null) return "—";
  return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtPnl(n) {
  const sign = n > 0 ? "+" : "";
  return `${sign}$${Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtRelative(iso) {
  if (!iso) return "No bar yet";
  const d = new Date(iso);
  const diff = Date.now() - d.getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "Just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function StatusBadge({ status }) {
  const s = STATUS_STYLE[status] || STATUS_STYLE.disconnected;
  return (
    <span
      style={{
        fontSize: 11,
        fontWeight: 500,
        padding: "2px 8px",
        borderRadius: 4,
        background: s.bg,
        color: s.text,
        whiteSpace: "nowrap",
      }}
    >
      {s.label}
    </span>
  );
}

function Section({ title, count, children }) {
  return (
    <section
      style={{
        background: "var(--color-background-primary)",
        border: "0.5px solid var(--color-border-tertiary)",
        borderRadius: "var(--border-radius-lg)",
        padding: "16px 18px",
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 14 }}>
        <h2 style={{ margin: 0, fontSize: 15, fontWeight: 500 }}>{title}</h2>
        {count != null && (
          <span style={{ fontSize: 12, color: "var(--color-text-tertiary)" }}>({count})</span>
        )}
      </div>
      {children}
    </section>
  );
}

function PlatformRow({ platform }) {
  const assets = (platform.asset_classes || []).join(", ");
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr auto",
        gap: 8,
        alignItems: "start",
        padding: "10px 0",
        borderBottom: "0.5px solid var(--color-border-tertiary)",
      }}
    >
      <div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontSize: 13, fontWeight: 500 }}>{platform.name}</span>
          <span style={{ fontSize: 10, color: "var(--color-text-tertiary)", letterSpacing: "0.04em" }}>
            {CATEGORY_LABEL[platform.category] || platform.category}
          </span>
        </div>
        <p style={{ margin: "3px 0 0", fontSize: 12, color: "var(--color-text-secondary)" }}>{platform.detail}</p>
        {assets && (
          <p style={{ margin: "2px 0 0", fontSize: 11, color: "var(--color-text-tertiary)" }}>{assets}</p>
        )}
      </div>
      <StatusBadge status={platform.status} />
    </div>
  );
}

function OpenPositionRow({ pos }) {
  const isLong = pos.direction === "long";
  const pnlColor = pos.unrealized_pnl_dollars > 0 ? "#27500A" : pos.unrealized_pnl_dollars < 0 ? "#A32D2D" : "#5F5E5A";
  const pnlBg = pos.unrealized_pnl_dollars > 0 ? "#EAF3DE" : pos.unrealized_pnl_dollars < 0 ? "#FCEBEB" : "#F1EFE8";

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "90px 1fr 100px 100px",
        gap: 12,
        alignItems: "center",
        padding: "12px 0",
        borderBottom: "0.5px solid var(--color-border-tertiary)",
      }}
    >
      <div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontWeight: 500, fontSize: 14 }}>{pos.symbol}</span>
          <span
            style={{
              fontSize: 10,
              padding: "2px 6px",
              borderRadius: 4,
              background: isLong ? "#E6F1FB" : "#FAECE7",
              color: isLong ? "#185FA5" : "#993C1D",
            }}
          >
            {isLong ? "↑ long" : "↓ short"}
          </span>
        </div>
        <p style={{ margin: "2px 0 0", fontSize: 11, color: "var(--color-text-tertiary)" }}>
          {pos.platform_name || pos.broker}
        </p>
      </div>

      <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>
        <span>
          Entry <strong style={{ color: "var(--color-text-primary)" }}>{fmtPrice(pos.entry_price)}</strong>
        </span>
        <span style={{ margin: "0 8px", color: "var(--color-border-tertiary)" }}>·</span>
        <span>
          Mark <strong style={{ color: "var(--color-text-primary)" }}>{fmtPrice(pos.current_price)}</strong>
        </span>
        <span style={{ margin: "0 8px", color: "var(--color-border-tertiary)" }}>·</span>
        <span>Qty {pos.quantity}</span>
        <div style={{ marginTop: 4, fontSize: 11 }}>
          Stop {fmtPrice(pos.stop_loss)} → Target {fmtPrice(pos.take_profit)}
        </div>
      </div>

      <div style={{ textAlign: "right", fontSize: 12, color: pnlColor }}>
        {pos.unrealized_pnl_ticks > 0 ? "+" : ""}
        {pos.unrealized_pnl_ticks} ticks
      </div>

      <div style={{ textAlign: "right" }}>
        <span
          style={{
            display: "inline-block",
            padding: "4px 10px",
            borderRadius: "var(--border-radius-md)",
            background: pnlBg,
            color: pnlColor,
            fontSize: 13,
            fontWeight: 500,
          }}
        >
          {fmtPnl(pos.unrealized_pnl_dollars)}
        </span>
      </div>
    </div>
  );
}

function ChartRow({ chart }) {
  const displayName = chart.display_name || chart.label || chart.symbol;
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "100px 1fr auto auto",
        gap: 12,
        alignItems: "center",
        padding: "10px 0",
        borderBottom: "0.5px solid var(--color-border-tertiary)",
      }}
    >
      <div>
        <span style={{ fontWeight: 500, fontSize: 14 }}>{chart.symbol}</span>
        <span style={{ marginLeft: 6, fontSize: 12, color: "var(--color-text-tertiary)" }}>{chart.timeframe}</span>
      </div>
      <div>
        <p style={{ margin: 0, fontSize: 12, color: "var(--color-text-secondary)" }}>{displayName}</p>
        <p style={{ margin: "2px 0 0", fontSize: 11, color: "var(--color-text-tertiary)" }}>
          Last bar {fmtRelative(chart.last_bar_at)}
          {chart.session_label ? ` · ${chart.session_label}` : ""}
        </p>
      </div>
      <span style={{ fontSize: 13, fontWeight: 500 }}>{fmtPrice(chart.last_price)}</span>
      <StatusBadge status={chart.status} />
    </div>
  );
}

function WatchedChartsPanel({ watchedCharts, grouped }) {
  const groups = grouped && Object.keys(grouped).length > 0 ? grouped : null;

  if (!watchedCharts.length && !groups) {
    return <p style={{ margin: 0, fontSize: 13, color: "var(--color-text-tertiary)" }}>No charts configured.</p>;
  }

  if (groups) {
    return (
      <>
        {Object.entries(groups).map(([className, charts]) => (
          <div key={className} style={{ marginBottom: 12 }}>
            <p
              style={{
                margin: "0 0 6px",
                fontSize: 11,
                color: "var(--color-text-tertiary)",
                letterSpacing: "0.05em",
              }}
            >
              {className.toUpperCase()} ({charts.length})
            </p>
            {charts.map((chart) => (
              <ChartRow key={`${chart.symbol}-${chart.timeframe}`} chart={chart} />
            ))}
          </div>
        ))}
      </>
    );
  }

  return watchedCharts.map((chart) => (
    <ChartRow key={`${chart.symbol}-${chart.timeframe}`} chart={chart} />
  ));
}

const WATCHER_SYMBOLS_MOCK = [
  "MES", "ES", "MNQ", "NQ", "CL", "GC", "ZB", "RTY",
  "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD",
  "BTCUSD", "ETHUSD", "SOLUSD", "BNBUSD", "XRPUSD",
  "TSLA", "NVDA", "AAPL", "MSFT", "AMZN",
];

const MOCK_WATCHED_CHARTS = WATCHER_SYMBOLS_MOCK.map((symbol) => ({
  symbol,
  timeframe: "5m",
  display_name: symbol,
  label: symbol,
  asset_class: "unknown",
  status: "watching",
  last_bar_at: null,
  last_price: null,
}));

export const MOCK_DASHBOARD = {
  execution_mode: "paper",
  active_broker: "paper",
  platform_summary: { connected: 1, configured: 0, total: 9 },
  platforms: [
    {
      id: "paper",
      name: "Paper Trading",
      category: "simulation",
      asset_classes: ["futures", "stocks"],
      status: "connected",
      detail: "Simulated fills — no capital at risk",
    },
    {
      id: "robinhood",
      name: "Robinhood",
      category: "retail",
      asset_classes: ["stocks", "options", "crypto"],
      status: "disconnected",
      detail: "Set ROBINHOOD_ACCESS_TOKEN in .env to connect",
    },
    {
      id: "tradovate",
      name: "Tradovate",
      category: "futures",
      asset_classes: ["futures"],
      status: "disconnected",
      detail: "Set TRADOVATE_API_KEY and TRADOVATE_USERNAME in .env",
    },
    {
      id: "ibkr",
      name: "Interactive Brokers",
      category: "professional",
      asset_classes: ["futures", "stocks", "options", "forex"],
      status: "disconnected",
      detail: "Set IBKR_ACCOUNT_ID and run TWS Gateway",
    },
  ],
  open_positions: [
    {
      id: "pos-demo-1",
      symbol: "MES",
      direction: "long",
      entry_price: 5420.25,
      current_price: 5426.5,
      stop_loss: 5410,
      take_profit: 5442,
      quantity: 2,
      unrealized_pnl_dollars: 156.25,
      unrealized_pnl_ticks: 25,
      platform_id: "tradovate",
      platform_name: "Tradovate",
      status: "open",
    },
  ],
  watched_charts: MOCK_WATCHED_CHARTS,
  watched_chart_count: MOCK_WATCHED_CHARTS.length,
};

export default function SystemStatusPanel({ dashboard, loading = false, onPollingChange }) {
  if (loading) {
    return (
      <div style={{ padding: "1.5rem 0", color: "var(--color-text-tertiary)", fontSize: 14 }}>
        Loading system status…
      </div>
    );
  }

  const data = dashboard || MOCK_DASHBOARD;
  const platforms = data.platforms || [];
  const openPositions = data.open_positions || [];
  const watchedCharts = data.watched_charts || [];
  const summary = data.platform_summary || {};

  const connectedBrokers = platforms.filter((p) => p.status === "connected");
  const otherPlatforms = platforms.filter((p) => p.status !== "connected");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, marginBottom: "2rem" }}>
      {/* Summary bar */}
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 12,
          alignItems: "center",
          padding: "12px 16px",
          background: "var(--color-background-primary)",
          border: "0.5px solid var(--color-border-tertiary)",
          borderRadius: "var(--border-radius-lg)",
        }}
      >
        <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center", flex: 1 }}>
          <span style={{ fontSize: 13, color: "var(--color-text-secondary)" }}>
            Active:{" "}
            <strong style={{ color: "var(--color-text-primary)" }}>
              {platforms.find((p) => p.id === data.active_broker)?.name || data.active_broker || "—"}
            </strong>
          </span>
          <span style={{ color: "var(--color-border-tertiary)" }}>|</span>
          <span style={{ fontSize: 13, color: "var(--color-text-secondary)" }}>
            <strong style={{ color: "#27500A" }}>{summary.connected ?? 0}</strong> connected
          </span>
          <span style={{ fontSize: 13, color: "var(--color-text-secondary)" }}>
            <strong style={{ color: "#185FA5" }}>{summary.configured ?? 0}</strong> configured
          </span>
          <span style={{ fontSize: 13, color: "var(--color-text-secondary)" }}>
            <strong>{openPositions.length}</strong> open trades
          </span>
          <span style={{ fontSize: 13, color: "var(--color-text-secondary)" }}>
            <strong>{watchedCharts.length}</strong> charts watched
          </span>
        </div>
        <NewsPollingToggle polling={data.news_polling} onChange={onPollingChange} />
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
          gap: 16,
        }}
      >
        {/* Open positions — full width priority on mobile via order */}
        <div style={{ gridColumn: openPositions.length ? "1 / -1" : undefined }}>
          <Section title="Open positions" count={openPositions.length}>
            {openPositions.length === 0 ? (
              <p style={{ margin: 0, fontSize: 13, color: "var(--color-text-tertiary)" }}>No live trades open.</p>
            ) : (
              <>
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "90px 1fr 100px 100px",
                    gap: 12,
                    paddingBottom: 6,
                  }}
                >
                  {["Symbol", "Levels", "Ticks", "Unrealized"].map((h) => (
                    <span key={h} style={{ fontSize: 10, color: "var(--color-text-tertiary)", letterSpacing: "0.05em" }}>
                      {h.toUpperCase()}
                    </span>
                  ))}
                </div>
                {openPositions.map((pos) => (
                  <OpenPositionRow key={pos.id} pos={pos} />
                ))}
              </>
            )}
          </Section>
        </div>

        {/* Chart watching system — WATCHER_SYMBOLS via API */}
        <div style={{ gridColumn: "1 / -1" }}>
          <Section title="Chart watching system" count={watchedCharts.length}>
            <WatchedChartsPanel
              watchedCharts={watchedCharts}
              grouped={data.watched_charts_grouped}
            />
          </Section>
        </div>

        {/* Broker platforms */}
        <Section title="Trading platforms" count={platforms.length}>
          {connectedBrokers.length > 0 && (
            <>
              <p style={{ margin: "0 0 8px", fontSize: 11, color: "var(--color-text-tertiary)", letterSpacing: "0.05em" }}>
                CONNECTED
              </p>
              {connectedBrokers.map((p) => (
                <PlatformRow key={p.id} platform={p} />
              ))}
            </>
          )}
          {otherPlatforms.length > 0 && (
            <>
              <p
                style={{
                  margin: connectedBrokers.length ? "12px 0 8px" : "0 0 8px",
                  fontSize: 11,
                  color: "var(--color-text-tertiary)",
                  letterSpacing: "0.05em",
                }}
              >
                AVAILABLE TO CONNECT
              </p>
              {otherPlatforms.map((p) => (
                <PlatformRow key={p.id} platform={p} />
              ))}
            </>
          )}
        </Section>
      </div>
    </div>
  );
}
