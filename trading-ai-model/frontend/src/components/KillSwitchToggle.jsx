/**
 * Kill switch — halt or resume trading across API, watcher, and MCP.
 */

import { useState } from "react";
import { setKillSwitch } from "../api/risk.js";

export default function KillSwitchToggle({ killSwitch, onChange }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const enabled = killSwitch?.enabled ?? false;

  async function handleToggle() {
    const next = !enabled;
    if (next && !window.confirm("Halt all trading immediately?")) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const updated = await setKillSwitch(next);
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
      }}
    >
      <div style={{ textAlign: "right" }}>
        <div style={{ fontSize: 12, fontWeight: 500, color: "var(--color-text-primary)" }}>
          Kill switch
        </div>
        <div style={{ fontSize: 11, color: "var(--color-text-tertiary)" }}>
          {enabled ? "Trading halted — no new orders" : "Trading active"}
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
        title={enabled ? "Resume trading" : "Halt all trading immediately"}
        style={{
          position: "relative",
          width: 44,
          height: 24,
          borderRadius: 12,
          border: "none",
          cursor: busy ? "wait" : "pointer",
          background: enabled ? "#A32D2D" : "#C4C3BC",
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
