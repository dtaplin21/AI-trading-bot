const API_BASE = import.meta.env.VITE_API_URL ?? "/api";

export async function fetchNewsPolling() {
  const res = await fetch(`${API_BASE}/news/polling`);
  if (!res.ok) {
    throw new Error(`Failed to load news polling status (${res.status})`);
  }
  return res.json();
}

export async function setNewsPolling(enabled) {
  const res = await fetch(`${API_BASE}/news/polling`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!res.ok) {
    throw new Error(`Failed to update news polling (${res.status})`);
  }
  return res.json();
}
