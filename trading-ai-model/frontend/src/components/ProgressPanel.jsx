import React from "react";
import ProgressRow from "./ProgressRow.jsx";

const SECTION = {
  at_line: { label: "At the line (would trade now)", color: "#22c55e" },
  qualified: { label: "Qualified (DB ready, waiting for price)", color: "#3b82f6" },
  building: { label: "Building toward fast lane", color: "#f59e0b" },
};

const COL_HEADERS = ["Symbol", "Level", "Dist", "Touches", "Hold", "EV%", "R:R", "Progress"];

export default function ProgressPanel({ data, loading }) {
  if (loading) {
    return (
      <div style={{ color: "#9ca3af", padding: 32, textAlign: "center" }}>
        Loading progress...
      </div>
    );
  }

  if (!data) {
    return (
      <div style={{ color: "#ef4444", padding: 32, textAlign: "center" }}>
        Failed to load progress data.
      </div>
    );
  }

  const { summary, recent_touches, thresholds, updated_at } = data;

  const closest = summary?.closest;
  const updatedAgo = updated_at
    ? Math.round((Date.now() - new Date(updated_at).getTime()) / 1000)
    : null;

  return (
    <div style={{ color: "var(--color-text-primary, #1a1a18)", fontFamily: "sans-serif" }}>
      <div
        style={{
          background: "#111827",
          color: "#e5e7eb",
          borderRadius: 8,
          padding: "14px 18px",
          marginBottom: 20,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <div>
          <span style={{ fontWeight: 700, fontSize: 16, color: "#fff" }}>Fast lane progress</span>
          {updatedAgo != null && (
            <span style={{ color: "#6b7280", fontSize: 12, marginLeft: 12 }}>
              Updated {updatedAgo}s ago
            </span>
          )}
          <div style={{ marginTop: 6, fontSize: 13 }}>
            <span style={{ color: "#22c55e", marginRight: 16 }}>
              At the line: {summary?.at_line ?? 0}
            </span>
            <span style={{ color: "#3b82f6", marginRight: 16 }}>
              Qualified: {summary?.qualified ?? 0}
            </span>
            <span style={{ color: "#f59e0b" }}>Building: {summary?.building ?? 0}</span>
          </div>
        </div>

        {closest && (
          <div style={{ textAlign: "right", fontSize: 13 }}>
            <span style={{ color: "#9ca3af" }}>Closest: </span>
            <span style={{ fontWeight: 600 }}>
              {closest.symbol} {closest.level_price?.toFixed(5)}
            </span>
            <span style={{ color: "#9ca3af" }}>
              {" "}
              — {closest.distance_pct?.toFixed(2)}% away
            </span>
          </div>
        )}
      </div>

      {thresholds && (
        <div style={{ fontSize: 11, color: "var(--color-text-secondary, #5f5e5a)", marginBottom: 16 }}>
          Thresholds: {thresholds.min_touches} touches · {(thresholds.min_hold_rate * 100).toFixed(0)}% hold ·
          EV ≥ {thresholds.min_ev_pct}% · within {thresholds.tolerance_pct}%
        </div>
      )}

      <div
        style={{
          display: "flex",
          gap: 12,
          padding: "6px 0",
          borderBottom: "1px solid var(--color-border-tertiary, #e3e1da)",
          fontSize: 11,
          color: "var(--color-text-secondary, #5f5e5a)",
          fontWeight: 600,
        }}
      >
        {[
          { w: 80, l: "Symbol" },
          { w: 100, l: "Level" },
          { w: 70, l: "Dist" },
          { w: 80, l: "Touches" },
          { w: 70, l: "Hold" },
          { w: 70, l: "EV%" },
          { w: 50, l: "R:R" },
          { w: null, l: "Progress" },
        ].map(({ w, l }) => (
          <span key={l} style={w ? { width: w } : { flex: 1 }}>
            {l}
          </span>
        ))}
      </div>

      {["at_line", "qualified", "building"].map((bucket) => {
        const rows = data[bucket] ?? [];
        const { label, color } = SECTION[bucket];
        return (
          <div key={bucket} style={{ marginTop: 20 }}>
            <div
              style={{
                color,
                fontWeight: 700,
                fontSize: 13,
                borderBottom: `1px solid ${color}33`,
                paddingBottom: 4,
                marginBottom: 8,
              }}
            >
              {label} ({rows.length})
            </div>
            {rows.length === 0 ? (
              <div style={{ color: "var(--color-text-secondary, #5f5e5a)", fontSize: 13, padding: "8px 0" }}>
                None
              </div>
            ) : (
              rows.map((entry, i) => (
                <ProgressRow key={`${entry.symbol}-${entry.level_price}-${i}`} entry={entry} />
              ))
            )}
          </div>
        );
      })}

      {recent_touches?.length > 0 && (
        <div style={{ marginTop: 28 }}>
          <div
            style={{
              fontWeight: 700,
              fontSize: 13,
              color: "var(--color-text-primary, #1a1a18)",
              borderBottom: "1px solid var(--color-border-tertiary, #e3e1da)",
              paddingBottom: 4,
              marginBottom: 8,
            }}
          >
            Recent touches (live intel)
          </div>
          {recent_touches.map((t, i) => (
            <div
              key={i}
              style={{
                display: "flex",
                gap: 16,
                padding: "6px 0",
                fontSize: 13,
                color: "var(--color-text-primary, #1a1a18)",
                borderBottom: "1px solid var(--color-border-tertiary, #e3e1da)",
              }}
            >
              <span style={{ width: 80, fontWeight: 600 }}>{t.symbol}</span>
              <span style={{ width: 100, fontFamily: "monospace" }}>
                @ {t.price_at_touch?.toFixed(5) ?? t.level_price}
              </span>
              <span style={{ color: "var(--color-text-secondary, #5f5e5a)" }}>
                {t.touched_at ? new Date(t.touched_at).toLocaleTimeString() : "—"}
              </span>
              <span
                style={{
                  color:
                    t.outcome === "hold"
                      ? "#22c55e"
                      : t.outcome === "break"
                        ? "#ef4444"
                        : "#9ca3af",
                }}
              >
                {t.outcome ?? "pending"}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
