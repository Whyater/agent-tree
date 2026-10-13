// Thin fetch wrapper for the local JSON API (docs/API.md).

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

export async function api(path, params) {
  let url = path;
  if (params) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== "" && v !== false) q.set(k, v === true ? "1" : String(v));
    }
    const qs = q.toString();
    if (qs) url += "?" + qs;
  }
  let res;
  try {
    res = await fetch(url, { headers: { Accept: "application/json" } });
  } catch {
    throw new ApiError("Could not reach the Agent Tree server. Is `att serve` still running?", 0);
  }
  let body = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (!res.ok) {
    throw new ApiError((body && body.error) || `Server returned HTTP ${res.status}`, res.status);
  }
  return body;
}

export async function postScan(full = false) {
  let res;
  try {
    res = await fetch("/api/scan", {
      method: "POST",
      headers: { "X-Agent-Tree": "1", "Content-Type": "application/json" },
      body: JSON.stringify(full ? { full: true } : {}),
    });
  } catch {
    throw new ApiError("Could not reach the Agent Tree server.", 0);
  }
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  if (!res.ok || (body && body.ok === false)) {
    throw new ApiError((body && body.error) || `Scan failed (HTTP ${res.status})`, res.status);
  }
  return body;
}

export function threadPath(id) {
  return "/api/threads/" + encodeURIComponent(id);
}

export function exportPath(id, ext) {
  return "/api/export/thread/" + encodeURIComponent(id) + "." + ext;
}
