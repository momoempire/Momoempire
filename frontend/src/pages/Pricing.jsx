import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import i18n from "@/i18n/estimator";
import { Logo } from "@/components/Logo";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ArrowRight, Check, Sparkles } from "lucide-react";

export default function Pricing() {
  const [plans, setPlans] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get("/plans").then((r) => setPlans(r.data)).finally(() => setLoading(false));
  }, []);

  const payable = plans.filter((p) => p.price_cents > 0 && p.key !== "enterprise");
  const trial = plans.find((p) => p.key === "trial");
  const enterprise = plans.find((p) => p.key === "enterprise");

  return (
    <div className="marketing-shell relative overflow-x-hidden min-h-screen" data-testid="pricing-page">
      <div className="marketing-noise fixed inset-0 opacity-50" aria-hidden />
      <header className="sticky top-0 z-30 glass-crystal border-b border-white/10">
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          <Link to="/"><Logo variant="light" /></Link>
          <nav className="hidden md:flex items-center gap-8 text-[13px] text-white/70">
            <Link to="/" className="hover:text-white">Home</Link>
            <Link to="/pricing" className="text-white">Pricing</Link>
          </nav>
          <div className="flex items-center gap-2">
            <Link to="/login"><Button variant="ghost" className="text-white hover:bg-white/10" data-testid="pricing-login-btn">Log in</Button></Link>
            <Link to="/signup"><Button className="bg-white text-black hover:bg-white/90" data-testid="pricing-signup-btn">Get started<ArrowRight className="h-4 w-4 ml-1" /></Button></Link>
          </div>
        </div>
      </header>

      <section className="relative max-w-7xl mx-auto px-6 pt-20 pb-10">
        <Badge className="bg-white/10 text-white/80 border border-white/15 mb-5 hover:bg-white/10">
          <Sparkles className="h-3 w-3 mr-1.5" /> Pricing
        </Badge>
        <h1 className="font-display text-5xl md:text-6xl leading-[0.95] tracking-tight text-white max-w-3xl">
          Pay for the office, not the overtime.
        </h1>
        <p className="mt-6 text-white/70 text-lg max-w-xl">
          Every plan includes the AI employee, a branded page, and your CRM. Scale up as calls grow.
          Overage billed per plan — no surprise shutoffs.
        </p>
      </section>

      <section className="relative max-w-7xl mx-auto px-6 pb-24">
        {loading ? (
          <div className="text-white/60">Loading plans…</div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5">
            {payable.map((p) => (
              <div key={p.key} className="glass-crystal rounded-2xl p-6 flex flex-col" data-testid={`pricing-plan-${p.key}`}>
                <div className="overline text-white/60">{p.name}</div>
                <div className="font-display text-4xl mt-3 tracking-tight text-white">
                  ${(p.price_cents / 100).toFixed(0)}<span className="text-base text-white/50 font-sans">/mo</span>
                </div>
                {p.description && <div className="text-sm text-white/60 mt-2 min-h-[40px]">{p.description}</div>}
                <ul className="mt-5 space-y-2 text-sm text-white/80 flex-1">
                  {p.limits?.ai_minutes != null && (
                    <li className="flex gap-2"><Check className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />{p.limits.ai_minutes} AI minutes / mo</li>
                  )}
                  {p.limits?.calls != null && (
                    <li className="flex gap-2"><Check className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />{p.limits.calls} calls / mo</li>
                  )}
                  {p.limits?.sms != null && (
                    <li className="flex gap-2"><Check className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />{p.limits.sms} SMS / mo</li>
                  )}
                  {(p.features || []).slice(0, 5).map((f) => (
                    <li key={f} className="flex gap-2"><Check className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />{f}</li>
                  ))}
                </ul>
                {p.overage && (p.overage.ai_minutes || p.overage.sms) && (
                  <div className="mt-4 text-[11px] text-white/50 font-mono">
                    overage: {p.overage.ai_minutes ? `${p.overage.ai_minutes}¢/min` : ""} {p.overage.sms ? `· ${p.overage.sms}¢/sms` : ""}
                  </div>
                )}
                <Link to="/signup" className="mt-6">
                  <Button className="w-full bg-white text-black hover:bg-white/90" data-testid={`pricing-cta-${p.key}`}>
                    Start {p.name}<ArrowRight className="h-4 w-4 ml-1" />
                  </Button>
                </Link>
              </div>
            ))}
          </div>
        )}

        <p className="mt-6 text-center text-sm text-white/70"><Link to="/estimate" className="underline hover:text-white" data-testid="pricing-estimate-link">{i18n.t("estimator.entry")}</Link></p>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 mt-6">
          {trial && (
            <div className="glass-crystal rounded-2xl p-7" data-testid="pricing-plan-trial">
              <div className="overline text-white/60">{trial.name}</div>
              <div className="font-display text-3xl mt-2 text-white">Free</div>
              <p className="text-sm text-white/60 mt-2 max-w-md">{trial.description}</p>
              <Link to="/signup"><Button variant="outline" className="mt-5 bg-transparent border-white/30 text-white hover:bg-white/10" data-testid="pricing-trial-cta">Start free trial</Button></Link>
            </div>
          )}
          {enterprise && (
            <div className="glass-crystal rounded-2xl p-7" data-testid="pricing-plan-enterprise">
              <div className="overline text-white/60">Enterprise</div>
              <div className="font-display text-3xl mt-2 text-white">Custom</div>
              <p className="text-sm text-white/60 mt-2 max-w-md">{enterprise.description || "Multiple locations, custom AI personas, custom integrations."}</p>
              <a href="mailto:sales@aioffice.io"><Button variant="outline" className="mt-5 bg-transparent border-white/30 text-white hover:bg-white/10" data-testid="pricing-enterprise-cta">Contact sales</Button></a>
            </div>
          )}
        </div>

        <p className="mt-10 text-xs text-white/40">Plans update live from the admin dashboard — pricing shown is always current.</p>
      </section>
    </div>
  );
}
