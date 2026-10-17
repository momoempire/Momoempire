import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Logo } from "@/components/Logo";
import LanguageSwitcher from "@/components/LanguageSwitcher";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { api, errMessage } from "@/lib/api";
import { waitlistErrorText } from "@/lib/waitlistErrors";
import { toast } from "sonner";
import DemoCall from "@/components/DemoCall";
import { isWaitlistOnly } from "@/lib/waitlistMode";
import TurnstileWidget from "@/components/TurnstileWidget";
import ThemeToggle from "@/components/ThemeToggle";
import {
  ArrowRight, Bot, Target, Rocket, PhoneCall, UserPlus,
  Headphones, ChevronDown, Check, Snowflake, Flame, Thermometer, Wind, Fan, Sparkles,
} from "lucide-react";

import { BRAND_NAME } from "@/config/brand";
import { CONTACT_EMAIL } from "@/config/contact";
import RoiCalculator from "../components/roi/RoiCalculator";
import WorkflowDiagrams from "../components/roi/WorkflowDiagrams";
import { ROI_PAGES } from "../config/roi";

// Mae, the AI-assistant mascot (conversational lead capture). Lazy so the landing page's
// initial bundle is unchanged; it feeds the same /public/waitlist lead path as the form below.
const LeadAssistant = lazy(() => import("@/components/LeadAssistant"));
const PILLAR_META = [
  { icon: Bot, titleKey: "landing.pillar_receptionist_title", descKey: "landing.pillar_receptionist_desc", testid: "pillar-receptionist" },
  { icon: Target, titleKey: "landing.pillar_sales_title", descKey: "landing.pillar_sales_desc", testid: "pillar-sales" },
  { icon: Rocket, titleKey: "landing.pillar_growth_title", descKey: "landing.pillar_growth_desc", testid: "pillar-growth" },
];
// Decorative heating/cooling accents (aria-hidden): cool = blue, warm = orange.
const PILLAR_ACCENT = [
  { icon: Snowflake, tone: "hvac-cool" },
  { icon: Flame, tone: "hvac-warm" },
  { icon: Wind, tone: "hvac-cool" },
];

// Decorative HVAC icon strip under the hero copy. Purely visual, hidden from screen readers.
function HvacIconStrip() {
  const items = [
    [Snowflake, "hvac-cool"], [Fan, "hvac-cool"], [Thermometer, "hvac-neutral"], [Flame, "hvac-warm"], [Wind, "hvac-cool"],
  ];
  return (
    <div className="mt-8 flex items-center gap-3" aria-hidden="true" data-testid="hvac-icon-strip">
      {items.map(([Icon, tone], i) => (
        <span key={i} className={`hvac-chip ${tone} h-11 w-11 rounded-xl grid place-items-center`}><Icon className="h-5 w-5" /></span>
      ))}
    </div>
  );
}

const INDUSTRY_KEYS = ["hvac", "dental", "legal", "salon"];

const STEP_META = [
  { icon: UserPlus, n: "01", titleKey: "landing.step_1_title", descKey: "landing.step_1_desc" },
  { icon: PhoneCall, n: "02", titleKey: "landing.step_2_title", descKey: "landing.step_2_desc" },
  { icon: Headphones, n: "03", titleKey: "landing.step_3_title", descKey: "landing.step_3_desc" },
];

