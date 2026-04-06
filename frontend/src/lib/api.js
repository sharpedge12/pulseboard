const _API_HOST = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
const API_BASE_URL = _API_HOST + '/api/v1';
const WS_BASE_URL = (import.meta.env.VITE_WS_BASE_URL || 'ws://localhost:8000');

/**
 * Build a full URL to a backend-served resource (e.g. avatar, upload).
 * @param {string} path - path starting with '/', e.g. '/uploads/abc.png'
 * @returns {string}
 */
function assetUrl(path) {
  return `${_API_HOST}${path}`;
}

function getHeaders(token, extraHeaders = {}) {
  return {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...extraHeaders,
  };
}

export async function apiRequest(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, options);
  if (!response.ok) {
    const errorBody = await response.json().catch(() => ({}));
    throw new Error(errorBody.detail || 'Request failed');
  }

  if (response.status === 204) {
    return null;
  }

  return response.json();
}

export { API_BASE_URL, WS_BASE_URL, getHeaders, assetUrl };
