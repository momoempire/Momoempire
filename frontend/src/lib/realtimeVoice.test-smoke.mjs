import assert from "node:assert/strict";

function assertEphemeralSecret(value) {
  if (!value || typeof value !== "string") throw new Error("Missing ephemeral client secret");
  if (value.startsWith("sk_")) throw new Error("Refusing to use a non-ephemeral API key in the browser");
  if (!value.startsWith("ek_")) throw new Error("Unexpected client secret format");
  return value;
}
function formatVoiceCountdown(totalSeconds, elapsedMs) {
  return Math.max(0, Math.ceil(totalSeconds - elapsedMs / 1000));
}

assert.equal(assertEphemeralSecret("ek_test_abc"), "ek_test_abc");
assert.throws(() => assertEphemeralSecret("sk_live_secret"), /non-ephemeral/);
assert.equal(formatVoiceCountdown(60, 0), 60);
assert.equal(formatVoiceCountdown(60, 59500), 1);
assert.equal(formatVoiceCountdown(60, 70000), 0);
console.log("realtimeVoice helper smoke OK");
