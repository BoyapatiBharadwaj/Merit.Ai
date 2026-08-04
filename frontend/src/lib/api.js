/**
 * Fetch wrapper for the FastAPI backend. In dev, vite.config.js proxies
 * /api/* to http://localhost:8000, so relative paths work with no CORS
 * setup needed. Behavior mirrors the legacy frontend/js/api.js: attach the
 * stored bearer token automatically, clear the session on 401, and surface
 * FastAPI/Pydantic error shapes as a single readable message.
 */
import { getToken, clearSession } from "./auth.js";

const BASE_URL = "/api/v1";

export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

function messageFromDetail(detail, fallback) {
  if (!detail) return fallback;
  if (typeof detail === "string") return detail;
  // FastAPI/Pydantic validation errors: [{ loc, msg, type }, ...]
  if (Array.isArray(detail) && detail[0]?.msg) return detail[0].msg;
  // Structured error payloads, e.g. { code, message, ... } -- see
  // attempt_service._expired_auto_submit_error.
  if (typeof detail === "object" && typeof detail.message === "string") return detail.message;
  return fallback;
}

async function request(path, { method = "GET", body, auth = true } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (auth) {
    const token = getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }

  let res;
  try {
    res = await fetch(`${BASE_URL}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError("Can't reach the server. Is the backend running on :8000?", 0, null);
  }

  if (auth && res.status === 401) {
    clearSession();
    throw new ApiError("Session expired. Please log in again.", 401, null);
  }

  let data = null;
  const text = await res.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      // Non-JSON response body; leave data null.
    }
  }

  if (!res.ok) {
    throw new ApiError(messageFromDetail(data?.detail, `Request failed (${res.status})`), res.status, data?.detail);
  }
  return data;
}

/** Downloads a binary response (PDF report/certificate) via a temporary object URL. */
async function downloadFile(path, filename) {
  const headers = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  let res;
  try {
    res = await fetch(`${BASE_URL}${path}`, { method: "GET", headers });
  } catch {
    throw new ApiError("Can't reach the server. Is the backend running on :8000?", 0, null);
  }

  if (res.status === 401) {
    clearSession();
    throw new ApiError("Session expired. Please log in again.", 401, null);
  }
  if (!res.ok) {
    let detail = null;
    try {
      detail = (await res.json())?.detail;
    } catch {
      // non-JSON error body
    }
    throw new ApiError(messageFromDetail(detail, `Download failed (${res.status})`), res.status, detail);
  }

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/**
 * Fetches a binary response (e.g. a photo) as a Blob for inline display,
 * rather than triggering a save-to-disk prompt like downloadFile does.
 * A 404 resolves to null -- "nothing on file yet" is a normal, expected
 * state for these endpoints (a student who hasn't verified an ID yet, a
 * face photo that was never registered), not an error worth throwing.
 */
async function fetchBlob(path) {
  const headers = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  let res;
  try {
    res = await fetch(`${BASE_URL}${path}`, { method: "GET", headers });
  } catch {
    throw new ApiError("Can't reach the server. Is the backend running on :8000?", 0, null);
  }

  if (res.status === 401) {
    clearSession();
    throw new ApiError("Session expired. Please log in again.", 401, null);
  }
  if (res.status === 404) return null;
  if (!res.ok) {
    let detail = null;
    try {
      detail = (await res.json())?.detail;
    } catch {
      // non-JSON error body
    }
    throw new ApiError(messageFromDetail(detail, `Request failed (${res.status})`), res.status, detail);
  }
  return res.blob();
}

export const Api = {
  login: (email, password) => request("/auth/login", { method: "POST", body: { email, password }, auth: false }),
  registerStudent: (payload) => request("/auth/register/student", { method: "POST", body: payload, auth: false }),

  get: (path) => request(path),
  post: (path, body) => request(path, { method: "POST", body }),
  put: (path, body) => request(path, { method: "PUT", body }),
  patch: (path, body) => request(path, { method: "PATCH", body }),
  del: (path) => request(path, { method: "DELETE" }),
  downloadFile,
  fetchBlob,
};
