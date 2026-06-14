/**
 * SystemStatusPanel — connected platforms, open positions, watched charts.
 */

import KillSwitchToggle from "./KillSwitchToggle.jsx";
import NewsPollingToggle from "./NewsPollingToggle.jsx";

const STATUS_STYLE = {
  connected: { bg: "#EAF3DE", text: "#27500A", label: "Connected" },
  configured: { bg: "#E6F1FB", text: "#185FA5", label: "Configured" },
  disconnected: { bg: "#F1EFE8", text: "#5F5E5A", label: "Offline" },
  disabled: { bg: "#FAECE7", text: "#993C1D", label: "Disabled" },
  live: { bg: "#EAF3DE", text: "#27500A", label: "Live" },
  watching: { bg: "#FAEEDA", text: "#633806", label: "Watching" },
  closed: { bg: "#F1EFE8", text: "#5F5E5A", label: "Session closed" },
  feeding: { bg: "#EAF3DE", text: "#27500A", label: "Feeding" },
  stale: { bg: "#FAEEDA", text: "#633806", label: "Stale" },
  offline: { bg: "#F1EFE8", text: "#5F5E5A", label: "Offline" },
  session_closed: { bg: "#F1EFE8", text: "#5F5E5A", label: "Session closed" },
  no_broker: { bg: "#FAECE7", text: "#993C1D", label: "No broker" },
};

const CATEGORY_LABEL = {
  simulation: "Paper",
  retail: "Retail broker",
  futures: "Futures broker",
  professional: "Professional",
};

const EXECUTION_MODE_LABEL = {
  paper: "Paper",
  coinbase: "Coinbase live",
  oanda: "OANDA live",
  live: "Multi-broker live",
  disabled: "Disabled",
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
  const feedStatus = chart.feed_status || chart.status || "offline";
  const lastBarIso = chart.watcher_last_bar_at || chart.last_bar_at;
  const showNoBroker =
    feedStatus === "feeding" && !chart.execution_ready && chart.pipeline_running;

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "100px 1fr auto auto auto",
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
          Last bar {fmtRelative(lastBarIso)}
          {chart.watcher_bars_processed > 0 ? ` · ${chart.watcher_bars_processed} bars` : ""}
          {chart.session_label ? ` · ${chart.session_label}` : ""}
        </p>
      </div>
      <span style={{ fontSize: 13, fontWeight: 500 }}>{fmtPrice(chart.last_price)}</span>
      <StatusBadge status={feedStatus} />
      {showNoBroker && <StatusBadge status="no_broker" />}
    </div>
  );
}

function WatchedChartsPanel({ watchedCharts, grouped, watcherStatus }) {
  const groups = grouped && Object.keys(grouped).length > 0 ? grouped : null;
  const ws = watcherStatus || {};
  const summaryParts = [
    ws.online ? "Watcher online" : "Watcher offline",
    ws.mode ? `mode ${ws.mode}` : null,
    ws.feeding != null ? `${ws.feeding} feeding` : null,
    ws.stale != null ? `${ws.stale} stale` : null,
    ws.session_closed != null ? `${ws.session_closed} session closed` : null,
    ws.execution_ready_count != null ? `${ws.execution_ready_count} exec ready` : null,
  ].filter(Boolean);

  const renderCharts = (charts) =>
    charts.map((chart) => (
      <ChartRow key={`${chart.symbol}-${chart.timeframe}`} chart={chart} />
    ));

  if (!watchedCharts.length && !groups) {
    return <p style={{ margin: 0, fontSize: 13, color: "var(--color-text-tertiary)" }}>No charts configured.</p>;
  }

  return (
    <>
      {summaryParts.length > 0 && (
        <p style={{ margin: "0 0 12px", fontSize: 12, color: "var(--color-text-secondary)" }}>
          {summaryParts.join(" · ")}
        </p>
      )}
      {groups ? (
        Object.entries(groups).map(([className, charts]) => (
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
            {renderCharts(charts)}
          </div>
        ))
      ) : (
        renderCharts(watchedCharts)
      )}
    </>
  );
}

