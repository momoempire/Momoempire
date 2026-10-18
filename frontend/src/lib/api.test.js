// F-17 pinning tests: src/lib/api.js must never silently produce the
// literal API base "undefined/api". A production build without
// REACT_APP_BACKEND_URL fails loudly (and `yarn build` refuses to build
// in that state via scripts/check-build-env.js); the localhost fallback
// is for development/test only and cannot be inherited by production.

const ORIGINAL_ENV = { ...process.env };

function loadApi(env) {
  jest.resetModules();
  process.env = { ...ORIGINAL_ENV };
  for (const [k, v] of Object.entries(env)) {
    if (v === undefined) delete process.env[k];
    else process.env[k] = v;
  }
  return require("./api");
}

afterEach(() => {
  process.env = { ...ORIGINAL_ENV };
  jest.resetModules();
});

test("uses REACT_APP_BACKEND_URL when set", () => {
  const { API_BASE } = loadApi({
    NODE_ENV: "production",
    REACT_APP_BACKEND_URL: "https://api.example.com",
  });
  expect(API_BASE).toBe("https://api.example.com/api");
});

test("production build without REACT_APP_BACKEND_URL throws instead of emitting undefined/api", () => {
  expect(() =>
    loadApi({ NODE_ENV: "production", REACT_APP_BACKEND_URL: undefined })
  ).toThrow(/REACT_APP_BACKEND_URL was not set/);
});

test("non-production without the variable falls back to the documented dev backend", () => {
  const { API_BASE } = loadApi({
    NODE_ENV: "development",
    REACT_APP_BACKEND_URL: undefined,
  });
  expect(API_BASE).toBe("http://localhost:8001/api");
});
