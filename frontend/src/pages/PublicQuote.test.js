// EMP-W-CF-022: /q/:token must not offer "Accept quote" when accepting can only fail
// (expired, or never approved by the owner), and the dashboard must not hand out such links.
const { TextEncoder, TextDecoder } = require("util");
Object.assign(globalThis, { TextEncoder, TextDecoder });

const mockGet = jest.fn();
jest.mock("react-router-dom", () => jest.requireActual("react-router"));
// "@/..." is resolved with virtual mocks so this test runs with or without the craco jest alias.
jest.mock("@/lib/api", () => ({ api: { get: (...a) => mockGet(...a), post: jest.fn() }, errMessage: (e) => String(e) }),
  { virtual: true });
jest.mock("@/lib/money", () => jest.requireActual("../lib/money"), { virtual: true });
jest.mock("@/components/ui/button", () => ({
  Button: ({ children, ...p }) => require("react").createElement("button", p, children),
}), { virtual: true });

const React = require("react");
const { act } = React;
const { createRoot } = require("react-dom/client");
const { MemoryRouter, Routes, Route } = require("react-router");
const PublicQuote = require("./PublicQuote").default;
const { canShareQuoteLink } = require("../lib/quoteLinks");

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
let container;
let root;
afterEach(() => { act(() => root.unmount()); container.remove(); mockGet.mockReset(); });

async function renderQuote(quote) {
  mockGet.mockResolvedValue({ data: { title: "Repair", total: 50, lines: [], business: { name: "Acme" }, ...quote } });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={["/q/tok"]}>
        <Routes><Route path="/q/:token" element={<PublicQuote />} /></Routes>
      </MemoryRouter>,
    );
  });
}
const $ = (id) => container.querySelector(`[data-testid="${id}"]`);

test("approved, unexpired quote offers Accept", async () => {
  await renderQuote({ status: "sent", owner_approved: true, expired: false });
  expect($("public-quote-accept-btn")).not.toBeNull();
});

test("expired quote shows a message instead of Accept", async () => {
  await renderQuote({ status: "sent", owner_approved: true, expired: true });
  expect($("public-quote-accept-btn")).toBeNull();
  expect($("public-quote-expired").textContent).toMatch(/expired.*Acme/);
});

test("quote the owner never approved (dashboard estimate) shows a message instead of Accept", async () => {
  await renderQuote({ status: "sent", owner_approved: false, expired: false });
  expect($("public-quote-accept-btn")).toBeNull();
  expect($("public-quote-not-approved").textContent).toMatch(/Acme/);
});

test("dashboard only shares links customers can act on", () => {
  expect(canShareQuoteLink({ status: "sent", owner_approved: true })).toBe(true);
  expect(canShareQuoteLink({ status: "sent" })).toBe(false); // dashboard estimate marked sent
  expect(canShareQuoteLink({ status: "draft", owner_approved: false })).toBe(false);
  expect(canShareQuoteLink({ status: "draft", owner_approved: true })).toBe(false);
  expect(canShareQuoteLink(null)).toBe(false);
});

test("Payments.jsx gates the Copy link button on canShareQuoteLink", () => {
  const src = require("fs").readFileSync(require("path").join(__dirname, "dashboard/Payments.jsx"), "utf8");
  expect(src).toMatch(/\{canShareQuoteLink\(e\) && <Button[^>]*onClick=\{\(\) => copyEstimateLink\(e\)\}/);
  expect(src.match(/copyEstimateLink\(e\)/g)).toHaveLength(1);
});
