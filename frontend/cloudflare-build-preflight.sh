#!/usr/bin/env bash
# Manual Cloudflare-2 frontend build preflight; does not deploy or use paid services.
set -euo pipefail
cd "$(dirname "$0")/.."
echo "Node: $(node --version)"
echo "Yarn: $(yarn --version)"
if ! yarn --version | grep -q '^1\.22\.'; then
  echo 'ERROR: Yarn 1.22.x is required (see packageManager in package.json).' >&2
  exit 2
fi
if [ ! -f yarn.lock ]; then
  echo 'No yarn.lock found. Generating a candidate lockfile with yarn install; review dependency changes before committing.'
  yarn install --non-interactive
  echo 'Candidate frontend/yarn.lock created. Review it; do not deploy yet.'
else
  yarn install --frozen-lockfile --non-interactive
fi
CI=true yarn build
test -f build/index.html || { echo 'ERROR: build/index.html was not produced' >&2; exit 3; }
echo 'Local frontend build PASSED; this does NOT verify Cloudflare preview, API, Stripe or production readiness.'
