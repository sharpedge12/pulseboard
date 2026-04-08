/**
 * @file api.js — HTTP client utilities for communicating with the PulseBoard
 *       backend API.
 *
 * **Interview topics: API abstraction layer, environment variables, auth
 * header injection, error handling, asset URL construction.**
 *
 * ### Why wrap `fetch` in a helper?
 * Raw `fetch` calls scattered across components lead to duplicated error
 * handling, inconsistent headers, and hard-to-change base URLs.  This module
 * provides a thin abstraction (`apiRequest`) that:
 * 1. Prepends the correct base URL (configurable via env vars).
 * 2. Parses JSON responses automatically.
 * 3. Throws a meaningful error with the server's `detail` message on failure.
 * 4. Handles 204 No Content responses gracefully (returns `null`).
 *
 * ### Environment variables (Vite):
 * Vite exposes env vars prefixed with `VITE_` via `import.meta.env`.
 * - `VITE_API_BASE_URL` — e.g. `http://api.example.com` (defaults to localhost:8000)
 * - `VITE_WS_BASE_URL`  — WebSocket base URL (defaults to ws://localhost:8000)
 *
 * These are **baked in at build time** — changing them requires a rebuild.
 *
 * @module lib/api
 */

/**
 * The HTTP origin of the API gateway (e.g. `http://localhost:8000`).
 * Used as the base for both REST endpoints and static asset URLs.
 *
 * @constant {string}
 */
const _API_HOST = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

/**
 * Full base URL for REST API calls, including the `/api/v1` version prefix.
 * All `apiRequest` calls are relative to this path.
 *
 * @constant {string}
 * @example
 * // API_BASE_URL = 'http://localhost:8000/api/v1'
 * // apiRequest('/threads') → fetch('http://localhost:8000/api/v1/threads')
 */
const API_BASE_URL = _API_HOST + '/api/v1';

/**
 * WebSocket base URL for real-time features (notifications, chat, live
 * thread updates).  Uses `ws://` in dev, `wss://` in production.
 *
 * @constant {string}
 */
const WS_BASE_URL = (import.meta.env.VITE_WS_BASE_URL || 'ws://localhost:8000');

/**
 * Builds a full URL to a backend-served static resource (e.g. uploaded
 * avatars, file attachments).
 *
 * **Interview note — why not just use relative paths?**
 * In development, the Vite dev server runs on port 5173 while the API
 * gateway runs on port 8000.  A relative path like `/uploads/avatar.png`
 * would resolve to `localhost:5173/uploads/avatar.png` (the Vite server),
 * which doesn't serve uploaded files.  `assetUrl` prepends the correct
 * API host so the browser fetches from the right server.
 *
 * In production with a reverse proxy, you might not need this — but it's
 * a safe, portable approach.
 *
 * @param {string} path - Path starting with '/', e.g. '/uploads/abc.png'
 * @returns {string} Full URL, e.g. 'http://localhost:8000/uploads/abc.png'
 */
function assetUrl(path) {
  return `${_API_HOST}${path}`;
}

/**
 * Builds a headers object for API requests.
 *
 * **Interview note — conditional spread for auth headers:**
 * The expression `...(token ? { Authorization: ... } : {})` is a common
 * pattern for conditionally including object properties.  If `token` is
 * falsy, we spread an empty object `{}` (no-op).  If truthy, we include
 * the `Authorization: Bearer <token>` header required by the backend's
 * JWT middleware.
 *
 * @param {string|null}  token        - JWT access token, or null for public endpoints
 * @param {object}       extraHeaders - Additional headers to merge in
 * @returns {object} Headers object ready for `fetch()`
 */
function getHeaders(token, extraHeaders = {}) {
  return {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...extraHeaders,
  };
}

/**
 * Makes an API request to the backend and returns parsed JSON.
 *
 * This is the central fetch wrapper used by every page and component.
 * It handles:
 * - Prepending `API_BASE_URL` to the path
 * - Parsing the JSON response body
 * - Extracting the server's `detail` error message on failure
 * - Returning `null` for 204 No Content responses (e.g. after DELETE)
 *
 * **Interview note — error handling strategy:**
 * On non-2xx responses, we attempt to parse the error body as JSON to
 * extract the backend's `detail` field (FastAPI's standard error format).
 * The `.catch(() => ({}))` fallback handles cases where the error body
 * isn't valid JSON (e.g. a 502 from a proxy).  The thrown `Error` always
 * has a human-readable message that callers can display in the UI.
 *
 * @param {string} path     - API path relative to `/api/v1`, e.g. '/threads'
 * @param {RequestInit} [options] - Standard `fetch` options (method, headers, body, etc.)
 * @returns {Promise<object|null>} Parsed JSON response, or null for 204
 * @throws {Error} With the server's detail message on non-OK responses
 *
 * @example
 * // GET request (public endpoint)
 * const threads = await apiRequest('/threads?page=1');
 *
 * @example
 * // POST request (authenticated)
 * const newThread = await apiRequest('/threads', {
 *   method: 'POST',
 *   headers: getHeaders(accessToken),
 *   body: JSON.stringify({ title: 'Hello', body: 'World', category_id: 1 }),
 * });
 */
export async function apiRequest(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, options);

  // Non-2xx status → extract error detail and throw
  if (!response.ok) {
    const errorBody = await response.json().catch(() => ({}));
    throw new Error(errorBody.detail || 'Request failed');
  }

  // 204 No Content — server succeeded but there's no body to parse.
  // Common after DELETE operations or actions that don't return data.
  if (response.status === 204) {
    return null;
  }

  return response.json();
}

export { API_BASE_URL, WS_BASE_URL, getHeaders, assetUrl };
