// EMP-WL-002 / WL-027: true in the waitlist-only build (REACT_APP_WAITLIST_ONLY=true at build time).
// CRA inlines the value at build time, so webpack drops the hidden branches from that build.
// It's a function (not a constant) so Jest tests can switch it per test.
export const isWaitlistOnly = () => process.env.REACT_APP_WAITLIST_ONLY === "true";
