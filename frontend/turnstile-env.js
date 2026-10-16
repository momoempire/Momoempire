// EMP-WL-023 (frontend half): Turnstile must be configured the same way on both sides.
// Backend TURNSTILE_ENABLED=true rejects every signup without a token, and the page only sends a
// token when the widget renders, which needs REACT_APP_TURNSTILE_SITE_KEY at BUILD time.
// So: REACT_APP_TURNSTILE_ENABLED=true without a site key fails the build (and `craco start`)
// with a clear message instead of shipping a form where every signup fails.
// Pairing (docs/deploy/client-ip-and-proxies.md, "Turnstile"):
//   backend  TURNSTILE_ENABLED=true + TURNSTILE_SECRET_KEY
//   frontend REACT_APP_TURNSTILE_ENABLED=true + REACT_APP_TURNSTILE_SITE_KEY (rebuild after changing)
function turnstileBuildError(env) {
  const enabled = String(env.REACT_APP_TURNSTILE_ENABLED || "").trim().toLowerCase();
  const key = String(env.REACT_APP_TURNSTILE_SITE_KEY || "").trim();
  if (enabled && enabled !== "true" && enabled !== "false") {
    return `REACT_APP_TURNSTILE_ENABLED must be "true" or "false" (got "${env.REACT_APP_TURNSTILE_ENABLED}").`;
  }
  if (enabled === "true" && !key) {
    return "REACT_APP_TURNSTILE_ENABLED=true but REACT_APP_TURNSTILE_SITE_KEY is empty. The waitlist form " +
      "would send no Turnstile token and the backend (TURNSTILE_ENABLED=true) would reject every signup. " +
      "Set REACT_APP_TURNSTILE_SITE_KEY, or set REACT_APP_TURNSTILE_ENABLED=false here AND turn " +
      "TURNSTILE_ENABLED off on the backend.";
  }
  return null;
}

function assertTurnstileBuildEnv(env = process.env) {
  const err = turnstileBuildError(env);
  if (err) throw new Error(`[EMP-WL-023] ${err}`);
}

module.exports = { turnstileBuildError, assertTurnstileBuildEnv };
