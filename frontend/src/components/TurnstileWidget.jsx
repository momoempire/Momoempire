import { useEffect, useRef } from "react";

// Cloudflare Turnstile widget. Renders nothing unless REACT_APP_TURNSTILE_SITE_KEY is set at build
// time. The backend only checks the token when TURNSTILE_ENABLED is on (off by default).
const SITE_KEY = process.env.REACT_APP_TURNSTILE_SITE_KEY || "";
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

export const turnstileConfigured = !!SITE_KEY;

export default function TurnstileWidget({ onToken }) {
  const ref = useRef(null);
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
  }, [onToken]);
  if (!SITE_KEY) return null;
  return <div ref={ref} data-testid="waitlist-turnstile" />;
}
