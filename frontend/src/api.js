const API_BASE = import.meta.env.VITE_API_BASE || "";

async function getJson(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    throw new Error(`Request failed: ${path} (${res.status})`);
  }
  return res.json();
}

export function fetchHealth() {
  return getJson("/health");
}

export function fetchMeta() {
  return getJson("/api/meta");
}
