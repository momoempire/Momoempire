import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";

// Direct Google OAuth (authorization-code flow), operator-owned credentials.
// The button renders ONLY when the backend reports Google sign-in configured
// (GOOGLE_CLIENT_ID set server-side). The client id comes from the backend
// config endpoint — never hardcode it or any redirect URL here.
export function googleAuthorizeUrl({ clientId, redirectUri }) {
  const params = new URLSearchParams({
    client_id: clientId,
    redirect_uri: redirectUri,
    response_type: "code",
    scope: "openid email profile",
    access_type: "online",
    prompt: "select_account",
  });
  return `https://accounts.google.com/o/oauth2/v2/auth?${params.toString()}`;
}

export function useGoogleClientId() {
  const [clientId, setClientId] = useState(null);
  useEffect(() => {
    let live = true;
    api
      .get("/auth/google/config")
      .then((r) => {
        if (live && r.data?.google_enabled) setClientId(r.data.google_client_id);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);
  return clientId;
}

export default function GoogleSignInButton({ label = "Continue with Google", testId = "google-signin-btn" }) {
  const clientId = useGoogleClientId();
  if (!clientId) return null;
  const start = () => {
    const redirectUri = window.location.origin + "/auth/callback";
    window.location.href = googleAuthorizeUrl({ clientId, redirectUri });
  };
  return (
    <Button type="button" variant="outline" className="w-full h-11 gap-2" onClick={start} data-testid={testId}>
      <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden>
        <path fill="#EA4335" d="M12 10.2v3.9h5.5c-.2 1.4-1.6 4-5.5 4-3.3 0-6-2.7-6-6s2.7-6 6-6c1.9 0 3.1.8 3.9 1.5l2.7-2.6C16.9 3.2 14.7 2.2 12 2.2 6.9 2.2 2.8 6.3 2.8 11.4S6.9 20.5 12 20.5c6.9 0 9.3-4.9 9.3-8.3 0-.6-.1-1.1-.2-1.6H12z" />
      </svg>
      {label}
    </Button>
  );
}
