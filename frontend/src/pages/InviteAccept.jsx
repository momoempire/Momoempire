import { useEffect, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { api, errMessage } from "@/lib/api";
import { Logo } from "@/components/Logo";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";
import { googleAuthorizeUrl, useGoogleClientId } from "@/components/GoogleSignInButton";

export default function InviteAccept() {
  const [sp] = useSearchParams();
  const token = sp.get("token") || "";
  const [preview, setPreview] = useState(null);
  const [form, setForm] = useState({ name: "", password: "" });
  const [loading, setLoading] = useState(false);
  const [googleLoading, setGoogleLoading] = useState(false);
  const googleClientId = useGoogleClientId();
  const [err, setErr] = useState(null);
  const nav = useNavigate();

  useEffect(() => {
    if (!token) { setErr("Missing invite token"); return; }
    api.get(`/public/invitations/${token}`).then((r) => setPreview(r.data)).catch((e) => setErr(errMessage(e)));
  }, [token]);

  // After the direct Google OAuth redirect we land back here with ?code=
  // (the invite token stays in the query string; the code is stripped after use).
  useEffect(() => {
    if (!token) return;
    const code = sp.get("code");
    if (!code) return;
    const redirect_uri = window.location.origin + "/invite?token=" + encodeURIComponent(token);
    setGoogleLoading(true);
    (async () => {
      try {
        const { data } = await api.post("/public/invitations/accept-google", { token, code, redirect_uri });
        window.history.replaceState({}, "", window.location.pathname + "?token=" +encodeURIComponent(token));
        toast.success(`Welcome, ${data.name || data.email}`);
        nav("/app", { replace: true });
      } catch (e) {
        toast.error(errMessage(e));
      } finally {
        setGoogleLoading(false);
      }
    })();
  }, [token, nav, sp]);

  const accept = async () => {
    setLoading(true);
    try {
      await api.post("/public/invitations/accept", { token, name: form.name, password: form.password });
      toast.success("Welcome aboard!");
      nav("/app", { replace: true });
    } catch (e) { toast.error(errMessage(e)); }
    finally { setLoading(false); }
  };

  const startGoogle = () => {
    if (!googleClientId) return;
    // Return to this invite URL so we keep the token in query string
    const redirectUri = window.location.origin + "/invite?token=" + encodeURIComponent(token);
    window.location.href = googleAuthorizeUrl({ clientId: googleClientId, redirectUri });
  };

  if (err) return <div className="min-h-screen grid place-items-center text-sm text-muted-foreground">{err}</div>;
  if (!preview) return <div className="min-h-screen grid place-items-center text-sm text-muted-foreground">Loading…</div>;

  return (
    <div className="min-h-screen grid place-items-center p-6" data-testid="invite-accept-page">
      <div className="w-full max-w-sm">
        <Logo />
        <h1 className="font-display text-3xl mt-8 tracking-tight">Join {preview.tenant_name}</h1>
        <p className="text-sm text-muted-foreground mt-1">
          You've been invited as <span className="font-medium text-foreground">{preview.invitation.role}</span>.{googleClientId ? " Accept in one click with Google:" : ""}
        </p>

        {googleClientId && (<>
        <Button
          type="button"
          variant="outline"
          className="mt-6 w-full h-11 gap-2"
          onClick={startGoogle}
          disabled={googleLoading}
          data-testid="invite-google-btn"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden>
            <path fill="#EA4335" d="M12 10.2v3.9h5.5c-.2 1.4-1.6 4-5.5 4-3.3 0-6-2.7-6-6s2.7-6 6-6c1.9 0 3.1.8 3.9 1.5l2.7-2.6C16.9 3.2 14.7 2.2 12 2.2 6.9 2.2 2.8 6.3 2.8 11.4S6.9 20.5 12 20.5c6.9 0 9.3-4.9 9.3-8.3 0-.6-.1-1.1-.2-1.6H12z" />
          </svg>
          {googleLoading ? "Finishing sign-in…" : "Accept with Google"}
        </Button>
        <p className="text-[11px] text-muted-foreground text-center mt-2">
          Your Google email must match <span className="font-mono">{preview.invitation.email}</span>.
        </p>
        </>)}

        <div className="my-6 flex items-center gap-3 text-[11px] text-muted-foreground">
          <div className="flex-1 h-px bg-border" /> or create a password <div className="flex-1 h-px bg-border" />
        </div>

        <div className="space-y-4">
          <div className="space-y-1.5"><Label>Email</Label><Input value={preview.invitation.email} disabled /></div>
          <div className="space-y-1.5"><Label>Your name</Label><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} data-testid="invite-name-input" /></div>
          <div className="space-y-1.5"><Label>Password</Label><Input type="password" minLength={8} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} data-testid="invite-password-input" /></div>
          <Button className="w-full h-11 btn-tenant" onClick={accept} disabled={loading || !form.name || form.password.length < 8} data-testid="invite-accept-btn">
            {loading ? "Joining…" : "Join with password"}
          </Button>
        </div>
      </div>
    </div>
  );
}
