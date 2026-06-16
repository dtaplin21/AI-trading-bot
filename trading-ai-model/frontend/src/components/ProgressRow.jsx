import React, { useState } from "react";

const CHECK_LABELS = {
  watchlist_active: "Watchlist active",
  actionable_exits: "Actionable exits (TP/SL/EV/R:R)",
  touches_ok: "Touch count",
  hold_ok: "Hold rate",
  ev_ok: "Expected value",
  at_price: "Price in range",
};

export default function ProgressRow({ entry }) {
  const [open, setOpen] = useState(false);

  const bucketColor =
    {
      at_line: "#22c55e",
      qualified: "#3b82f6",
      building: "#f59e0b",
    }[entry.bucket] ?? "#6b7280";

  const pct = entry.progress_pct ?? 0;

  return (
    <div
      style={{
        borderBottom: "1px solid #1f2937",
        padding: "10px 0",
        cursor: "pointer",
      }}
      onClick={() => setOpen((o) => !o)}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <span style={{ width: 80, fontWeight: 600 }}>{entry.symbol}</span>
        <span style={{ width: 100, fontFamily: "monospace" }}>
          {entry.level_price?.toFixed(5)}
        </span>
        <span style={{ width: 70, color: "#9ca3af" }}>
          {entry.distance_pct != null ? `${entry.distance_pct.toFixed(2)}%` : "—"}
        </span>
        <span style={{ width: 80 }}>
          {entry.touch_count ?? 0}/{entry.touch_target} {entry.checks?.touches_ok ? "✅" : ""}
        </span>
        <span style={{ width: 70 }}>
          {entry.hold_rate != null ? `${(entry.hold_rate * 100).toFixed(0)}%` : "—"}{" "}
          {entry.checks?.hold_ok ? "✅" : ""}
        </span>
        <span style={{ width: 70 }}>
          {entry.expected_value_pct != null
            ? `${entry.expected_value_pct > 0 ? "+" : ""}${entry.expected_value_pct.toFixed(2)}%`
            : "—"}{" "}
          {entry.checks?.ev_ok ? "✅" : ""}
        </span>
        <span style={{ width: 50 }}>
          {entry.optimal_rr != null ? entry.optimal_rr.toFixed(1) : "—"}
        </span>

        <div style={{ flex: 1, background: "#1f2937", borderRadius: 4, height: 6 }}>
          <div
            style={{
              width: `${pct}%`,
              background: bucketColor,
              height: 6,
              borderRadius: 4,
              transition: "width 0.3s",
            }}
          />
        </div>
        <span style={{ width: 40, color: bucketColor, fontWeight: 600 }}>
          {pct}%
        </span>
      </div>

      {open && (
        <div
          style={{
            marginTop: 10,
            marginLeft: 80,
            background: "#111827",
            borderRadius: 6,
            padding: "10px 14px",
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <div style={{ fontWeight: 600, marginBottom: 8 }}>
            {entry.symbol} @ {entry.level_price?.toFixed(5)} —{" "}
            <span style={{ color: bucketColor }}>
              {entry.bucket === "at_line"
                ? "At the line"
                : entry.bucket === "qualified"
                  ? "Qualified"
                  : "Building"}
            </span>
          </div>

          {Object.entries(CHECK_LABELS).map(([key, label]) => {
            const passed = entry.checks?.[key];
            return (
              <div key={key} style={{ marginBottom: 4, fontSize: 13 }}>
                <span style={{ marginRight: 8 }}>{passed ? "✅" : "⏳"}</span>
                <span style={{ color: passed ? "#e5e7eb" : "#9ca3af" }}>{label}</span>
              </div>
            );
          })}

          {entry.blockers?.length > 0 && (
            <div style={{ marginTop: 8, color: "#f59e0b", fontSize: 12 }}>
              {entry.blockers.map((b, i) => (
                <div key={i}>⚠ {b}</div>
              ))}
            </div>
          )}

          <div
            style={{
              marginTop: 8,
              fontSize: 12,
              color: "#6b7280",
            }}
          >
            Fast lane readiness: {pct}%
          </div>
        </div>
      )}
    </div>
  );
}
