const API_BASE = import.meta.env.VITE_API_URL ?? "/api";

export async function fetchTrades() {
  const res = await fetch(`${API_BASE}/trades`);
  if (!res.ok) {
    throw new Error(`Failed to load trades (${res.status})`);
  }
  const data = await res.json();
  return data.trades ?? [];
}