export default function Landing({ industry = null }) {
  const navigate = useNavigate();
  const { t: tCurrent, i18n } = useTranslation();
  // EMP-WL-016: the waitlist page is English-only for now (most of its copy isn't translated), so
  // its few translated labels stay English too, even for a Spanish browser or a saved "es" choice.
  // Industry pages (/hvac, /es/hvac, ...) render in the language of their route.
  const t = industry ? i18n.getFixedT(industry.lang) : isWaitlistOnly() ? i18n.getFixedT("en") : tCurrent;
  const [trial, setTrial] = useState(null);
  const [openFaq, setOpenFaq] = useState(0);
  const [wlForm, setWlForm] = useState({ email: "", name: "", business_name: "", industry: industry ? industry.copy.formIndustry : "", note: "", website: "", turnstile_token: "" });
  const [wlSent, setWlSent] = useState(false);
  const [wlSending, setWlSending] = useState(false);
  const [wlError, setWlError] = useState("");
  const wlSuccessRef = useRef(null);
  // WL-027: the waitlist-only build hides the demo, trial, login and pricing (their APIs 404 there).
  const waitlistOnly = isWaitlistOnly();
  const wlEmailRef = useRef(null);

  // EMP-WL-034: header/hero "Join waitlist" CTAs scroll to the form and focus the email field.
  // They stay plain #waitlist links, so they also work without JS.
  const goToWaitlist = (e) => {
    e.preventDefault();
    const section = document.getElementById("waitlist");
    if (section && section.scrollIntoView) section.scrollIntoView({ behavior: "smooth", block: "start" });
    if (wlEmailRef.current) wlEmailRef.current.focus({ preventScroll: true });
  };

  // Mae's "just browsing" branch: a #pricing section if a page ever grows one, the pricing
  // route in the full build, and the features section in the early-access build (no pricing
  // page exists there). Her error fallback scrolls to this page's own lead form.
  const scrollToId = (id) => {
    const el = document.getElementById(id);
    if (el && el.scrollIntoView) el.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  const mascotShowPricing = () => {
    if (document.getElementById("pricing")) { scrollToId("pricing"); return; }
    if (waitlistOnly) scrollToId("features");
    else navigate("/pricing");
  };
  const mascotShowForm = () => scrollToId("waitlist");

  useEffect(() => {
    if (isWaitlistOnly()) return; // no /api/plans call in the waitlist-only build
    api.get("/plans").then((r) => {
      const plan = (r.data || []).find((p) => p.key === "trial");
      setTrial(plan || null);
    }).catch(() => {});
  }, []);

  const onTurnstileToken = useCallback((token) => setWlForm((f) => ({ ...f, turnstile_token: token })), []);

  const faqs = useMemo(() => (
    [1, 2, 3, 4, 5, 6].map((i) => ({
      q: t(`landing.faq_${i}_q`),
      a: t(`landing.faq_${i}_a`),
    }))
  ), [t]);

  const industries = useMemo(() => (
    INDUSTRY_KEYS.map((key) => ({
      key,
      label: t(`landing.industry_${key}_label`),
      example: t(`landing.industry_${key}_example`),
      bullets: [1, 2, 3].map((n) => t(`landing.industry_${key}_b${n}`)),
    }))
  ), [t]);

  const submitWaitlist = async (e) => {
    e.preventDefault();
    setWlSending(true);
    setWlError("");
    try {
      await api.post("/public/waitlist", wlForm);
      setWlSent(true); toast.success(t("landing.waitlist_thanks_toast"));
    } catch (err) {
      // EMP-WL-013: show the error next to the form (announced, linked to the email field)
      // instead of only in a toast.
      // EMP-WL-064 / WL-071: translated 503 text, plain wording for validation errors.
      setWlError(waitlistErrorText(err, t, errMessage));
    }
    finally { setWlSending(false); }
  };

  // EMP-WL-013: the form is replaced by the success message; move focus there so keyboard and
  // screen-reader users aren't left on a removed element.
  useEffect(() => { if (wlSent && wlSuccessRef.current) wlSuccessRef.current.focus(); }, [wlSent]);

  const trialLimits = trial?.limits || {};
  const trialDays = trial?.trial_days || 60;
  const trialMinutes = trialLimits.ai_minutes || 50;
  const trialCalls = trialLimits.calls || 25;

  return (
    <div className="marketing-shell marketing-themed relative overflow-x-hidden" data-testid={industry ? `industry-page-${industry.slug}` : "landing-page"} lang={industry ? industry.lang : undefined}>
      <div className="marketing-noise fixed inset-0 opacity-50" aria-hidden />

      {/* Nav */}
      <header className="sticky top-0 z-30 glass-crystal border-b border-white/10">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between gap-3">
          <Logo />
          {!industry && <nav className="hidden md:flex items-center gap-8 text-[13px] text-white/70">
            <a href="#features" className="hover:text-white">{t("landing.nav_features")}</a>
            <a href="#industries" className="hover:text-white">{t("landing.nav_industries")}</a>
            {!waitlistOnly && <a href="#how" className="hover:text-white">{t("landing.nav_how")}</a>}
            {!waitlistOnly && <a href="#demo" className="hover:text-white">{t("landing.nav_demo")}</a>}
            {!waitlistOnly && <Link to="/pricing" className="hover:text-white">{t("landing.nav_pricing")}</Link>}
            <a href="#faq" className="hover:text-white">{t("landing.nav_faq")}</a>
          </nav>}
          <div className="flex items-center gap-2 shrink-0">
            <ThemeToggle lang={industry ? industry.lang : isWaitlistOnly() ? "en" : (i18n.language || "en").slice(0, 2)} className="text-white" />
            {/* EMP-WL-016: the waitlist page is English-only for now, so no EN/ES switcher there. */}
            {!waitlistOnly && <LanguageSwitcher compact />}
            {/* EMP-WL-034: waitlist-only header CTA (existing "Join waitlist" label). */}
            {waitlistOnly && (
              // EMP-WL-051: one interactive element (a link styled as the button), not <button> in <a>.
              <Button asChild className="bg-white text-black hover:bg-white/90">
                <a href="#waitlist" onClick={goToWaitlist} data-testid="header-cta-waitlist">{t("landing.cta_join_waitlist")}<ArrowRight className="h-4 w-4 ml-1" /></a>
              </Button>
            )}
            {!waitlistOnly && <Link to="/login"><Button variant="ghost" className="text-white hover:bg-white/10" data-testid="landing-login-btn">{t("common.login")}</Button></Link>}
            {!waitlistOnly && <Link to="/signup"><Button className="bg-white text-black hover:bg-white/90" data-testid="landing-signup-btn">{t("landing.cta_trial")}<ArrowRight className="h-4 w-4 ml-1" /></Button></Link>}
          </div>
        </div>
      </header>

      {/* Hero */}
      <section className="relative max-w-7xl mx-auto px-4 sm:px-6 pt-16 sm:pt-20 pb-20">
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
          <div className="min-w-0 lg:col-span-7">
            <Badge className="bg-white/10 text-white/80 border border-white/15 mb-5 hover:bg-white/10">
              {industry && industry.slug !== "hvac" ? <Sparkles className="h-3 w-3 mr-1.5" aria-hidden /> : <Thermometer className="h-3 w-3 mr-1.5" aria-hidden />} {industry ? industry.copy.label : t("landing.badge_platform")}
            </Badge>
            <h1 className="font-display text-4xl sm:text-5xl md:text-7xl leading-[0.95] tracking-tight text-white break-words">
              {industry ? industry.copy.title : <>{t("landing.hero_title_lead")} <span className="hvac-accent-text">{t("landing.hero_title_trail")}</span></>}
            </h1>
            <p className="mt-6 text-white/70 text-lg max-w-xl">
              {industry ? industry.copy.sub : t("landing.hero_sub")}
            </p>
            <HvacIconStrip />
            {/* EMP-WL-034 / WL-001: waitlist-only hero CTA is the primary button (existing label). */}
            {waitlistOnly && (
              <div className="mt-8">
                <Button asChild className="h-12 px-6 bg-white text-black hover:bg-white/90 text-base">
                  <a href="#waitlist" onClick={goToWaitlist} data-testid="hero-cta-waitlist">
                    {t("landing.cta_join_waitlist")}<ArrowRight className="h-4 w-4 ml-1.5" />
                  </a>
                </Button>
              </div>
            )}
            {!waitlistOnly && <div className="mt-8 flex flex-wrap gap-3 items-center">
              <Link to="/signup">
                <Button className="h-12 px-6 bg-white text-black hover:bg-white/90 text-base" data-testid="hero-cta-signup">
                  {t("landing.hero_cta_free")}<ArrowRight className="h-4 w-4 ml-1.5" />
                </Button>
              </Link>
              <a href="#demo"><Button variant="ghost" className="h-12 px-5 text-white hover:bg-white/10" data-testid="hero-cta-demo">{t("landing.hero_cta_demo")}</Button></a>
            </div>}
            {trial && !waitlistOnly && (
              <p
                className="mt-4 text-[13px] text-white/60"
                data-testid="hero-trial-copy"
                dangerouslySetInnerHTML={{
                  __html: t("landing.hero_trial_copy", {
                    days: trialDays,
                    minutes: trialMinutes,
                    calls: trialCalls,
                  }),
                }}
              />
            )}
          </div>
          <div className="min-w-0 lg:col-span-5">
            {industry ? <IndustryPoints copy={industry.copy} slug={industry.slug} /> : <HeroPreview />}
          </div>
        </div>
      </section>

      {industry && ROI_PAGES.includes(industry.slug) && (
        <section id="roi" className="relative max-w-7xl mx-auto px-4 sm:px-6 py-12 space-y-12">
          <RoiCalculator niche={industry.slug} lang={industry.lang} />
          <WorkflowDiagrams lang={industry.lang} />
        </section>
      )}

      {!industry && (<>
      {/* Features */}
      <section id="features" className="relative max-w-7xl mx-auto px-6 py-20">
        <div className="overline text-white/70 mb-3">{t("landing.pillars_eyebrow")}</div>
        <h2 className="font-display text-4xl md:text-5xl text-white tracking-tight max-w-2xl">{t("landing.pillars_title")}</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-5 mt-10">
          {PILLAR_META.map((p, idx) => {
            const A = PILLAR_ACCENT[idx % PILLAR_ACCENT.length];
            return (
            <div key={p.testid} className="glass-crystal hvac-card relative rounded-2xl p-6 text-white" data-testid={p.testid}>
              <A.icon className={`${A.tone} hvac-corner absolute top-5 right-5 h-5 w-5`} aria-hidden />
              <div className={`hvac-chip ${A.tone} h-10 w-10 rounded-xl grid place-items-center mb-5`}><p.icon className="h-5 w-5" /></div>
              <h3 className="font-medium text-xl">{t(p.titleKey)}</h3>
              <p className="text-white/70 text-sm mt-2 leading-relaxed">{t(p.descKey)}</p>
            </div>
            );
          })}
        </div>
      </section>

      {/* Industries */}
      <section id="industries" className="relative max-w-7xl mx-auto px-6 py-20">
        <div className="overline text-white/70 mb-3">{t("landing.industries_eyebrow")}</div>
        <h2 className="font-display text-4xl md:text-5xl text-white tracking-tight max-w-2xl">{t("landing.industries_title")}</h2>
        <p className="text-white/70 mt-4 max-w-xl">{t("landing.industries_sub")}</p>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mt-10">
          {industries.map((i) => (
            <div key={i.key} className="glass-crystal rounded-2xl p-5 text-white" data-testid={`industry-card-${i.key}`}>
              <div className="font-display text-2xl tracking-tight">{i.label}</div>
              <div className="text-[12px] text-white/60 mt-1">{i.example}</div>
              <ul className="mt-4 space-y-1.5 text-sm">
                {i.bullets.map((b) => (
                  <li key={b} className="flex gap-2"><Check className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" /><span className="text-white/80">{b}</span></li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </section>

      {/* How it works. Hidden in the waitlist-only build: its steps describe self-serve signup
          ("Sign up", "Create your workspace"), which that build doesn't offer (EMP-WL-001).
          TODO(WL-001, Brann): waitlist-mode version of this section, if wanted. */}
      {!waitlistOnly && <section id="how" className="relative max-w-7xl mx-auto px-6 py-20">
        <div className="overline text-white/70 mb-3">{t("landing.how_eyebrow")}</div>
        <h2 className="font-display text-4xl md:text-5xl text-white tracking-tight max-w-2xl">{t("landing.how_title")}</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-5 mt-10">
          {STEP_META.map((s) => (
            <div key={s.n} className="glass-crystal rounded-2xl p-7 text-white" data-testid={`step-${s.n}`}>
              <div className="font-mono text-white/60 text-sm">{s.n}</div>
              <div className="h-10 w-10 rounded-xl bg-white/10 border border-white/10 grid place-items-center mt-3 mb-4"><s.icon className="h-5 w-5" /></div>
              <h3 className="font-medium text-xl">{t(s.titleKey)}</h3>
              <p className="text-white/70 text-sm mt-2 leading-relaxed">{t(s.descKey)}</p>
            </div>
          ))}
        </div>
      </section>}

      {/* Live demo (hidden in the waitlist-only build: /api/public/demo/* returns 404 there) */}
      {!waitlistOnly && <section id="demo" className="relative max-w-7xl mx-auto px-6 py-20">
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
          <div className="min-w-0 lg:col-span-5">
            <div className="overline text-white/70 mb-3">{t("landing.demo_section_eyebrow")}</div>
            <h2 className="font-display text-4xl md:text-5xl text-white tracking-tight">{t("landing.demo_section_title")}</h2>
            <p className="text-white/70 mt-5 max-w-md">
              {t("landing.demo_section_sub")}
            </p>
            <ul className="mt-6 space-y-2 text-sm text-white/80">
              <li className="flex gap-2"><Check className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />{t("landing.demo_bullet_1")}</li>
              <li className="flex gap-2"><Check className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />{t("landing.demo_bullet_2")}</li>
              <li className="flex gap-2"><Check className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />{t("landing.demo_bullet_3")}</li>
            </ul>
          </div>
          <div className="min-w-0 lg:col-span-7">
            <DemoCall />
          </div>
        </div>
      </section>}

      {/* FAQ */}
      <section id="faq" className="relative max-w-4xl mx-auto px-6 py-20">
        <div className="overline text-white/70 mb-3">{t("landing.faq_eyebrow")}</div>
        <h2 className="font-display text-4xl md:text-5xl text-white tracking-tight">{t("landing.faq_title")}</h2>
        <div className="mt-10 divide-y divide-white/10 border-y border-white/10">
          {faqs.map((f, i) => (
            <button key={f.q} onClick={() => setOpenFaq(openFaq === i ? -1 : i)} className="w-full text-left py-5 text-white hover:bg-white/5 px-2 transition" data-testid={`faq-${i}`}>
              <div className="flex items-start justify-between gap-4">
                <div className="font-medium">{f.q}</div>
                <ChevronDown className={`h-5 w-5 shrink-0 transition ${openFaq === i ? "rotate-180" : ""}`} />
              </div>
              {openFaq === i && <p className="text-white/70 mt-3 text-sm leading-relaxed">{f.a}</p>}
            </button>
          ))}
        </div>
      </section>
      </>)}

      {/* Waitlist */}
      {/* EMP-WL-006: one column on phones. The old grid-cols-12 + gap-8 kept 11 column gaps
          (352 px) even with col-span-12, which pushed the form ~43 px past a 390 px screen. */}
      <section id="waitlist" className="relative max-w-5xl mx-auto px-4 sm:px-6 py-20 scroll-mt-20">
        <div className="glass-crystal rounded-3xl p-5 sm:p-8 md:p-12 text-white grid grid-cols-1 md:grid-cols-12 gap-6 md:gap-8 items-center">
          <div className="min-w-0 md:col-span-6">
            {/* TODO(WL-034, Brann): waitlist-mode card overline. "Not ready to try?" refers to the
                trial, which the waitlist build doesn't have, so it is hidden there; wording is Brann's. */}
            {!waitlistOnly && <div className="overline text-white/70 mb-3">{t("landing.waitlist_eyebrow")}</div>}
            <h2 className="font-display text-3xl sm:text-4xl tracking-tight break-words">{t("landing.waitlist_title")}</h2>
            <p className="text-white/70 mt-4">
              {t("landing.waitlist_sub")}
            </p>
          </div>
          <div className="min-w-0 md:col-span-6">
            {wlSent ? (
              <div ref={wlSuccessRef} tabIndex={-1} role="status" className="rounded-xl bg-emerald-400/10 border border-emerald-300/20 p-6 text-emerald-100 focus:outline-none" data-testid="waitlist-success">
                <div className="font-medium">{t("landing.waitlist_success_title")}</div>
                <div className="text-sm text-emerald-100/80 mt-1">{t("landing.waitlist_success_sub")}</div>
              </div>
            ) : (
              <form onSubmit={submitWaitlist} className="space-y-3" data-testid="waitlist-form">
                {/* EMP-WL-013: every field has a visible <label> (existing wording: the locale keys or the
                    old placeholder text). Placeholders and borders meet contrast (scripts/waitlist-contrast.js, WaitlistA11y.test.js). */}
                <div>
                  <label htmlFor="wl-email" className="block text-sm text-white/80 mb-1.5">{t("landing.waitlist_email")}</label>
                  <Input id="wl-email" ref={wlEmailRef} required type="email" autoComplete="email" placeholder={t("landing.waitlist_email_ph")} value={wlForm.email} onChange={(e) => setWlForm({ ...wlForm, email: e.target.value })} aria-invalid={wlError ? true : undefined} aria-describedby={wlError ? "wl-error" : undefined} className="bg-white/5 border-white/40 text-white placeholder:text-white/60" data-testid="waitlist-email" />
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label htmlFor="wl-name" className="block text-sm text-white/80 mb-1.5">{t("landing.waitlist_name_ph")}</label>
                    <Input id="wl-name" autoComplete="name" value={wlForm.name} onChange={(e) => setWlForm({ ...wlForm, name: e.target.value })} className="bg-white/5 border-white/40 text-white placeholder:text-white/60" data-testid="waitlist-name" />
                  </div>
                  <div>
                    <label htmlFor="wl-biz" className="block text-sm text-white/80 mb-1.5">{t("landing.waitlist_business_ph")}</label>
                    <Input id="wl-biz" autoComplete="organization" value={wlForm.business_name} onChange={(e) => setWlForm({ ...wlForm, business_name: e.target.value })} className="bg-white/5 border-white/40 text-white placeholder:text-white/60" data-testid="waitlist-biz" />
                  </div>
                </div>
                <div>
                  <label htmlFor="wl-industry" className="block text-sm text-white/80 mb-1.5">{t("landing.waitlist_industry")}</label>
                  <Input id="wl-industry" placeholder={t("landing.waitlist_industry_ph")} value={wlForm.industry} onChange={(e) => setWlForm({ ...wlForm, industry: e.target.value })} className="bg-white/5 border-white/40 text-white placeholder:text-white/60" data-testid="waitlist-industry" />
                </div>
                <div>
                  <label htmlFor="wl-note" className="block text-sm text-white/80 mb-1.5">{t("landing.waitlist_note_ph")}</label>
                  <Textarea id="wl-note" rows={2} value={wlForm.note} onChange={(e) => setWlForm({ ...wlForm, note: e.target.value })} className="bg-white/5 border-white/40 text-white placeholder:text-white/60" data-testid="waitlist-note" />
                </div>
                {/* Honeypot: hidden from people and screen readers; bots that fill it are silently ignored. */}
                <div aria-hidden="true" style={{ position: "absolute", left: "-10000px", width: 1, height: 1, overflow: "hidden" }}>
                  <label htmlFor="wl-website">Website</label>
                  <input id="wl-website" name="website" type="text" tabIndex={-1} autoComplete="off" value={wlForm.website} onChange={(e) => setWlForm({ ...wlForm, website: e.target.value })} />
                </div>
                <TurnstileWidget onToken={onTurnstileToken} />
                {wlError && <p id="wl-error" role="alert" className="text-sm text-rose-200" data-testid="waitlist-error">{wlError}</p>}
                {/* WL-001: the primary button. Not greyed out before an email is typed (looked broken);
                    the required email field still blocks an empty submit. */}
                <Button type="submit" disabled={wlSending} className="w-full h-11 bg-white text-black hover:bg-white/90" data-testid="waitlist-submit">
                  {wlSending ? t("landing.waitlist_adding") : t("landing.cta_join_waitlist")}
                </Button>
              </form>
            )}
          </div>
        </div>
      </section>

      {!industry && (<>
      {/* Final CTA (trial signup; hidden in the waitlist-only build). TODO(WL-001, Brann): waitlist copy. */}
      {!waitlistOnly && <section className="relative max-w-5xl mx-auto px-6 py-20 text-center text-white">
        <h2 className="font-display text-5xl md:text-6xl tracking-tight">{t("landing.final_title")}</h2>
        <p className="text-white/70 mt-5 max-w-xl mx-auto">{t("landing.final_sub")}</p>
        <Link to="/signup"><Button className="mt-8 h-12 px-7 bg-white text-black hover:bg-white/90 text-base" data-testid="final-cta-signup">{t("landing.hero_cta_free")}<ArrowRight className="h-4 w-4 ml-1.5" /></Button></Link>
      </section>}
      </>)}

      {/* Footer */}
      <footer className="relative border-t border-white/10 mt-10 text-white/60">
        <div className="max-w-7xl mx-auto px-6 py-10 grid grid-cols-2 md:grid-cols-4 gap-8 text-sm">
          <div>
            <Logo />
            <p className="mt-3 text-xs text-white/60 max-w-xs">{t("landing.footer_tagline")}</p>
          </div>
          <div>
            <div className="overline text-white/70 mb-3">{t("landing.footer_product")}</div>
            <ul className="space-y-2">
              <li><a href={industry ? "/#features" : "#features"} className="hover:text-white">{t("landing.nav_features")}</a></li>
              <li><a href={industry ? "/#industries" : "#industries"} className="hover:text-white">{t("landing.nav_industries")}</a></li>
              {!waitlistOnly && <li><Link to="/pricing" className="hover:text-white">{t("landing.nav_pricing")}</Link></li>}
              {!waitlistOnly && <li><a href="#demo" className="hover:text-white">{t("landing.nav_demo")}</a></li>}
            </ul>
          </div>
          <div>
            <div className="overline text-white/70 mb-3">{t("landing.footer_company")}</div>
            <ul className="space-y-2">
              <li><a href="#waitlist" className="hover:text-white">{t("landing.footer_early_access")}</a></li>
              <li><a href={`mailto:${CONTACT_EMAIL}`} className="hover:text-white">{t("landing.footer_contact")}</a></li>
            </ul>
          </div>
          <div>
            <div className="overline text-white/70 mb-3">{t("landing.footer_legal")}</div>
            <ul className="space-y-2">
              <li><Link to="/privacy" className="hover:text-white" data-testid="footer-privacy-link">{t("landing.footer_privacy")}</Link></li>
              <li><Link to="/terms" className="hover:text-white" data-testid="footer-terms-link">{t("landing.footer_terms")}</Link></li>
            </ul>
          </div>
        </div>
        <div className="max-w-7xl mx-auto px-6 pb-8 text-[11px] text-white/60">© {new Date().getFullYear()} {BRAND_NAME} · {t("landing.footer_rights")}</div>
      </footer>

      <Suspense fallback={null}>
        <LeadAssistant
          t={t}
          presetIndustry={industry ? industry.copy.formIndustry : ""}
          waitlistOnly={waitlistOnly}
          onShowPricing={mascotShowPricing}
          onShowForm={mascotShowForm}
        />
      </Suspense>
    </div>
  );
}

// Industry pages: plain list of the page's points (draft copy, see config/industries.js).
// The snowflake/flame glyph pair is HVAC iconography — only the HVAC page wears
// it; other industries get a neutral sparkle so shared chrome stays trade-neutral.
function IndustryPoints({ copy, slug }) {
  return (
    <div className="glass-crystal rounded-2xl p-6 text-white" data-testid="industry-points">
      <h2 className="font-display text-xl flex items-center gap-2">{slug === "hvac" ? (<><Snowflake className="hvac-cool h-5 w-5" aria-hidden /><Flame className="hvac-warm h-5 w-5" aria-hidden /></>) : (<Sparkles className="h-5 w-5" aria-hidden />)}{copy.pointsTitle}</h2>
      <ul className="mt-4 space-y-3">
        {copy.points.map((pt) => (
          <li key={pt} className="flex gap-2 text-white/80"><Check className="hvac-cool h-4 w-4 mt-1 shrink-0" aria-hidden />{pt}</li>
        ))}
      </ul>
    </div>
  );
}

function HeroPreview() {
  const { t } = useTranslation();
  return (
    <div className="glass-crystal rounded-2xl p-5 text-white" data-testid="hero-preview">
      <div className="flex items-center gap-2 text-xs text-white/60">
        <span className="h-2 w-2 rounded-full bg-emerald-400 inline-block" /> {t("landing.preview_online")}
      </div>
      <div className="mt-4 space-y-2">
        <div className="bg-white/10 rounded-2xl px-3 py-2 text-[13px] w-fit max-w-[85%]">{t("landing.preview_msg_1")}</div>
        <div className="bg-white text-black rounded-2xl px-3 py-2 text-[13px] w-fit max-w-[85%] ml-auto">{t("landing.preview_msg_2")}</div>
        <div className="bg-white/10 rounded-2xl px-3 py-2 text-[13px] w-fit max-w-[85%]">{t("landing.preview_msg_3")}</div>
        <div className="bg-white text-black rounded-2xl px-3 py-2 text-[13px] w-fit max-w-[85%] ml-auto">{t("landing.preview_msg_4")}</div>
        <div className="bg-white/10 rounded-2xl px-3 py-2 text-[13px] w-fit max-w-[85%]">{t("landing.preview_msg_5")}</div>
      </div>
      <div className="mt-5 flex items-center gap-2 border-t border-white/10 pt-4 text-xs">
        <Badge className="bg-rose-400/20 text-rose-200 border border-rose-300/30">🔥 {t("landing.preview_hot")}</Badge>
        <span className="text-white/60">{t("landing.preview_scored")}</span>
      </div>
    </div>
  );
}
