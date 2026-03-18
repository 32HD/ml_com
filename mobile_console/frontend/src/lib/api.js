export function apiUrl(path) {
  const raw = String(path || "").trim();
  if (!raw) return `${window.location.protocol}//${window.location.host}/`;
  if (/^https?:\/\//i.test(raw)) return raw;
  const base = `${window.location.protocol}//${window.location.host}`;
  return new URL(raw, base).toString();
}

export async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  const isFormData = typeof FormData !== "undefined" && options.body instanceof FormData;

  if (!isFormData && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(apiUrl(path), {
    ...options,
    headers,
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`${response.status}: ${text}`);
  }

  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return response.json();
  }
  return response.text();
}
