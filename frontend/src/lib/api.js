import axios from "axios";

// REACT_APP_BACKEND_URL is inlined at build time. A production build
// without it used to produce the literal API base "undefined/api" and the
// failure only surfaced invisibly in the browser (audit finding F-17).
// `yarn build` now refuses to build in that state
// (scripts/check-build-env.js); this throw is the last line of defense
// for builds that bypass that path — a production bundle with no backend
// configured fails loudly instead of silently calling "undefined/api".
const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
if (!BACKEND_URL && process.env.NODE_ENV === "production") {
  throw new Error(
    "REACT_APP_BACKEND_URL was not set when this build was produced, so the app has no API backend. " +
      "Rebuild with REACT_APP_BACKEND_URL=<backend origin> (see README)."
  );
}

// Development/test fallback only: production bundles take the throw
// above, so this default can never be inherited by a production build.
export const API_BASE = `${BACKEND_URL || "http://localhost:8001"}/api`;

export const api = axios.create({
  baseURL: API_BASE,
  withCredentials: true,
  headers: { "Content-Type": "application/json" },
});

export function formatApiError(detail) {
  if (detail == null) return "Something went wrong. Please try again.";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail
      .map((e) => (e && typeof e.msg === "string" ? e.msg : JSON.stringify(e)))
      .filter(Boolean)
      .join(" ");
  if (detail && typeof detail.msg === "string") return detail.msg;
  return String(detail);
}

export function errMessage(e) {
  return formatApiError(e?.response?.data?.detail) || e?.message || "Unknown error";
}
