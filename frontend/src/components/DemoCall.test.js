// QA (Oct 13): the public demo hides internal reasons and recovers from an expired session.
const { TextEncoder, TextDecoder } = require("util");
Object.assign(globalThis, { TextEncoder, TextDecoder });
jest.mock("@/lib/utils", () => jest.requireActual("../lib/utils"), { virtual: true });
jest.mock("@/components/ui/button", () => jest.requireActual("./ui/button"), { virtual: true });
jest.mock("@/components/ui/input", () => jest.requireActual("./ui/input"), { virtual: true });
jest.mock("@/components/ui/badge", () => jest.requireActual("./ui/badge"), { virtual: true });
jest.mock("@/lib/realtimeVoice", () => ({ connectRealtimeVoice: jest.fn(), formatVoiceCountdown: () => 0 }), { virtual: true });
jest.mock("@/lib/api", () => ({ api: { post: jest.fn() }, errMessage: (e) => String(e) }), { virtual: true });
jest.mock("sonner", () => ({ toast: { error: jest.fn(), message: jest.fn() } }));

const React = require("react");
require("../i18n");
const { api } = require("@/lib/api");
const { toast } = require("sonner");
const DemoCall = require("./DemoCall").default;
const { act } = React;
const { createRoot } = require("react-dom/client");
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
Element.prototype.scrollIntoView = () => {};

let container; let root;
afterEach(() => { act(() => root.unmount()); container.remove(); jest.clearAllMocks(); });
const q = (id) => document.querySelector(`[data-testid="${id}"]`);
async function flush() { for (let i = 0; i < 5; i += 1) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); }
async function render() {
  container = document.createElement("div"); document.body.appendChild(container);
  root = createRoot(container);
  act(() => root.render(React.createElement(DemoCall)));
}

test("voice unavailable: toast never shows the server's internal reason", async () => {
  api.post.mockResolvedValueOnce({ data: { available: false, reason: "PUBLIC_VOICE_DEMO_ENABLED is off" } });
  await render();
  act(() => q("demo-voice-start-btn").click());
  await flush();
  expect(toast.message).toHaveBeenCalledTimes(1);
  expect(JSON.stringify(toast.message.mock.calls)).not.toMatch(/PUBLIC_VOICE_DEMO_ENABLED/);
  expect(q("demo-card-start")).not.toBeNull();
});

test("expired session (404) returns to the start card instead of getting stuck", async () => {
  api.post
    .mockResolvedValueOnce({ data: { session_id: "s1", greeting: "Hi", business: "B", ai_name: "A", max_turns: 10 } })
    .mockRejectedValueOnce({ response: { status: 404 } });
  await render();
  act(() => q("demo-start-btn").click());
  await flush();
  expect(q("demo-card-live")).not.toBeNull();
  act(() => q("demo-quick-0").click());
  await flush();
  expect(q("demo-card-start")).not.toBeNull();
  expect(toast.message).toHaveBeenCalled();
  expect(toast.error).not.toHaveBeenCalled();
});
