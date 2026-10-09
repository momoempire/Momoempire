// EMP-WL-023 (frontend half): build-time pairing check and the REACT_APP_TURNSTILE_ENABLED switch.
const { turnstileBuildError, assertTurnstileBuildEnv } = require("../../turnstile-env");
const { turnstileConfigured } = require("./TurnstileWidget");

const SAVED = { ...process.env };
afterEach(() => {
  for (const k of ["REACT_APP_TURNSTILE_ENABLED", "REACT_APP_TURNSTILE_SITE_KEY"]) {
    if (SAVED[k] === undefined) delete process.env[k]; else process.env[k] = SAVED[k];
  }
});

test("enabled without a site key fails the build with a clear message", () => {
  const env = { REACT_APP_TURNSTILE_ENABLED: "true", REACT_APP_TURNSTILE_SITE_KEY: "  " };
  expect(turnstileBuildError(env)).toMatch(/REACT_APP_TURNSTILE_SITE_KEY is empty/);
  expect(() => assertTurnstileBuildEnv(env)).toThrow(/EMP-WL-023/);
});

test.each([
  [{}],
  [{ REACT_APP_TURNSTILE_ENABLED: "false" }],
  [{ REACT_APP_TURNSTILE_ENABLED: "TRUE", REACT_APP_TURNSTILE_SITE_KEY: "0x4AAA" }],
  [{ REACT_APP_TURNSTILE_SITE_KEY: "0x4AAA" }],
])("valid config builds: %j", (env) => {
  expect(turnstileBuildError(env)).toBeNull();
  expect(() => assertTurnstileBuildEnv(env)).not.toThrow();
});

test("a typo in the switch fails too", () => {
  expect(turnstileBuildError({ REACT_APP_TURNSTILE_ENABLED: "yes" })).toMatch(/must be "true" or "false"/);
});

test("widget on/off follows the switch; unset keeps the site-key rule", () => {
  process.env.REACT_APP_TURNSTILE_SITE_KEY = "0x4AAA";
  delete process.env.REACT_APP_TURNSTILE_ENABLED;
  expect(turnstileConfigured()).toBe(true);
  process.env.REACT_APP_TURNSTILE_ENABLED = "false";
  expect(turnstileConfigured()).toBe(false);
  process.env.REACT_APP_TURNSTILE_ENABLED = "true";
  expect(turnstileConfigured()).toBe(true);
  delete process.env.REACT_APP_TURNSTILE_SITE_KEY;
  delete process.env.REACT_APP_TURNSTILE_ENABLED;
  expect(turnstileConfigured()).toBe(false);
});

test("craco config runs the check", () => {
  const src = require("fs").readFileSync(require("path").join(__dirname, "../../craco.config.js"), "utf8");
  expect(src).toMatch(/require\("\.\/turnstile-env"\)\.assertTurnstileBuildEnv\(\)/);
});
