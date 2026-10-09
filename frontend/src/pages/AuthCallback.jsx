import { useEffect, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api, errMessage } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { toast } from "sonner";
import { Logo } from "@/components/Logo";
import { Loader2 } from "lucide-react";
import { resumePendingCheckoutIfAny } from "@/lib/checkout";

// REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
export default function AuthCallback() {
  const location = useLocation();
  const nav = useNavigate();
  const { setUser } = useAuth();
  const processed = useRef(false);

  useEffect(() => {
    if (processed.current) return;
    processed.current = true;
    const hash = location.hash || "";
    const match = hash.match(/session_id=([^&]+)/);
    if (!match) { nav("/login", { replace: true }); return; }
    const session_id = decodeURIComponent(match[1]);
    (async () => {
      try {
        const { data } = await api.post("/auth/google/session", { session_id });
        setUser(data);
        // Clear the hash
        window.history.replaceState({}, "", window.location.pathname);
        toast.success(`Welcome, ${data.name || data.email}`);
        try {
          const started = await resumePendingCheckoutIfAny();
          if (started) return;
        } catch (checkoutErr) {
          toast.error(errMessage(checkoutErr) || "Could not start checkout — open Billing to subscribe.");
        }
        const target = data.role === "platform_admin" ? "/admin" : "/app";
        nav(target, { replace: true, state: { user: data } });
      } catch (e) {
        toast.error(errMessage(e));
        nav("/login", { replace: true });
      }
    })();
  }, [location.hash, nav, setUser]);

  return (
    <div className="min-h-screen grid place-items-center">
      <div className="text-center">
        <Logo />
        <div className="mt-8 flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Signing you in with Google…
        </div>
      </div>
    </div>
  );
}
