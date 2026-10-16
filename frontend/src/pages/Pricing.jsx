import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";
import { api, errMessage } from "@/lib/api";
import i18n from "@/i18n/estimator";
import { Logo } from "@/components/Logo";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ArrowRight, Check, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { useAuth } from "@/context/AuthContext";
import { isPaidCheckoutPlan, setPendingCheckoutPlan, startStripeCheckout } from "@/lib/checkout";

import { CONTACT_EMAIL } from "@/config/contact";
import ThemeToggle from "@/components/ThemeToggle";
import LanguageSwitcher from "@/components/LanguageSwitcher";

// QA-PRICING: toFixed(0) showed 1999 cents as "$20" while checkout charges $19.99.
export function formatPrice(cents) {
  const dollars = cents / 100;
  return `$${Number.isInteger(dollars) ? dollars.toFixed(0) : dollars.toFixed(2)}`;
}

export default function Pricing() {
  const [plans, setPlans] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [busyPlan, setBusyPlan] = useState("");
  const { t, i18n: ui18n } = useTranslation();
  const lang = (ui18n.language || "en").slice(0, 2);
  const { user } = useAuth();
  const nav = useNavigate();

  // QA-PRICING: a failed /plans call used to leave an empty page with no message.
  const loadPlans = useCallback(() => {
    setLoading(true);
    setLoadError(false);
    api.get("/plans")
      .then((r) => setPlans(Array.isArray(r.data) ? r.data : []))
      .catch(() => setLoadError(true))
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => { loadPlans(); }, [loadPlans]);

  const payable = plans.filter((p) => p.price_cents > 0 && p.key !== "enterprise");
  const trial = plans.find((p) => p.key === "trial");
  const enterprise = plans.find((p) => p.key === "enterprise");

  const startPaidPlan = async (planKey) => {
    if (!isPaidCheckoutPlan(planKey)) return;
    // Logged-in tenant → existing checkout endpoint (server resolves stripe_price_id).
    if (user && user.tenant_id) {
      setBusyPlan(planKey);
      try {
        await startStripeCheckout(planKey);
      } catch (e) {
        toast.error(errMessage(e));
        setBusyPlan("");
      }
      return;
    }
    // Anonymous / still loading auth → carry plan through signup (and login link).
    setPendingCheckoutPlan(planKey);
    nav(`/signup?plan=${encodeURIComponent(planKey)}`);
  };

  return (
    <div className="marketing-shell marketing-themed relative overflow-x-hidden min-h-screen" data-testid="pricing-page">
      <div className="marketing-noise fixed inset-0 opacity-50" aria-hidden />
      <header className="sticky top-0 z-30 glass-crystal border-b border-white/10">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 h-16 gap-3 flex items-center justify-between">
          <Link to="/" className="min-w-0"><Logo /></Link>
          <nav className="hidden md:flex items-center gap-8 text-[13px] text-white/70">
            <Link to="/" className="hover:text-white">{t("pricing.home")}</Link>
            <Link to="/pricing" className="text-white">{t("landing.nav_pricing")}</Link>
          </nav>
          <div className="flex items-center gap-1 sm:gap-2 shrink-0">
            <ThemeToggle lang={lang} className="text-white" />
            <LanguageSwitcher compact />
            <Link to="/login" className="hidden sm:inline-flex"><Button variant="ghost" className="text-white hover:bg-white/10" data-testid="pricing-login-btn">{t("common.login")}</Button></Link>
            <Link to="/signup"><Button className="bg-white text-black hover:bg-white/90" data-testid="pricing-signup-btn">{t("pricing.get_started")}<ArrowRight className="h-4 w-4 ml-1" /></Button></Link>
          </div>
        </div>
      </header>

      <section className="relative max-w-7xl mx-auto px-6 pt-20 pb-10">
        <Badge className="bg-white/10 text-white/80 border border-white/15 mb-5 hover:bg-white/10">
          <Sparkles className="h-3 w-3 mr-1.5" /> {t("pricing.badge")}
        </Badge>
        <h1 className="font-display text-5xl md:text-6xl leading-[0.95] tracking-tight text-white max-w-3xl">
          {t("pricing.title")}
        </h1>
        <p className="mt-6 text-white/70 text-lg max-w-xl">
          {t("pricing.sub")}
        </p>
      </section>

      <section className="relative max-w-7xl mx-auto px-6 pb-24">
        {loading ? (
          <div className="text-white/60" role="status">{t("pricing.loading")}</div>
        ) : loadError ? (
          <div className="glass-crystal rounded-2xl p-6 text-white" role="alert" data-testid="pricing-load-error">
            <p>{t("pricing.load_error")}</p>
            <Button className="mt-4 bg-white text-black hover:bg-white/90" onClick={loadPlans} data-testid="pricing-retry-btn">{t("pricing.retry")}</Button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5">
            {payable.map((p) => (
              <div key={p.key} className="glass-crystal rounded-2xl p-6 flex flex-col" data-testid={`pricing-plan-${p.key}`}>
                <div className="overline text-white/60">{p.name}</div>
                <div className="font-display text-4xl mt-3 tracking-tight text-white">
                  {formatPrice(p.price_cents)}<span className="text-base text-white/60 font-sans">{t("pricing.per_month")}</span>
                </div>
                {p.description && <div className="text-sm text-white/60 mt-2 min-h-[40px]">{p.description}</div>}
                <ul className="mt-5 space-y-2 text-sm text-white/80 flex-1">
                  {p.limits?.ai_minutes != null && (
                    <li className="flex gap-2"><Check className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />{t("pricing.ai_minutes", { n: p.limits.ai_minutes })}</li>
                  )}
                  {p.limits?.calls != null && (
                    <li className="flex gap-2"><Check className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />{t("pricing.calls", { n: p.limits.calls })}</li>
                  )}
                  {p.limits?.sms != null && (
                    <li className="flex gap-2"><Check className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />{t("pricing.sms", { n: p.limits.sms })}</li>
                  )}
                  {(p.features || []).slice(0, 5).map((f) => (
                    <li key={f} className="flex gap-2"><Check className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />{f}</li>
                  ))}
                </ul>
                {p.overage && (p.overage.ai_minutes || p.overage.sms) && (
                  <div className="mt-4 text-[11px] text-white/60 font-mono">
                    {t("pricing.overage")} {[p.overage.ai_minutes ? t("pricing.per_min", { n: p.overage.ai_minutes }) : "", p.overage.sms ? t("pricing.per_sms", { n: p.overage.sms }) : ""].filter(Boolean).join(" · ")}
                  </div>
                )}
                <Button
                  className="w-full mt-6 bg-white text-black hover:bg-white/90"
                  disabled={busyPlan === p.key || user === undefined}
                  onClick={() => startPaidPlan(p.key)}
                  data-testid={`pricing-cta-${p.key}`}
                >
                  {busyPlan === p.key ? t("pricing.redirecting") : <>{t("pricing.start_plan", { name: p.name })}<ArrowRight className="h-4 w-4 ml-1" /></>}
                </Button>
              </div>
            ))}
          </div>
        )}

        <p className="mt-6 text-center text-sm text-white/70"><Link to="/estimate" className="underline hover:text-white" data-testid="pricing-estimate-link">{i18n.t("estimator.entry")}</Link></p>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 mt-6">
          {trial && (
            <div className="glass-crystal rounded-2xl p-7" data-testid="pricing-plan-trial">
              <div className="overline text-white/60">{trial.name}</div>
              <div className="font-display text-3xl mt-2 text-white">{t("pricing.free")}</div>
              <p className="text-sm text-white/60 mt-2 max-w-md">{trial.description}</p>
              <Link to="/signup"><Button variant="outline" className="mt-5 bg-transparent border-white/30 text-white hover:bg-white/10" data-testid="pricing-trial-cta">{t("pricing.start_trial")}</Button></Link>
            </div>
          )}
          {enterprise && (
            <div className="glass-crystal rounded-2xl p-7" data-testid="pricing-plan-enterprise">
              <div className="overline text-white/60">{t("pricing.enterprise")}</div>
              <div className="font-display text-3xl mt-2 text-white">{t("pricing.custom")}</div>
              <p className="text-sm text-white/60 mt-2 max-w-md">{enterprise.description || t("pricing.enterprise_desc")}</p>
              <a href={`mailto:${CONTACT_EMAIL}`}><Button variant="outline" className="mt-5 bg-transparent border-white/30 text-white hover:bg-white/10" data-testid="pricing-enterprise-cta">{t("pricing.contact_sales")}</Button></a>
            </div>
          )}
        </div>

        <div className="mt-14" data-testid="pricing-addons">
          <h2 className="font-display text-3xl text-white">{t("pricing.addons_title")}</h2>
          <p className="mt-2 text-white/70 max-w-xl">{t("pricing.addons_sub")}</p>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-5 mt-6">
            {[
              { key: "books-light", name: t("pricing.addon_light_name"), price: "$10", desc: t("pricing.addon_light_desc") },
              { key: "books-medium", name: t("pricing.addon_medium_name"), price: "$20", desc: t("pricing.addon_medium_desc") },
              { key: "books-heavy", name: t("pricing.addon_heavy_name"), price: t("pricing.addon_volume"), desc: t("pricing.addon_heavy_desc") },
            ].map((a) => (
              <div key={a.key} className="glass-crystal rounded-2xl p-6" data-testid={`pricing-addon-${a.key}`}>
                <div className="overline text-white/60">{a.name}</div>
                <div className="font-display text-3xl mt-2 text-white">{a.price}<span className="text-base text-white/60 font-sans">{a.key === "books-heavy" ? "" : t("pricing.per_month")}</span></div>
                <p className="text-sm text-white/60 mt-2">{a.desc}</p>
              </div>
            ))}
          </div>
        </div>

        <p className="mt-10 text-xs text-white/60">{t("pricing.footnote")}</p>
      </section>
    </div>
  );
}
