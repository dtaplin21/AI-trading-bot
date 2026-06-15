/**
 * Per-broker order size (USD) — Coinbase crypto + OANDA forex.
 */

import { useEffect, useState } from "react";
import { setOrderSizing } from "../api/risk.js";

const PRESETS = [5, 10, 25, 50];

const presetStyle = (active) => ({
  fontSize: 12,
  padding: "3px 8px",
  borderRadius: "var(--border-radius-md)",
  border: active ? "1.5px solid var(--color-border-primary)" : "0.5px solid var(--color-border-tertiary)",
  background: active ? "var(--color-background-secondary)" : "transparent",
  color: "var(--color-text-primary)",
  cursor: "pointer",
  fontWeight: active ? 500 : 400,
});

function BrokerRow({ label, value, min, max, busy, onChange, onSave }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 6 }}>
      <span
        style={{
          fontSize: 11,
          color: "var(--color-text-secondary)",
          minWidth: 58,
          fontWeight: 500,
        }}
      >
        {label}
      </span>
      {PRESETS.filter((p) => p >= min && p <= max).map((preset) => (
        <button
          key={preset}
          type="button"
          disabled={busy}
          style={presetStyle(value === preset)}
          onClick={() => {
            onChange(preset);
            onSave(preset);
          }}
        >
          ${preset}
        </button>
      ))}
      <input
        type="number"
        min={min}
        max={max}
        step={1}
        value={value}
        disabled={busy}
        onChange={(e) => onChange(Number(e.target.value))}
        style={{
          width: 56,
          fontSize: 12,
          padding: "3px 6px",
          borderRadius: "var(--border-radius-md)",
          border: "0.5px solid var(--color-border-tertiary)",
        }}
      />
      <button
        type="button"
        disabled={busy}
        onClick={() => onSave()}
        style={{
          fontSize: 12,
          padding: "3px 10px",
          borderRadius: "var(--border-radius-md)",
          border: "0.5px solid var(--color-border-tertiary)",
          background: "var(--color-background-secondary)",
          cursor: busy ? "wait" : "pointer",
        }}
      >
        Save
      </button>
    </div>
  );
}

export default function OrderSizingControl({ orderSizing, onChange }) {
  const [coinbase, setCoinbase] = useState(5);
  const [oanda, setOanda] = useState(5);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const limits = orderSizing?.limits ?? {};
  const min = limits.min_usd ?? 5;
  const max = limits.max_usd ?? 50;

  useEffect(() => {
    if (orderSizing) {
      setCoinbase(orderSizing.coinbase_order_usd ?? 5);
      setOanda(orderSizing.oanda_order_usd ?? 5);
    }
  }, [orderSizing]);

  async function save(nextCb = coinbase, nextOa = oanda) {
    setBusy(true);
    setError(null);
    try {
      const updated = await setOrderSizing({
        coinbase_order_usd: nextCb,
        oanda_order_usd: nextOa,
      });
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
        flexDirection: "column",
        gap: 8,
        minWidth: 0,
        maxWidth: 360,
      }}
    >
      <div style={{ fontSize: 12, fontWeight: 500, color: "var(--color-text-primary)" }}>
        Order size (USD)
      </div>
      <BrokerRow
        label="Coinbase"
        value={coinbase}
        min={min}
        max={max}
        busy={busy}
        onChange={setCoinbase}
        onSave={(next) => save(next ?? coinbase, oanda)}
      />
      <BrokerRow
        label="OANDA"
        value={oanda}
        min={min}
        max={max}
        busy={busy}
        onChange={setOanda}
        onSave={(next) => save(coinbase, next ?? oanda)}
      />
      <div style={{ fontSize: 11, color: "var(--color-text-tertiary)" }}>
        Next trade: ${coinbase} crypto · ${oanda} forex (min ${min}, max ${max})
      </div>
      {error && <div style={{ fontSize: 11, color: "#A32D2D" }}>{error}</div>}
    </div>
  );
}
