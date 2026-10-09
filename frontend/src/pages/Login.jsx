import { useEffect, useState } from "react";
import { useNavigate, Link, useLocation, useSearchParams } from "react-router-dom";
import { Logo } from "@/components/Logo";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/context/AuthContext";
import { toast } from "sonner";
import { errMessage } from "@/lib/api";
import GoogleSignInButton from "@/components/GoogleSignInButton";
import {
  isPaidCheckoutPlan,
  setPendingCheckoutPlan,
  resumePendingCheckoutIfAny,
} from "@/lib/checkout";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const [params] = useSearchParams();
  const planFromQuery = params.get("plan") || "";

  useEffect(() => {
    if (isPaidCheckoutPlan(planFromQuery)) {
      setPendingCheckoutPlan(planFromQuery);
    }
  }, [planFromQuery]);

  const signupHref = isPaidCheckoutPlan(planFromQuery)
    ? `/signup?plan=${encodeURIComponent(planFromQuery)}`
    : "/signup";

  const submit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const user = await login(email, password);
      toast.success(`Welcome back, ${user.name || user.email}`);
      try {
        const started = await resumePendingCheckoutIfAny();
        if (started) return;
      } catch (checkoutErr) {
        toast.error(errMessage(checkoutErr) || "Could not start checkout — open Billing to subscribe.");
      }
      const target = user.role === "platform_admin" ? "/admin" : (loc.state?.from || "/app");
      nav(target, { replace: true });
    } catch (err) {
      toast.error(errMessage(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen grid md:grid-cols-2 bg-background">
      <section className="relative hidden md:flex marketing-shell items-end p-12">
        <div className="marketing-noise fixed inset-0 opacity-50" aria-hidden />
        <div className="relative">
          <Logo variant="light" />
          <h2 className="font-display text-5xl mt-8 text-white tracking-tight leading-[1.05] max-w-md">
            Your digital office,<br /> staffed by one calm AI.
          </h2>
          <p className="text-white/60 mt-4 max-w-sm">Log in to the console that answers calls, books jobs, and runs your business — in every industry.</p>
          <div className="mt-10 font-mono text-[11px] text-white/40">⌁ Multi-tenant · Hardware elite · Face swappable</div>
        </div>
      </section>

      <section className="flex items-center justify-center p-8 md:p-16">
        <div className="w-full max-w-sm">
          <Logo />
          <h1 className="font-display text-3xl tracking-tight mt-8">Welcome back</h1>
          <p className="text-muted-foreground text-sm mt-1">Log in to your workspace.</p>
          <form onSubmit={submit} className="mt-8 space-y-4" data-testid="login-form">
            <GoogleSignInButton label="Continue with Google" testId="login-google-btn" />
            <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
              <span className="flex-1 h-px bg-border" />OR<span className="flex-1 h-px bg-border" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="email">Email</Label>
              <Input id="email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} data-testid="login-email-input" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="password">Password</Label>
              <Input id="password" type="password" required value={password} onChange={(e) => setPassword(e.target.value)} data-testid="login-password-input" />
            </div>
            <Button type="submit" className="w-full h-11 btn-tenant" disabled={loading} data-testid="login-submit-btn">
              {loading ? "Signing in…" : "Log in"}
            </Button>
          </form>
          <div className="flex items-center justify-between mt-4 text-[13px]">
            <Link to="/forgot-password" className="text-muted-foreground hover:text-foreground" data-testid="login-forgot-link">Forgot password?</Link>
            <Link to={signupHref} className="font-medium" data-testid="login-signup-link">Create account</Link>
          </div>
        </div>
      </section>
    </div>
  );
}
