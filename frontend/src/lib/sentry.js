/* Frontend error monitoring (Sentry) — no-op unless REACT_APP_SENTRY_DSN is set.
 *
 * Loads the official Sentry browser bundle from their CDN at runtime so no
 * npm dependency (and no yarn.lock churn) is required. If @sentry/react is
 * ever added to package.json, replace this loader with a static import.
 * CSP note: frontend/public/_headers already allows script-src
 * https://browser.sentry-cdn.com.
 *
 * PII hygiene: default SDK scrubbing + sendDefaultPii off; errors only.
 */
const CDN_URL = "https://browser.sentry-cdn.com/8.46.0/bundle.min.js";

let started = false;

export function initSentry() {
  if (started) return;
  const dsn = process.env.REACT_APP_SENTRY_DSN;
  if (!dsn) return; // no-op without a DSN
  started = true;
  if (window.Sentry) {
    window.Sentry.init({ dsn, sendDefaultPii: false });
    return;
  }
  const s = document.createElement("script");
  s.src = CDN_URL;
  s.crossOrigin = "anonymous";
  s.onload = () => {
    try {
      window.Sentry.init({ dsn, sendDefaultPii: false });
    } catch (e) {
      /* monitoring must never break the app */
    }
  };
  s.onerror = () => {
    /* offline / blocked CDN: stay silent */
  };
  document.head.appendChild(s);
}

export function captureException(err) {
  try {
    if (window.Sentry) window.Sentry.captureException(err);
  } catch (e) {
    /* ignore */
  }
}
