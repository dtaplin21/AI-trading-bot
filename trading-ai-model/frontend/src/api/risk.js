const API_BASE = import.meta.env.VITE_API_URL ?? "/api";

export async function setKillSwitch(enabled) {
  const res = await fetch(`${API_BASE}/risk/kill-switch`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!res.ok) {
    throw new Error(`Failed to update kill switch (${res.status})`);
  }
  return res.json();
}

export async function getOrderSizing() {
  const res = await fetch(`${API_BASE}/risk/order-sizing`);
  if (!res.ok) throw new Error(`Failed to load order sizing (${res.status})`);
  return res.json();
}

export async function setOrderSizing({ coinbase_order_usd, oanda_order_usd }) {
  const res = await fetch(`${API_BASE}/risk/order-sizing`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ coinbase_order_usd, oanda_order_usd }),
  });
  if (!res.ok) throw new Error(`Failed to update order sizing (${res.status})`);
  return res.json();
}
