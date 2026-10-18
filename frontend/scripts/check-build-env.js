#!/usr/bin/env node
// Fails (exit 1) when a production build would be produced without a
// backend origin. `craco build` is always a production build, and
// REACT_APP_BACKEND_URL is inlined into the bundle at build time: when it
// was unset, src/lib/api.js emitted the literal base "undefined/api" and
// the build still reported success (audit finding F-17). This check runs
// as the first step of `yarn build` so a misconfigured build fails loudly
// here instead of shipping a frontend that can never reach its API.
// Usage: node scripts/check-build-env.js   (wired into `yarn build`)

function backendUrlProblems(env) {
  const raw = env.REACT_APP_BACKEND_URL;
  const value = raw == null ? "" : String(raw).trim();
  if (!value || value.toLowerCase() === "undefined" || value.toLowerCase() === "null") {
    return [
      "REACT_APP_BACKEND_URL is not set. A production build without it produces " +
        'an API base of "undefined/api" and every backend call fails. ' +
        "Set REACT_APP_BACKEND_URL to the backend origin (e.g. https://api.example.com) " +
        "and rebuild. For local development use `yarn start` (see README).",
    ];
  }
  if (!/^https?:\/\//i.test(value)) {
    return [
      `REACT_APP_BACKEND_URL="${value}" does not look like an http(s) origin. ` +
        "Set it to the backend origin (e.g. https://api.example.com) and rebuild.",
    ];
  }
  return [];
}

module.exports = { backendUrlProblems };

if (require.main === module) {
  const problems = backendUrlProblems(process.env);
  if (problems.length) {
    for (const p of problems) console.error(`check-build-env: ERROR: ${p}`);
    process.exit(1);
  }
  console.log("check-build-env: OK (REACT_APP_BACKEND_URL is set)");
}
