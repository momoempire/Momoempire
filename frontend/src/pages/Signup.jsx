import { useEffect, useState } from "react";
import { useNavigate, Link, useSearchParams } from "react-router-dom";
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

export default function Signup() {
  const [name, setName] = useState("");
  const [businessName, setBusinessName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const { register } = useAuth();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const planFromQuery = params.get("plan") || "";

  useEffect(() => {
    if (isPaidCheckoutPlan(planFromQuery)) {
      setPendingCheckoutPlan(planFromQuery);
    }
  }, [planFromQuery]);

  const loginHref = isPaidCheckoutPlan(planFromQuery)
    ? `/login?plan=${encodeURIComponent(planFromQuery)}`
    : "/login";

  const submit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      await register({ name, email, password, business_name: businessName });
      toast.success("Account created. Let's set up your office.");
      try {
        const started = await resumePendingCheckoutIfAny();
        if (started) return; // redirecting to Stripe
      } catch (checkoutErr) {
        toast.error(errMessage(checkoutErr) || "Could not start checkout — continue setup, then upgrade from Billing.");
      }
      nav("/onboarding", { replace: true });
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
            Open your AI Office<br /> in two minutes.
          </h2>
          <p className="text-white/60 mt-4 max-w-sm">A workspace built on battle-tested hardware, with a brand that stays yours forever.</p>
          {isPaidCheckoutPlan(planFromQuery) && (
            <p className="text-white/80 mt-4 text-sm" data-testid="signup-plan-hint">
              Selected plan: <span className="font-medium">{planFromQuery}</span> — checkout continues after you create your account.
            </p>
          )}
        </div>
      </section>

      <section className="flex items-center justify-center p-8 md:p-16">
        <div className="w-full max-w-sm">
          <Logo />
          <h1 className="font-display text-3xl tracking-tight mt-8">Create your account</h1>
          <p className="text-muted-foreground text-sm mt-1">You'll become the owner of a brand new workspace.</p>
          <form onSubmit={submit} className="mt-8 space-y-4" data-testid="signup-form">
            <GoogleSignInButton label="Sign up with Google" testId="signup-google-btn" />
            <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
              <span className="flex-1 h-px bg-border" />OR<span className="flex-1 h-px bg-border" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="name">Your name</Label>
              <Input id="name" required value={name} onChange={(e) => setName(e.target.value)} data-testid="signup-name-input" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="biz">Business name</Label>
              <Input id="biz" required value={businessName} onChange={(e) => setBusinessName(e.target.value)} data-testid="signup-business-input" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="email">Email</Label>
              <Input id="email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} data-testid="signup-email-input" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="password">Password</Label>
              <Input id="password" type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} data-testid="signup-password-input" />
              <p className="text-[11px] text-muted-foreground">8+ characters</p>
            </div>
            <Button type="submit" className="w-full h-11 btn-tenant" disabled={loading} data-testid="signup-submit-btn">
              {loading ? "Creating…" : "Create account"}
            </Button>
          </form>
          <p className="text-[13px] text-muted-foreground mt-4">
            Already have an account? <Link to={loginHref} className="text-foreground font-medium" data-testid="signup-login-link">Log in</Link>
          </p>
        </div>
      </section>
    </div>
  );
}
