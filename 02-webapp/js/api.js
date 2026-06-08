/* ============================================================================ */
/* api.js                                                                       */
/* HTTP client for the RAG demo backend API.                                   */
/* All requests include the Cognito JWT Bearer token from localStorage.        */
/* ============================================================================ */

import { CONFIG } from "./config.js";
import { getAccessToken } from "./auth.js";

const BASE = CONFIG.API_BASE_URL;

/* ---------------------------------------------------------------------------- */
/* Internal fetch wrapper                                                        */
/* ---------------------------------------------------------------------------- */

async function apiFetch(method, path, body) {
  const token = getAccessToken();
  const opts = {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  };

  const res = await fetch(`${BASE}${path}`, opts);
  const data = await res.json();

  if (!res.ok) {
    const err = new Error(data?.error || `HTTP ${res.status}`);
    err.status = res.status;
    err.data   = data;
    throw err;
  }

  return data;
}

/* ---------------------------------------------------------------------------- */
/* User management                                                               */
/* ---------------------------------------------------------------------------- */

export function registerUser()      { return apiFetch("POST", "/register"); }
export function getUsage()          { return apiFetch("GET",  "/usage"); }

/* ---------------------------------------------------------------------------- */
/* Conversations                                                                 */
/* ---------------------------------------------------------------------------- */

export function listConversations()      { return apiFetch("GET",    "/conversations"); }
export function createConversation()     { return apiFetch("POST",   "/conversations"); }
export function deleteConversation(id)   { return apiFetch("DELETE", `/conversations/${id}`); }

/* ---------------------------------------------------------------------------- */
/* Queries                                                                       */
/* ---------------------------------------------------------------------------- */

export function listQueries(convId) {
  return apiFetch("GET", `/conversations/${convId}/queries`);
}

export function submitQuery(convId, question) {
  return apiFetch("POST", `/conversations/${convId}/queries`, { question });
}

export function getQuery(convId, queryId) {
  return apiFetch("GET", `/conversations/${convId}/queries/${queryId}`);
}
