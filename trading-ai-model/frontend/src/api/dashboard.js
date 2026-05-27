const API_BASE = import.meta.env.VITE_API_URL ?? "/api";

export async function fetchDashboard() {
  const res = await fetch(`${API_BASE}/dashboard`);
  if (!res.ok) {
    throw new Error(`Failed to load dashboard (${res.status})`);
  }
  return res.json();
}
