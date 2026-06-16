const API_BASE = import.meta.env.VITE_API_URL ?? "/api";

export async function fetchProgress() {
  const res = await fetch(`${API_BASE}/progress`);
  if (!res.ok) throw new Error(`Failed to load progress (${res.status})`);
  return res.json();
}
