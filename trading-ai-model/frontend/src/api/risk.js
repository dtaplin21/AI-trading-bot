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
