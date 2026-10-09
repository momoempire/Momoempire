import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Logo } from "@/components/Logo";
import LanguageSwitcher from "@/components/LanguageSwitcher";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { api, errMessage } from "@/lib/api";
import { toast } from "sonner";
import DemoCall from "@/components/DemoCall";
import {
  ArrowRight, Bot, Target, Rocket, Sparkles, PhoneCall, UserPlus,
  Headphones, ChevronDown, Check,
} from "lucide-react";

const PILLAR_META = [
  { icon: Bot, titleKey: "landing.pillar_receptionist_title", descKey: "landing.pillar_receptionist_desc", testid: "pillar-receptionist" },
  { icon: Target, titleKey: "landing.pillar_sales_title", descKey: "landing.pillar_sales_desc", testid: "pillar-sales" },
  { icon: Rocket, titleKey: "landing.pillar_growth_title", descKey: "landing.pillar_growth_desc", testid: "pillar-growth" },
];

const INDUSTRY_KEYS = ["hvac", "dental", "legal", "salon"];

const STEP_META = [
  { icon: UserPlus, n: "01", titleKey: "landing.step_1_title", descKey: "landing.step_1_desc" },
  { icon: PhoneCall, n: "02", titleKey: "landing.step_2_title", descKey: "landing.step_2_desc" },
  { icon: Headphones, n: "03", titleKey: "landing.step_3_title", descKey: "landing.step_3_desc" },
];

