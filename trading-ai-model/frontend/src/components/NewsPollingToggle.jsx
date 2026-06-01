/**
 * Dev toggle — enable/disable automatic news polling (RSS, Polygon, FRED, LLM).
 */

import { useState } from "react";
import { setNewsPolling } from "../api/news.js";

export default function NewsPollingToggle({ polling, onChange }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const enabled = polling?.enabled ?? false;
  const running = polling?.running ?? false;
  const intervalHrs = polling?.polling_interval_seconds
    ? Math.round(polling.polling_interval_seconds / 3600)
    : null;

  async function handleToggle() {
    const next = !enabled;
    setBusy(true);
    setError(null);
    try {
      const updated = await setNewsPolling(next);
      onChange?.(updated);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: 10,
        marginLeft: "auto",
      }}
    >
      <div style={{ textAlign: "right" }}>
        <div style={{ fontSize: 12, fontWeight: 500, color: "var(--color-text-primary)" }}>
          News polling
        </div>
        <div style={{ fontSize: 11, color: "var(--color-text-tertiary)" }}>
          {enabled
            ? running
              ? `Auto ingest on · ~${intervalHrs ?? "?"}h baseline`
              : "Starting…"
            : "Off — saves API / LLM usage"}
        </div>
        {error && (
          <div style={{ fontSize: 11, color: "#A32D2D", marginTop: 2 }}>{error}</div>
        )}
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={enabled}
        disabled={busy}
        onClick={handleToggle}
        title={enabled ? "Turn off automatic news polling" : "Turn on automatic news polling"}
        style={{
          position: "relative",
          width: 44,
          height: 24,
          borderRadius: 12,
          border: "none",
          cursor: busy ? "wait" : "pointer",
          background: enabled ? "#27500A" : "#C4C3BC",
          transition: "background 0.2s ease",
          flexShrink: 0,
          opacity: busy ? 0.7 : 1,
        }}
      >
        <span
          style={{
            position: "absolute",
            top: 3,
            left: enabled ? 23 : 3,
            width: 18,
            height: 18,
            borderRadius: "50%",
            background: "#fff",
            transition: "left 0.2s ease",
            boxShadow: "0 1px 2px rgba(0,0,0,0.15)",
          }}
        />
      </button>
    </div>
  );
}
