import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
// Relative imports so the Jest router test can resolve them (no @/ alias in the Jest config).
import { Toaster } from "../components/ui/sonner";
import CanonicalLink from "../components/CanonicalLink";
import Landing from "../pages/Landing";
import { Privacy, Terms } from "../pages/Legal";

// EMP-WL-002: waitlist-only build (REACT_APP_WAITLIST_ONLY=true). Only these three paths exist;
// anything else (login, signup, /app, /admin, public token pages, ...) redirects to "/".
// Do NOT add /estimate (PR #12, instant-quote estimator) or any other page here unless Brann
// decides it belongs on the waitlist launch: it calls /api/plans and /api/industries, which
// return 404 in backend WAITLIST_ONLY mode.
// TODO(PR #12): once merged, keep /estimate out of this list (covered by WaitlistApp.test.js).
export const WAITLIST_PATHS = ["/", "/privacy", "/terms"];

export function WaitlistRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/privacy" element={<Privacy />} />
      <Route path="/terms" element={<Terms />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function WaitlistApp() {
  return (
    <>
      <BrowserRouter>
        <CanonicalLink />
        <WaitlistRoutes />
      </BrowserRouter>
      <Toaster position="top-right" richColors />
    </>
  );
}