export default function SystemStatusPanel({ dashboard, loading = false, onPollingChange, onKillSwitchChange }) {
  if (loading) {
    return (
      <div style={{ padding: "1.5rem 0", color: "var(--color-text-tertiary)", fontSize: 14 }}>
        Loading system status…
      </div>
    );
  }

  if (!dashboard) {
    return (
      <div
        style={{
          padding: "2rem",
          textAlign: "center",
          color: "var(--color-text-tertiary)",
          fontSize: 14,
          background: "var(--color-background-primary)",
          border: "0.5px solid var(--color-border-tertiary)",
          borderRadius: "var(--border-radius-lg)",
          marginBottom: "2rem",
        }}
      >
        Dashboard data unavailable. Start the API server and refresh.
      </div>
    );
  }

  const data = dashboard;
  const platforms = data.platforms || [];
  const openPositions = data.open_positions || [];
  const watchedCharts = data.watched_charts || [];
  const summary = data.platform_summary || {};

  const connectedBrokers = platforms.filter((p) => p.status === "connected");
  const otherPlatforms = platforms.filter((p) => p.status !== "connected");
  const killSwitchActive = data.kill_switch?.enabled === true;

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
          {killSwitchActive && (
            <span
              style={{
                fontSize: 11,
                fontWeight: 600,
                letterSpacing: "0.04em",
                color: "#A32D2D",
                background: "#FAECE7",
                padding: "4px 8px",
                borderRadius: "var(--border-radius-sm)",
              }}
            >
              KILL SWITCH ACTIVE
            </span>
          )}
          <span style={{ fontSize: 13, color: "var(--color-text-secondary)" }}>
            Mode:{" "}
            <strong
              style={{
                color: killSwitchActive ? "#993C1D" : "var(--color-text-primary)",
              }}
            >
              {killSwitchActive
                ? `${EXECUTION_MODE_LABEL[data.execution_mode] || data.execution_mode || "paper"} — halted`
                : EXECUTION_MODE_LABEL[data.execution_mode] || data.execution_mode || "paper"}
            </strong>
          </span>
          {(data.oanda_live_ready || data.coinbase_live_ready) && (
            <>
              <span style={{ color: "var(--color-border-tertiary)" }}>|</span>
              <span style={{ fontSize: 12, color: "#27500A" }}>
                {[
                  data.coinbase_live_ready && "Coinbase ready",
                  data.oanda_live_ready && "OANDA ready",
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
            </>
          )}
          <span style={{ color: "var(--color-border-tertiary)" }}>|</span>
          <span style={{ fontSize: 13, color: "var(--color-text-secondary)" }}>
            Active:{" "}
            <strong style={{ color: "var(--color-text-primary)" }}>
              {platforms.find((p) => p.id === data.active_broker)?.name || data.active_broker || "—"}
            </strong>
          </span>
          {data.risk_limits?.account_cap_usd != null && (
            <>
              <span style={{ color: "var(--color-border-tertiary)" }}>|</span>
              <span style={{ fontSize: 13, color: "var(--color-text-secondary)" }}>
                Risk cap:{" "}
                <strong>${data.risk_limits.account_cap_usd}</strong>
                {" · "}
                daily stop ${data.risk_limits.max_daily_loss_usd}
              </span>
            </>
          )}
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
          {data.watcher_status && (
            <>
              <span style={{ color: "var(--color-border-tertiary)" }}>|</span>
              <span
                style={{
                  fontSize: 13,
                  color: data.watcher_status.online ? "#27500A" : "var(--color-text-secondary)",
                }}
              >
                Watcher{" "}
                <strong>{data.watcher_status.online ? "online" : "offline"}</strong>
                {data.watcher_status.feeding != null && (
                  <>
                    {" "}
                    · <strong>{data.watcher_status.feeding}</strong> feeding
                  </>
                )}
              </span>
            </>
          )}
          {data.source === "fallback" && (
            <>
              <span style={{ color: "var(--color-border-tertiary)" }}>|</span>
              <span style={{ fontSize: 12, color: "#633806" }}>Partial data (API fallback)</span>
            </>
          )}
        </div>
        <div style={{ display: "flex", gap: 16, marginLeft: "auto", alignItems: "center" }}>
          <KillSwitchToggle killSwitch={data.kill_switch} onChange={onKillSwitchChange} />
          <NewsPollingToggle polling={data.news_polling} onChange={onPollingChange} />
        </div>
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
              watcherStatus={data.watcher_status}
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
