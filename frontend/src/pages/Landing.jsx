import { useEffect, useRef, useState } from "react";
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
import { isWaitlistOnly } from "@/lib/waitlistMode";
import {
  ArrowRight, Bot, Target, Rocket, Sparkles, PhoneCall, UserPlus,
  Headphones, ChevronDown, Check,
} from "lucide-react";

const PILLARS = [
  { icon: Bot, title: "AI receptionist", desc: "Answers calls and texts 24/7, books appointments into your calendar, hands off to a human when needed. Trained on your services and prices.", testid: "pillar-receptionist" },
  { icon: Target, title: "Sales intelligence", desc: "Every call auto-scored hot / warm / cold with the reason. Objection playbook. Discount guardrails. Follow-up cadence that never misses a warm lead.", testid: "pillar-sales" },
  { icon: Rocket, title: "Growth autopilot", desc: "Weekly digest, review requests, win-back campaigns, referral tracking, service-area heatmap, and a one-line website widget — all running without you.", testid: "pillar-growth" },
];

const INDUSTRIES = [
  { key: "hvac",    label: "HVAC",   example: "Service calls, emergency dispatch, maintenance plans",       bullets: ["Books same-day AC calls", "Quotes tune-ups on the spot", "Escalates emergencies to on-call"] },
  { key: "dental",  label: "Dental", example: "New-patient intake, insurance checks, hygiene reminders",    bullets: ["Verifies insurance", "Books hygiene appointments", "Sends 24h reminders"] },
  { key: "legal",   label: "Legal",  example: "Intake, consult scheduling, matter triage",                  bullets: ["Qualifies by practice area", "Books free consultations", "Captures matter details"] },
  { key: "salon",   label: "Salon",  example: "Stylist booking, service questions, walk-in handling",       bullets: ["Books per stylist", "Explains service pricing", "Captures first-timer discount leads"] },
];

const STEPS = [
  { icon: UserPlus, n: "01", title: "Sign up", desc: "Create your workspace. Pick an industry — we seed starter services, FAQs, and an AI persona in 60 seconds." },
  { icon: PhoneCall, n: "02", title: "Connect your phone", desc: "Point your business line to our Twilio number (or get one from us). SMS and calls start flowing in instantly." },
  { icon: Headphones, n: "03", title: "The AI starts answering", desc: "Every call, every text — the AI handles it on-brand, books jobs, and surfaces hot leads to your dashboard." },
];

const FAQS = [
  { q: "Will this replace my staff?", a: "No. It replaces the voicemail and the missed-call black hole. Think of it as the receptionist you can afford to have 24/7 — human staff still handle on-site work and the calls the AI escalates." },
  { q: "What happens if the AI messes up?", a: "Three layers of safety: it never makes commitments outside your knowledge base, every call is transcribed so you can audit, and you set guardrails (discount caps, objection script, persona depth). You can also take over any live call from the dashboard." },
  { q: "Can I cancel anytime?", a: "Yes. Monthly plans — cancel in-app, access runs through the end of the paid period. No calls to a rep, no retention tricks." },
  { q: "What about my customer data?", a: "Multi-tenant isolation on day one. We don't sell data. Export or delete anything on request. Full privacy policy linked below." },
  { q: "Do I need a Twilio account?", a: "No — you can bring your own, or we provision a number for you inside your workspace. Either works." },
  { q: "What languages does it speak?", a: "Primarily English today. Other languages are coming — join the waitlist and tell us which." },
];