export default function Landing() {
  const { t } = useTranslation();
  const [trial, setTrial] = useState(null);
  const [openFaq, setOpenFaq] = useState(0);
  const [wlForm, setWlForm] = useState({ email: "", name: "", business_name: "", industry: "", note: "" });
  const [wlSent, setWlSent] = useState(false);
  const [wlSending, setWlSending] = useState(false);

  useEffect(() => {
    api.get("/plans").then((r) => {
      const plan = (r.data || []).find((p) => p.key === "trial");
      setTrial(plan || null);
    }).catch(() => {});
  }, []);

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
    try {
      await api.post("/public/waitlist", wlForm);
      setWlSent(true); toast.success(t("landing.waitlist_thanks_toast"));
    } catch (err) { toast.error(errMessage(err)); }
    finally { setWlSending(false); }
  };

  const trialLimits = trial?.limits || {};
  const trialDays = trial?.trial_days || 60;
  const trialMinutes = trialLimits.ai_minutes || 50;
  const trialCalls = trialLimits.calls || 25;

  return (
    <div className="marketing-shell relative overflow-x-hidden" data-testid="landing-page">
      <div className="marketing-noise fixed inset-0 opacity-50" aria-hidden />

      {/* Nav */}
      <header className="sticky top-0 z-30 glass-crystal border-b border-white/10">
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          <Logo variant="light" />
          <nav className="hidden md:flex items-center gap-8 text-[13px] text-white/70">
            <a href="#features" className="hover:text-white">{t("landing.nav_features")}</a>
            <a href="#industries" className="hover:text-white">{t("landing.nav_industries")}</a>
            <a href="#how" className="hover:text-white">{t("landing.nav_how")}</a>
            <a href="#demo" className="hover:text-white">{t("landing.nav_demo")}</a>
            <Link to="/pricing" className="hover:text-white">{t("landing.nav_pricing")}</Link>
            <a href="#faq" className="hover:text-white">{t("landing.nav_faq")}</a>
          </nav>
          <div className="flex items-center gap-2">
            <LanguageSwitcher compact />
            <Link to="/login"><Button variant="ghost" className="text-white hover:bg-white/10" data-testid="landing-login-btn">{t("common.login")}</Button></Link>
            <Link to="/signup"><Button className="bg-white text-black hover:bg-white/90" data-testid="landing-signup-btn">{t("landing.cta_trial")}<ArrowRight className="h-4 w-4 ml-1" /></Button></Link>
          </div>
        </div>
      </header>

      {/* Hero */}
      <section className="relative max-w-7xl mx-auto px-6 pt-20 pb-20">
        <div className="grid grid-cols-12 gap-6 items-start">
          <div className="col-span-12 lg:col-span-7">
            <Badge className="bg-white/10 text-white/80 border border-white/15 mb-5 hover:bg-white/10">
              <Sparkles className="h-3 w-3 mr-1.5" /> {t("landing.badge_platform")}
            </Badge>
            <h1 className="font-display text-5xl md:text-7xl leading-[0.95] tracking-tight text-white">
              {t("landing.hero_title_lead")} <span className="text-white/60">{t("landing.hero_title_trail")}</span>
            </h1>
            <p className="mt-6 text-white/70 text-lg max-w-xl">
              {t("landing.hero_sub")}
            </p>
            <div className="mt-8 flex flex-wrap gap-3 items-center">
              <Link to="/signup">
                <Button className="h-12 px-6 bg-white text-black hover:bg-white/90 text-base" data-testid="hero-cta-signup">
                  {t("landing.hero_cta_free")}<ArrowRight className="h-4 w-4 ml-1.5" />
                </Button>
              </Link>
              <a href="#demo"><Button variant="ghost" className="h-12 px-5 text-white hover:bg-white/10" data-testid="hero-cta-demo">{t("landing.hero_cta_demo")}</Button></a>
            </div>
            {trial && (
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
          <div className="col-span-12 lg:col-span-5">
            <HeroPreview />
          </div>
        </div>
      </section>

      {/* Features */}
      <section id="features" className="relative max-w-7xl mx-auto px-6 py-20">
        <div className="overline text-white/60 mb-3">{t("landing.pillars_eyebrow")}</div>
        <h2 className="font-display text-4xl md:text-5xl text-white tracking-tight max-w-2xl">{t("landing.pillars_title")}</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-5 mt-10">
          {PILLAR_META.map((p) => (
            <div key={p.testid} className="glass-crystal rounded-2xl p-6 text-white" data-testid={p.testid}>
              <div className="h-10 w-10 rounded-xl bg-white/10 border border-white/10 grid place-items-center mb-5"><p.icon className="h-5 w-5" /></div>
              <h3 className="font-medium text-xl">{t(p.titleKey)}</h3>
              <p className="text-white/70 text-sm mt-2 leading-relaxed">{t(p.descKey)}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Industries */}
      <section id="industries" className="relative max-w-7xl mx-auto px-6 py-20">
        <div className="overline text-white/60 mb-3">{t("landing.industries_eyebrow")}</div>
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

      {/* How it works */}
      <section id="how" className="relative max-w-7xl mx-auto px-6 py-20">
        <div className="overline text-white/60 mb-3">{t("landing.how_eyebrow")}</div>
        <h2 className="font-display text-4xl md:text-5xl text-white tracking-tight max-w-2xl">{t("landing.how_title")}</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-5 mt-10">
          {STEP_META.map((s) => (
            <div key={s.n} className="glass-crystal rounded-2xl p-7 text-white" data-testid={`step-${s.n}`}>
              <div className="font-mono text-white/40 text-sm">{s.n}</div>
              <div className="h-10 w-10 rounded-xl bg-white/10 border border-white/10 grid place-items-center mt-3 mb-4"><s.icon className="h-5 w-5" /></div>
              <h3 className="font-medium text-xl">{t(s.titleKey)}</h3>
              <p className="text-white/70 text-sm mt-2 leading-relaxed">{t(s.descKey)}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Live demo */}
      <section id="demo" className="relative max-w-7xl mx-auto px-6 py-20">
        <div className="grid grid-cols-12 gap-6 items-start">
          <div className="col-span-12 lg:col-span-5">
            <div className="overline text-white/60 mb-3">{t("landing.demo_section_eyebrow")}</div>
            <h2 className="font-display text-4xl md:text-5xl text-white tracking-tight whitespace-pre-line">{t("landing.demo_section_title")}</h2>
            <p className="text-white/70 mt-5 max-w-md">
              {t("landing.demo_section_sub")}
            </p>
            <ul className="mt-6 space-y-2 text-sm text-white/80">
              <li className="flex gap-2"><Check className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />{t("landing.demo_bullet_1")}</li>
              <li className="flex gap-2"><Check className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />{t("landing.demo_bullet_2")}</li>
              <li className="flex gap-2"><Check className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />{t("landing.demo_bullet_3")}</li>
            </ul>
          </div>
          <div className="col-span-12 lg:col-span-7">
            <DemoCall />
          </div>
        </div>
      </section>

      {/* FAQ */}
      <section id="faq" className="relative max-w-4xl mx-auto px-6 py-20">
        <div className="overline text-white/60 mb-3">{t("landing.faq_eyebrow")}</div>
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

      {/* Waitlist */}
      <section id="waitlist" className="relative max-w-5xl mx-auto px-6 py-20">
        <div className="glass-crystal rounded-3xl p-8 md:p-12 text-white grid grid-cols-12 gap-8 items-center">
          <div className="col-span-12 md:col-span-6">
            <div className="overline text-white/60 mb-3">{t("landing.waitlist_eyebrow")}</div>
            <h2 className="font-display text-4xl tracking-tight">{t("landing.waitlist_title")}</h2>
            <p className="text-white/70 mt-4">
              {t("landing.waitlist_sub")}
            </p>
          </div>
          <div className="col-span-12 md:col-span-6">
            {wlSent ? (
              <div className="rounded-xl bg-emerald-400/10 border border-emerald-300/20 p-6 text-emerald-100" data-testid="waitlist-success">
                <div className="font-medium">{t("landing.waitlist_success_title")}</div>
                <div className="text-sm text-emerald-100/80 mt-1">{t("landing.waitlist_success_sub")}</div>
              </div>
            ) : (
              <form onSubmit={submitWaitlist} className="space-y-3" data-testid="waitlist-form">
                <Input required type="email" placeholder={t("landing.waitlist_email_ph")} value={wlForm.email} onChange={(e) => setWlForm({ ...wlForm, email: e.target.value })} className="bg-white/5 border-white/20 text-white placeholder:text-white/40" data-testid="waitlist-email" />
                <div className="grid grid-cols-2 gap-3">
                  <Input placeholder={t("landing.waitlist_name_ph")} value={wlForm.name} onChange={(e) => setWlForm({ ...wlForm, name: e.target.value })} className="bg-white/5 border-white/20 text-white placeholder:text-white/40" data-testid="waitlist-name" />
                  <Input placeholder={t("landing.waitlist_business_ph")} value={wlForm.business_name} onChange={(e) => setWlForm({ ...wlForm, business_name: e.target.value })} className="bg-white/5 border-white/20 text-white placeholder:text-white/40" data-testid="waitlist-biz" />
                </div>
                <Input placeholder={t("landing.waitlist_industry_ph")} value={wlForm.industry} onChange={(e) => setWlForm({ ...wlForm, industry: e.target.value })} className="bg-white/5 border-white/20 text-white placeholder:text-white/40" data-testid="waitlist-industry" />
                <Textarea rows={2} placeholder={t("landing.waitlist_note_ph")} value={wlForm.note} onChange={(e) => setWlForm({ ...wlForm, note: e.target.value })} className="bg-white/5 border-white/20 text-white placeholder:text-white/40" data-testid="waitlist-note" />
                <Button type="submit" disabled={wlSending || !wlForm.email} className="w-full h-11 bg-white text-black hover:bg-white/90" data-testid="waitlist-submit">
                  {wlSending ? t("landing.waitlist_adding") : t("landing.waitlist_submit")}
                </Button>
              </form>
            )}
          </div>
        </div>
      </section>

      {/* Final CTA */}
      <section className="relative max-w-5xl mx-auto px-6 py-20 text-center text-white">
        <h2 className="font-display text-5xl md:text-6xl tracking-tight">{t("landing.final_title")}</h2>
        <p className="text-white/70 mt-5 max-w-xl mx-auto">{t("landing.final_sub")}</p>
        <Link to="/signup"><Button className="mt-8 h-12 px-7 bg-white text-black hover:bg-white/90 text-base" data-testid="final-cta-signup">{t("landing.hero_cta_free")}<ArrowRight className="h-4 w-4 ml-1.5" /></Button></Link>
      </section>

      {/* Footer */}
      <footer className="relative border-t border-white/10 mt-10 text-white/60">
        <div className="max-w-7xl mx-auto px-6 py-10 grid grid-cols-2 md:grid-cols-4 gap-8 text-sm">
          <div>
            <Logo variant="light" />
            <p className="mt-3 text-xs text-white/50 max-w-xs">{t("landing.footer_tagline")}</p>
          </div>
          <div>
            <div className="overline text-white/50 mb-3">{t("landing.footer_product")}</div>
            <ul className="space-y-2">
              <li><a href="#features" className="hover:text-white">{t("landing.nav_features")}</a></li>
              <li><a href="#industries" className="hover:text-white">{t("landing.nav_industries")}</a></li>
              <li><Link to="/pricing" className="hover:text-white">{t("landing.nav_pricing")}</Link></li>
              <li><a href="#demo" className="hover:text-white">{t("landing.nav_demo")}</a></li>
            </ul>
          </div>
          <div>
            <div className="overline text-white/50 mb-3">{t("landing.footer_company")}</div>
            <ul className="space-y-2">
              <li><a href="#waitlist" className="hover:text-white">{t("landing.footer_early_access")}</a></li>
              <li><a href="mailto:hello@aioffice.io" className="hover:text-white">{t("landing.footer_contact")}</a></li>
            </ul>
          </div>
          <div>
            <div className="overline text-white/50 mb-3">{t("landing.footer_legal")}</div>
            <ul className="space-y-2">
              <li><Link to="/privacy" className="hover:text-white" data-testid="footer-privacy-link">{t("landing.footer_privacy")}</Link></li>
              <li><Link to="/terms" className="hover:text-white" data-testid="footer-terms-link">{t("landing.footer_terms")}</Link></li>
            </ul>
          </div>
        </div>
        <div className="max-w-7xl mx-auto px-6 pb-8 text-[11px] text-white/40">© {new Date().getFullYear()} AI Office · {t("landing.footer_rights")}</div>
      </footer>
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
