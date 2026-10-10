import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@/index.css";
import "@/i18n";

// EMP-WL-002: REACT_APP_WAITLIST_ONLY=true at build time serves only /, /privacy and /terms.
// "@root-app" is a build-time alias (craco.config.js) to src/waitlist/WaitlistApp.jsx in that
// build and to src/App.js otherwise, so the waitlist build does not include App.js (dashboard,
// admin, auth pages) at all, and the normal build is bundled exactly as before.
import App from "@root-app";

// WL-033: the waitlist build doesn't use react-query, so skip it there (build-time constant;
// webpack then drops @tanstack/react-query from that bundle).
const WAITLIST_ONLY = process.env.REACT_APP_WAITLIST_ONLY === "true";
const queryClient = WAITLIST_ONLY
  ? null
  : new QueryClient({
      defaultOptions: {
        queries: {
          staleTime: 60_000,
          refetchOnWindowFocus: false,
        },
      },
    });

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(
  <React.StrictMode>
    {WAITLIST_ONLY ? (
      <App />
    ) : (
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    )}
  </React.StrictMode>,
);