export default function Landing() {
  const { t: tCurrent, i18n } = useTranslation();
  // EMP-WL-016: the waitlist page is English-only for now (most of its copy isn't translated), so
  // its few translated labels stay English too, even for a Spanish browser or a saved "es" choice.
  const t = isWaitlistOnly() ? i18n.getFixedT("en") : tCurrent;
  const [trial, setTrial] = useState(null);
  const [openFaq, setOpenFaq] = useState(0);
  const [wlForm, setWlForm] = useState({ email: "", name: "", business_name: "", industry: "", note: "" });
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

  useEffect(() => {
    if (isWaitlistOnly()) return; // no /api/plans call in the waitlist-only build
    api.get("/plans").then((r) => {
      const t = (r.data || []).find((p) => p.key === "trial");
      setTrial(t || null);
    }).catch(() => {});
  }, []);

  const submitWaitlist = async (e) => {
    e.preventDefault();
    setWlSending(true);
    setWlError("");
    try {
      await api.post("/public/waitlist", wlForm);
      setWlSent(true); toast.success("You're on the list.");
    } catch (err) {
      // EMP-WL-013: show the error next to the form (announced, linked to the email field)
      // instead of only in a toast.
      setWlError(errMessage(err));
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
    <div className="marketing-shell relative overflow-x-hidden" data-testid="landing-page">
      <div className="marketing-noise fixed inset-0 opacity-50" aria-hidden />

      {/* Nav */}
      <header className="sticky top-0 z-30 glass-crystal border-b border-white/10">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between gap-3">
          <Logo variant="light" />
          <nav className="hidden md:flex items-center gap-8 text-[13px] text-white/70">
            <a href="#features" className="hover:text-white">Features</a>
            <a href="#industries" className="hover:text-white">Industries</a>
            {!waitlistOnly && <a href="#how" className="hover:text-white">How it works</a>}
            {!waitlistOnly && <a href="#demo" className="hover:text-white">Live demo</a>}
            {!waitlistOnly && <Link to="/pricing" className="hover:text-white">Pricing</Link>}
            <a href="#faq" className="hover:text-white">FAQ</a>
          </nav>
          <div className="flex items-center gap-2 shrink-0">
            {/* EMP-WL-016: the waitlist page is English-only for now, so no EN/ES switcher there. */}
            {!waitlistOnly && <LanguageSwitcher compact />}
            {/* EMP-WL-034: waitlist-only header CTA (existing "Join waitlist" label). */}
            {waitlistOnly && (
              <a href="#waitlist" onClick={goToWaitlist} data-testid="header-cta-waitlist">
                <Button className="bg-white text-black hover:bg-white/90">{t("landing.cta_join_waitlist")}<ArrowRight className="h-4 w-4 ml-1" /></Button>
              </a>
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
              <Sparkles className="h-3 w-3 mr-1.5" /> AI Office Platform
            </Badge>
            <h1 className="font-display text-4xl sm:text-5xl md:text-7xl leading-[0.95] tracking-tight text-white break-words">
              We build you an AI employee <span className="text-white/60">and a digital office.</span>
            </h1>
            <p className="mt-6 text-white/70 text-lg max-w-xl">
              Your business never misses a call again. The AI answers, books jobs, follows up with warm leads,
              and brings you a weekly summary — on autopilot, in your voice.
            </p>
            {/* EMP-WL-034 / WL-001: waitlist-only hero CTA is the primary button (existing label). */}
            {waitlistOnly && (
              <div className="mt-8">
                <a href="#waitlist" onClick={goToWaitlist} data-testid="hero-cta-waitlist">
                  <Button className="h-12 px-6 bg-white text-black hover:bg-white/90 text-base">
                    {t("landing.cta_join_waitlist")}<ArrowRight className="h-4 w-4 ml-1.5" />
                  </Button>
                </a>
              </div>
            )}
            {!waitlistOnly && <div className="mt-8 flex flex-wrap gap-3 items-center">
              <Link to="/signup">
                <Button className="h-12 px-6 bg-white text-black hover:bg-white/90 text-base" data-testid="hero-cta-signup">
                  Try it free<ArrowRight className="h-4 w-4 ml-1.5" />
                </Button>
              </Link>
              <a href="#demo"><Button variant="ghost" className="h-12 px-5 text-white hover:bg-white/10" data-testid="hero-cta-demo">Try the live demo →</Button></a>
            </div>}
            {trial && !waitlistOnly && (
              <p className="mt-4 text-[13px] text-white/60" data-testid="hero-trial-copy">
                Free trial: <strong className="text-white">{trialDays} days</strong>, {trialMinutes} AI minutes,
                {" "}{trialCalls} calls. No credit card.
              </p>
            )}
          </div>
          <div className="min-w-0 lg:col-span-5">
            <HeroPreview />
          </div>
        </div>
      </section>

      {/* Features */}
      <section id="features" className="relative max-w-7xl mx-auto px-6 py-20">
        <div className="overline text-white/70 mb-3">Three pillars</div>
        <h2 className="font-display text-4xl md:text-5xl text-white tracking-tight max-w-2xl">An entire office in one place.</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-5 mt-10">
          {PILLARS.map((p) => (
            <div key={p.title} className="glass-crystal rounded-2xl p-6 text-white" data-testid={p.testid}>
              <div className="h-10 w-10 rounded-xl bg-white/10 border border-white/10 grid place-items-center mb-5"><p.icon className="h-5 w-5" /></div>
              <h3 className="font-medium text-xl">{p.title}</h3>
              <p className="text-white/70 text-sm mt-2 leading-relaxed">{p.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Industries */}
      <section id="industries" className="relative max-w-7xl mx-auto px-6 py-20">
        <div className="overline text-white/70 mb-3">Not one-size-fits-all</div>
        <h2 className="font-display text-4xl md:text-5xl text-white tracking-tight max-w-2xl">Same brain, your vocabulary.</h2>
        <p className="text-white/70 mt-4 max-w-xl">The AI adapts to your industry out of the box — jargon, service names, pricing patterns, intake flow.</p>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mt-10">
          {INDUSTRIES.map((i) => (
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
        <div className="overline text-white/70 mb-3">How it works</div>
        <h2 className="font-display text-4xl md:text-5xl text-white tracking-tight max-w-2xl">Live in under five minutes.</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-5 mt-10">
          {STEPS.map((s) => (
            <div key={s.n} className="glass-crystal rounded-2xl p-7 text-white" data-testid={`step-${s.n}`}>
              <div className="font-mono text-white/60 text-sm">{s.n}</div>
              <div className="h-10 w-10 rounded-xl bg-white/10 border border-white/10 grid place-items-center mt-3 mb-4"><s.icon className="h-5 w-5" /></div>
              <h3 className="font-medium text-xl">{s.title}</h3>
              <p className="text-white/70 text-sm mt-2 leading-relaxed">{s.desc}</p>
            </div>
          ))}
        </div>
      </section>}

      {/* Live demo (hidden in the waitlist-only build: /api/public/demo/* returns 404 there) */}
      {!waitlistOnly && <section id="demo" className="relative max-w-7xl mx-auto px-6 py-20">
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
          <div className="min-w-0 lg:col-span-5">
            <div className="overline text-white/70 mb-3">See it, don't imagine it</div>
            <h2 className="font-display text-4xl md:text-5xl text-white tracking-tight">Talk to the AI<br />right now.</h2>
            <p className="text-white/70 mt-5 max-w-md">
              Pick an industry, type (or tap the mic) and have an actual conversation.
              No signup. No credit card. 10 turns per demo.
            </p>
            <ul className="mt-6 space-y-2 text-sm text-white/80">
              <li className="flex gap-2"><Check className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />Same AI brain the real workspace runs on</li>
              <li className="flex gap-2"><Check className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />Industry-shaped services, pricing, and FAQs</li>
              <li className="flex gap-2"><Check className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />Try tough questions — "I got a cheaper quote," "I need you today"</li>
            </ul>
          </div>
          <div className="min-w-0 lg:col-span-7">
            <DemoCall />
          </div>
        </div>
      </section>}

      {/* FAQ */}
      <section id="faq" className="relative max-w-4xl mx-auto px-6 py-20">
        <div className="overline text-white/70 mb-3">Frequently asked</div>
        <h2 className="font-display text-4xl md:text-5xl text-white tracking-tight">The honest answers.</h2>
        <div className="mt-10 divide-y divide-white/10 border-y border-white/10">
          {FAQS.map((f, i) => (
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
      {/* EMP-WL-006: one column on phones. The old grid-cols-12 + gap-8 kept 11 column gaps
          (352 px) even with col-span-12, which pushed the form ~43 px past a 390 px screen. */}
      <section id="waitlist" className="relative max-w-5xl mx-auto px-4 sm:px-6 py-20 scroll-mt-20">
        <div className="glass-crystal rounded-3xl p-5 sm:p-8 md:p-12 text-white grid grid-cols-1 md:grid-cols-12 gap-6 md:gap-8 items-center">
          <div className="min-w-0 md:col-span-6">
            {/* TODO(WL-034, Brann): waitlist-mode card overline. "Not ready to try?" refers to the
                trial, which the waitlist build doesn't have, so it is hidden there; wording is Brann's. */}
            {!waitlistOnly && <div className="overline text-white/70 mb-3">Not ready to try?</div>}
            <h2 className="font-display text-3xl sm:text-4xl tracking-tight break-words">Join the early-access list.</h2>
            <p className="text-white/70 mt-4">
              We'll send a short note when seats open in your industry — and we'll never spam you.
            </p>
          </div>
          <div className="min-w-0 md:col-span-6">
            {wlSent ? (
              <div ref={wlSuccessRef} tabIndex={-1} role="status" className="rounded-xl bg-emerald-400/10 border border-emerald-300/20 p-6 text-emerald-100 focus:outline-none" data-testid="waitlist-success">
                <div className="font-medium">You're on the list.</div>
                <div className="text-sm text-emerald-100/80 mt-1">We'll reach out as soon as your industry opens.</div>
              </div>
            ) : (
              <form onSubmit={submitWaitlist} className="space-y-3" data-testid="waitlist-form">
                {/* EMP-WL-013: every field has a visible <label> (existing wording: the locale keys or the
                    old placeholder text). Placeholders and borders meet contrast (scripts/waitlist-contrast.js, WaitlistA11y.test.js). */}
                <div>
                  <label htmlFor="wl-email" className="block text-sm text-white/80 mb-1.5">{t("landing.waitlist_email")}</label>
                  <Input id="wl-email" ref={wlEmailRef} required type="email" autoComplete="email" placeholder="you@yourbusiness.com" value={wlForm.email} onChange={(e) => setWlForm({ ...wlForm, email: e.target.value })} aria-invalid={wlError ? true : undefined} aria-describedby={wlError ? "wl-error" : undefined} className="bg-white/5 border-white/40 text-white placeholder:text-white/60" data-testid="waitlist-email" />
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label htmlFor="wl-name" className="block text-sm text-white/80 mb-1.5">Your name</label>
                    <Input id="wl-name" autoComplete="name" value={wlForm.name} onChange={(e) => setWlForm({ ...wlForm, name: e.target.value })} className="bg-white/5 border-white/40 text-white placeholder:text-white/60" data-testid="waitlist-name" />
                  </div>
                  <div>
                    <label htmlFor="wl-biz" className="block text-sm text-white/80 mb-1.5">Business</label>
                    <Input id="wl-biz" autoComplete="organization" value={wlForm.business_name} onChange={(e) => setWlForm({ ...wlForm, business_name: e.target.value })} className="bg-white/5 border-white/40 text-white placeholder:text-white/60" data-testid="waitlist-biz" />
                  </div>
                </div>
                <div>
                  <label htmlFor="wl-industry" className="block text-sm text-white/80 mb-1.5">{t("landing.waitlist_industry")}</label>
                  <Input id="wl-industry" placeholder="Industry (e.g. HVAC, dental)" value={wlForm.industry} onChange={(e) => setWlForm({ ...wlForm, industry: e.target.value })} className="bg-white/5 border-white/40 text-white placeholder:text-white/60" data-testid="waitlist-industry" />
                </div>
                <div>
                  <label htmlFor="wl-note" className="block text-sm text-white/80 mb-1.5">Anything specific you'd want it to do?</label>
                  <Textarea id="wl-note" rows={2} value={wlForm.note} onChange={(e) => setWlForm({ ...wlForm, note: e.target.value })} className="bg-white/5 border-white/40 text-white placeholder:text-white/60" data-testid="waitlist-note" />
                </div>
                {wlError && <p id="wl-error" role="alert" className="text-sm text-rose-200" data-testid="waitlist-error">{wlError}</p>}
                {/* WL-001: the primary button. Not greyed out before an email is typed (looked broken);
                    the required email field still blocks an empty submit. */}
                <Button type="submit" disabled={wlSending} className="w-full h-11 bg-white text-black hover:bg-white/90" data-testid="waitlist-submit">
                  {wlSending ? "Adding…" : t("landing.cta_join_waitlist")}
                </Button>
              </form>
            )}
          </div>
        </div>
      </section>

      {/* Final CTA (trial signup; hidden in the waitlist-only build). TODO(WL-001, Brann): waitlist copy. */}
      {!waitlistOnly && <section className="relative max-w-5xl mx-auto px-6 py-20 text-center text-white">
        <h2 className="font-display text-5xl md:text-6xl tracking-tight">Your AI office is 60 seconds away.</h2>
        <p className="text-white/70 mt-5 max-w-xl mx-auto">Spin up your workspace, pick an industry, and the AI employee is on the clock.</p>
        <Link to="/signup"><Button className="mt-8 h-12 px-7 bg-white text-black hover:bg-white/90 text-base" data-testid="final-cta-signup">Try it free<ArrowRight className="h-4 w-4 ml-1.5" /></Button></Link>
      </section>}

      {/* Footer */}
      <footer className="relative border-t border-white/10 mt-10 text-white/60">
        <div className="max-w-7xl mx-auto px-6 py-10 grid grid-cols-2 md:grid-cols-4 gap-8 text-sm">
          <div>
            <Logo variant="light" />
            <p className="mt-3 text-xs text-white/60 max-w-xs">Done-for-you AI office platform for service businesses.</p>
          </div>
          <div>
            <div className="overline text-white/70 mb-3">Product</div>
            <ul className="space-y-2">
              <li><a href="#features" className="hover:text-white">Features</a></li>
              <li><a href="#industries" className="hover:text-white">Industries</a></li>
              {!waitlistOnly && <li><Link to="/pricing" className="hover:text-white">Pricing</Link></li>}
              {!waitlistOnly && <li><a href="#demo" className="hover:text-white">Live demo</a></li>}
            </ul>
          </div>
          <div>
            <div className="overline text-white/70 mb-3">Company</div>
            <ul className="space-y-2">
              <li><a href="#waitlist" className="hover:text-white">Early access</a></li>
              <li><a href="mailto:hello@aioffice.io" className="hover:text-white">Contact</a></li>
            </ul>
          </div>
          <div>
            <div className="overline text-white/70 mb-3">Legal</div>
            <ul className="space-y-2">
              <li><Link to="/privacy" className="hover:text-white" data-testid="footer-privacy-link">Privacy Policy</Link></li>
              <li><Link to="/terms" className="hover:text-white" data-testid="footer-terms-link">Terms of Service</Link></li>
            </ul>
          </div>
        </div>
        <div className="max-w-7xl mx-auto px-6 pb-8 text-[11px] text-white/60">© {new Date().getFullYear()} AI Office · All rights reserved.</div>
      </footer>
    </div>
  );
}

function HeroPreview() {
  // Minimal, non-chat visual mock so hero isn't static text.
  return (
    <div className="glass-crystal rounded-2xl p-5 text-white" data-testid="hero-preview">
      <div className="flex items-center gap-2 text-xs text-white/60">
        <span className="h-2 w-2 rounded-full bg-emerald-400 inline-block" /> AI online
      </div>
      <div className="mt-4 space-y-2">
        <div className="bg-white/10 rounded-2xl px-3 py-2 text-[13px] w-fit max-w-[85%]">Comfort Pros HVAC, this is Alex — how can I help?</div>
        <div className="bg-white text-black rounded-2xl px-3 py-2 text-[13px] w-fit max-w-[85%] ml-auto">My AC isn't cooling and the house is 85°</div>
        <div className="bg-white/10 rounded-2xl px-3 py-2 text-[13px] w-fit max-w-[85%]">That's uncomfortable — I can get a tech out between 2–4pm today. What's the address?</div>
        <div className="bg-white text-black rounded-2xl px-3 py-2 text-[13px] w-fit max-w-[85%] ml-auto">423 Oak St, Austin</div>
        <div className="bg-white/10 rounded-2xl px-3 py-2 text-[13px] w-fit max-w-[85%]">Booked. We'll text when the tech is 30 minutes out. Service call is $89 plus parts.</div>
      </div>
      <div className="mt-5 flex items-center gap-2 border-t border-white/10 pt-4 text-xs">
        <Badge className="bg-rose-400/20 text-rose-200 border border-rose-300/30">🔥 hot</Badge>
        <span className="text-white/60">Lead scored and sent to CRM</span>
      </div>
    </div>
  );
}
