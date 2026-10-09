import { useEffect, useRef } from "react";

// Cloudflare Turnstile widget. The backend only checks the token when TURNSTILE_ENABLED is on.
// Frontend switch (EMP-WL-023): REACT_APP_TURNSTILE_ENABLED=true|false. Unset keeps the old rule
// (on when REACT_APP_TURNSTILE_SITE_KEY is set). "true" without a site key fails the build
// (frontend/turnstile-env.js). Read at render time (CRA still inlines it at build time) so tests
// can set it.
const siteKey = () => {
  const enabled = (process.env.REACT_APP_TURNSTILE_ENABLED || "").trim().toLowerCase();
  if (enabled === "false") return "";
  return (process.env.REACT_APP_TURNSTILE_SITE_KEY || "").trim();
};
const SCRIPT_SRC = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

function loadScript() {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  return new Promise((resolve, reject) => {
    let s = document.querySelector(`script[src="${SCRIPT_SRC}"]`);
    if (!s) {
      s = document.createElement("script");
      s.src = SCRIPT_SRC;
      s.async = true;
      s.defer = true;
      document.head.appendChild(s);
    }
    s.addEventListener("load", () => resolve(window.turnstile));
    s.addEventListener("error", reject);
  });
}

export const turnstileConfigured = () => !!siteKey();

export default function TurnstileWidget({ onToken }) {
  const ref = useRef(null);
  const SITE_KEY = siteKey();
  useEffect(() => {
    if (!SITE_KEY) return undefined;
    let widgetId;
    let cancelled = false;
    loadScript()
      .then((ts) => {
        if (cancelled || !ts || !ref.current) return;
        widgetId = ts.render(ref.current, {
          sitekey: SITE_KEY,
          callback: (token) => onToken(token),
          "expired-callback": () => onToken(""),
          "error-callback": () => onToken(""),
        });
      })
      .catch(() => onToken(""));
    return () => {
      cancelled = true;
      if (widgetId !== undefined && window.turnstile) window.turnstile.remove(widgetId);
    };
  }, [onToken, SITE_KEY]);
  if (!SITE_KEY) return null;
  return <div ref={ref} data-testid="waitlist-turnstile" />;
}
