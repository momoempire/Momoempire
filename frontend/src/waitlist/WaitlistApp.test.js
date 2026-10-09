// jsdom in CRA's Jest lacks TextEncoder, which react-router v7 needs at import time.
const { TextEncoder, TextDecoder } = require("util");
Object.assign(globalThis, { TextEncoder, TextDecoder });

const React = require("react");
const { act } = React;
const { createRoot } = require("react-dom/client");
const { MemoryRouter, useLocation } = require("react-router");
const { WaitlistRoutes, WAITLIST_PATHS } = require("./WaitlistApp");

// CRA's Jest 27 can't resolve react-router-dom v7's "react-router/dom" export subpath; the router
// primitives used here are all exported by "react-router" itself.
jest.mock("react-router-dom", () => jest.requireActual("react-router"));

jest.mock("../pages/Landing", () => () => <div data-page="landing" />);
jest.mock("../pages/Legal", () => ({
  Privacy: () => <div data-page="privacy" />,
  Terms: () => <div data-page="terms" />,
}));
jest.mock("../components/ui/sonner", () => ({ Toaster: () => null }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function Where() {
  const loc = useLocation();
  return <span data-path={loc.pathname} />;
}

function render(path) {
  const el = document.createElement("div");
  document.body.appendChild(el);
  const root = createRoot(el);
  act(() => {
    root.render(
      <MemoryRouter initialEntries={[path]}>
        <WaitlistRoutes />
        <Where />
      </MemoryRouter>,
    );
  });
  const out = {
    page: el.querySelector("[data-page]")?.getAttribute("data-page"),
    path: el.querySelector("[data-path]")?.getAttribute("data-path"),
  };
  act(() => root.unmount());
  el.remove();
  return out;
}

test("only /, /privacy and /terms exist", () => {
  expect(WAITLIST_PATHS).toEqual(["/", "/privacy", "/terms"]);
  expect(render("/")).toEqual({ page: "landing", path: "/" });
  expect(render("/privacy")).toEqual({ page: "privacy", path: "/privacy" });
  expect(render("/terms")).toEqual({ page: "terms", path: "/terms" });
});

test.each([
  "/login", "/signup", "/forgot-password", "/reset-password", "/auth/callback", "/onboarding",
  "/app", "/app/ai-employee", "/admin", "/admin/tenants", "/set-password",
  "/pricing", "/estimate", "/b/acme", "/portal/acme", "/invite", "/r/code",
  "/reviews/tok", "/t/consent/tok", "/payment/success", "/q/tok", "/i/tok", "/nope",
])("%s redirects to /", (path) => {
  expect(render(path)).toEqual({ page: "landing", path: "/" });
});

test("/estimate is not a waitlist route (PR #12 stays off the waitlist build)", () => {
  expect(WAITLIST_PATHS).not.toContain("/estimate");
});
