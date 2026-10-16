import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Logo } from "@/components/Logo";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { api, errMessage } from "@/lib/api";
import { toast } from "sonner";
import { Check, ArrowRight, ArrowLeft } from "lucide-react";

import { BRAND_NAME } from "@/config/brand";
const STEPS = [
  { key: "basics", title: "Basics", description: "Name, industry, a one-liner about your business." },
  { key: "contact", title: "Contact & web", description: "How customers reach you." },
  { key: "hours", title: "Hours & area", description: "When and where you operate." },
  { key: "ai", title: "AI employee", description: "Pick a tone — we'll do the rest." },
  { key: "wrapup", title: "Policies & emergencies", description: "Edge cases your AI should know about." },
];

const DEFAULT_HOURS = {
  mon: "09:00-17:00", tue: "09:00-17:00", wed: "09:00-17:00",
  thu: "09:00-17:00", fri: "09:00-17:00", sat: "closed", sun: "closed",
};

export default function Onboarding() {
  const [step, setStep] = useState(0);
  const [industries, setIndustries] = useState([]);
  const [loading, setLoading] = useState(false);
  const [form, setForm] = useState({
    name: "", industry_slug: "", description: "",
    contact_name: "", contact_email: "", contact_phone: "", website: "",
    address: { line1: "", city: "", state: "", postal_code: "", country: "US" },
    service_areas_text: "",
    hours: { ...DEFAULT_HOURS },
    emergency_hours: "", emergency_procedures: "", human_fallback_number: "",
    voicemail_prefs: "transcribe", communication_prefs: ["email", "sms"],
    ai_tone: "warm",
    policies: "", pricing_info: "", appointment_availability: "",
  });
  const nav = useNavigate();

  useEffect(() => {
    api.get("/industries", { params: { active_only: true } }).then((r) => {
      setIndustries(r.data);
    });
    api.get("/tenants/me").then((r) => {
      if (r.data?.onboarding_complete) nav("/app");
      if (r.data?.name) setForm((f) => ({ ...f, name: r.data.name }));
    }).catch(() => {});
  }, [nav]);

  const update = (patch) => setForm((f) => ({ ...f, ...patch }));
  const upd = (k) => (e) => update({ [k]: e.target?.value ?? e });

  const next = () => setStep((s) => Math.min(STEPS.length - 1, s + 1));
  const back = () => setStep((s) => Math.max(0, s - 1));

  const submit = async () => {
    setLoading(true);
    try {
      const payload = {
        name: form.name,
        industry_slug: form.industry_slug,
        description: form.description,
        contact_name: form.contact_name,
        contact_email: form.contact_email || null,
        contact_phone: form.contact_phone,
        website: form.website,
        social_links: {},
        address: form.address,
        service_areas: form.service_areas_text.split(",").map((x) => x.trim()).filter(Boolean),
        hours: form.hours,
        emergency_hours: form.emergency_hours,
        emergency_procedures: form.emergency_procedures,
        human_fallback_number: form.human_fallback_number,
        voicemail_prefs: form.voicemail_prefs,
        communication_prefs: form.communication_prefs,
        services: [], staff: [], faqs: [],
        pricing_info: form.pricing_info,
        policies: form.policies,
        appointment_availability: form.appointment_availability,
      };
      await api.post("/tenants/onboard", payload);
      // persist AI tone separately
      const tone = form.ai_tone === "warm" ? "Warm, professional, concise."
        : form.ai_tone === "formal" ? "Formal, precise, respectful."
        : form.ai_tone === "friendly" ? "Friendly, upbeat, casual."
        : "Reassuring, calm, no-nonsense.";
      await api.put("/tenants/me", { ai_employee: { name: "Alex", personality: tone, voice: "neutral", greeting: `Hi! Thanks for calling ${form.name}. How can I help?`, enabled: true } });
      toast.success(`Your ${BRAND_NAME} is live!`);
      nav("/app", { replace: true });
    } catch (err) {
      toast.error(errMessage(err));
    } finally {
      setLoading(false);
    }
  };

  const progress = ((step + 1) / STEPS.length) * 100;
  const isLast = step === STEPS.length - 1;

  const stepValid = () => {
    if (step === 0) return form.name && form.industry_slug;
    return true;
  };

  return (
    <div className="min-h-screen grid md:grid-cols-[1fr_minmax(0,2fr)]" data-testid="onboarding-page">
      <aside className="relative hidden md:block marketing-shell p-10 overflow-hidden">
        <div className="marketing-noise fixed inset-0 opacity-40" aria-hidden />
        <div className="relative h-full flex flex-col">
          <Logo variant="light" />
          <div className="mt-auto">
            <div className="overline text-white/50 mb-3">Step {step + 1} of {STEPS.length}</div>
            <h2 className="font-display text-4xl text-white tracking-tight max-w-sm">{STEPS[step].title}</h2>
            <p className="text-white/60 mt-3 max-w-sm">{STEPS[step].description}</p>

            <ol className="mt-10 space-y-3">
              {STEPS.map((s, i) => (
                <li key={s.key} className="flex items-center gap-3 text-sm">
                  <span className={`h-6 w-6 rounded-full grid place-items-center text-[11px] font-mono ${i < step ? "bg-emerald-400 text-black" : i === step ? "bg-white text-black" : "bg-white/10 text-white/60"}`}>
                    {i < step ? <Check className="h-3 w-3" /> : i + 1}
                  </span>
                  <span className={i === step ? "text-white" : "text-white/50"}>{s.title}</span>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </aside>

      <section className="p-6 md:p-12 overflow-y-auto">
        <div className="max-w-xl mx-auto">
          <div className="flex items-center justify-between mb-6">
            <Badge variant="outline" className="font-mono">{Math.round(progress)}%</Badge>
            <span className="text-xs text-muted-foreground">You can edit anything later.</span>
          </div>
          <Progress value={progress} className="h-1 mb-10" />

          {step === 0 && (
            <div className="space-y-6" data-testid="onboarding-step-basics">
              <div className="space-y-1.5">
                <Label>Business name</Label>
                <Input value={form.name} onChange={upd("name")} data-testid="onb-name-input" />
              </div>
              <div className="space-y-1.5">
                <Label>Industry</Label>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                  {industries.map((ind) => (
                    <button
                      type="button"
                      key={ind.slug}
                      onClick={() => update({ industry_slug: ind.slug })}
                      data-testid={`onb-industry-${ind.slug}`}
                      className={`text-left p-3 rounded-xl border transition-colors ${form.industry_slug === ind.slug ? "border-foreground bg-foreground text-background" : "border-border hover:bg-muted"}`}
                    >
                      <div className="text-[13px] font-medium">{ind.name}</div>
                      <div className={`text-[11px] mt-1 line-clamp-2 ${form.industry_slug === ind.slug ? "text-background/70" : "text-muted-foreground"}`}>{ind.description}</div>
                    </button>
                  ))}
                </div>
              </div>
              <div className="space-y-1.5">
                <Label>One-liner about your business</Label>
                <Textarea rows={3} value={form.description} onChange={upd("description")} placeholder="Family-owned HVAC serving metro Phoenix since 2008." data-testid="onb-description-input" />
              </div>
            </div>
          )}

          {step === 1 && (
            <div className="space-y-5" data-testid="onboarding-step-contact">
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-1.5">
                  <Label>Contact person</Label>
                  <Input value={form.contact_name} onChange={upd("contact_name")} data-testid="onb-contact-name" />
                </div>
                <div className="space-y-1.5">
                  <Label>Contact phone</Label>
                  <Input value={form.contact_phone} onChange={upd("contact_phone")} data-testid="onb-contact-phone" />
                </div>
                <div className="space-y-1.5">
                  <Label>Contact email</Label>
                  <Input type="email" value={form.contact_email} onChange={upd("contact_email")} data-testid="onb-contact-email" />
                </div>
                <div className="space-y-1.5">
                  <Label>Website</Label>
                  <Input value={form.website} onChange={upd("website")} placeholder="https://" data-testid="onb-website" />
                </div>
              </div>
              <div className="space-y-1.5">
                <Label>Street address</Label>
                <Input value={form.address.line1} onChange={(e) => update({ address: { ...form.address, line1: e.target.value } })} data-testid="onb-address-line1" />
              </div>
              <div className="grid grid-cols-3 gap-4">
                <div className="space-y-1.5">
                  <Label>City</Label>
                  <Input value={form.address.city} onChange={(e) => update({ address: { ...form.address, city: e.target.value } })} data-testid="onb-city" />
                </div>
                <div className="space-y-1.5">
                  <Label>State / Region</Label>
                  <Input value={form.address.state} onChange={(e) => update({ address: { ...form.address, state: e.target.value } })} data-testid="onb-state" />
                </div>
                <div className="space-y-1.5">
                  <Label>Postal code</Label>
                  <Input value={form.address.postal_code} onChange={(e) => update({ address: { ...form.address, postal_code: e.target.value } })} data-testid="onb-postal" />
                </div>
              </div>
            </div>
          )}

          {step === 2 && (
            <div className="space-y-5" data-testid="onboarding-step-hours">
              <div className="space-y-1.5">
                <Label>Weekly hours</Label>
                <div className="rounded-xl border border-border divide-y">
                  {Object.keys(form.hours).map((day) => (
                    <div key={day} className="flex items-center gap-3 px-3 py-2.5">
                      <div className="w-16 text-sm capitalize">{day}</div>
                      <Input
                        className="flex-1 h-9"
                        value={form.hours[day]}
                        onChange={(e) => update({ hours: { ...form.hours, [day]: e.target.value } })}
                        placeholder="09:00-17:00 or closed"
                        data-testid={`onb-hours-${day}`}
                      />
                    </div>
                  ))}
                </div>
              </div>
              <div className="space-y-1.5">
                <Label>Service area (comma-separated)</Label>
                <Input value={form.service_areas_text} onChange={upd("service_areas_text")} placeholder="Phoenix, Scottsdale, Tempe" data-testid="onb-service-areas" />
              </div>
              <div className="space-y-1.5">
                <Label>Emergency hours (optional)</Label>
                <Input value={form.emergency_hours} onChange={upd("emergency_hours")} placeholder="24/7 for no-heat emergencies" data-testid="onb-emergency-hours" />
              </div>
            </div>
          )}

          {step === 3 && (
            <div className="space-y-5" data-testid="onboarding-step-ai">
              <div className="space-y-1.5">
                <Label>AI employee tone</Label>
                <div className="grid grid-cols-2 gap-2">
                  {[
                    { k: "warm", t: "Warm & professional", d: "Default. Great for most trades." },
                    { k: "friendly", t: "Friendly & upbeat", d: "Light-touch, casual." },
                    { k: "formal", t: "Formal & precise", d: "Perfect for medical / legal." },
                    { k: "steady", t: "Calm & reassuring", d: "Best for emergency calls." },
                  ].map((o) => (
                    <button
                      key={o.k}
                      type="button"
                      onClick={() => update({ ai_tone: o.k })}
                      data-testid={`onb-tone-${o.k}`}
                      className={`text-left p-4 rounded-xl border ${form.ai_tone === o.k ? "border-foreground bg-foreground text-background" : "border-border hover:bg-muted"}`}
                    >
                      <div className="text-sm font-medium">{o.t}</div>
                      <div className={`text-[11px] mt-1 ${form.ai_tone === o.k ? "text-background/70" : "text-muted-foreground"}`}>{o.d}</div>
                    </button>
                  ))}
                </div>
              </div>
              <div className="space-y-1.5">
                <Label>Human fallback number</Label>
                <Input value={form.human_fallback_number} onChange={upd("human_fallback_number")} placeholder="+1 555 123 4567" data-testid="onb-fallback-number" />
                <p className="text-[11px] text-muted-foreground">Used when the AI can't resolve a call.</p>
              </div>
              <div className="space-y-1.5">
                <Label>Voicemail preference</Label>
                <div className="flex gap-2">
                  {["transcribe", "forward", "email"].map((v) => (
                    <button
                      key={v}
                      type="button"
                      onClick={() => update({ voicemail_prefs: v })}
                      data-testid={`onb-vm-${v}`}
                      className={`px-3 py-2 rounded-lg text-[13px] border ${form.voicemail_prefs === v ? "border-foreground bg-foreground text-background" : "border-border hover:bg-muted"}`}
                    >{v}</button>
                  ))}
                </div>
              </div>
            </div>
          )}

          {step === 4 && (
            <div className="space-y-5" data-testid="onboarding-step-wrapup">
              <div className="space-y-1.5">
                <Label>Emergency procedures</Label>
                <Textarea rows={3} value={form.emergency_procedures} onChange={upd("emergency_procedures")} placeholder="Gas smell → tell customer to leave the premises and call 911." data-testid="onb-emergency-proc" />
              </div>
              <div className="space-y-1.5">
                <Label>Policies (returns, cancellations, warranty)</Label>
                <Textarea rows={3} value={form.policies} onChange={upd("policies")} data-testid="onb-policies" />
              </div>
              <div className="space-y-1.5">
                <Label>Pricing notes</Label>
                <Textarea rows={2} value={form.pricing_info} onChange={upd("pricing_info")} placeholder="Diagnostic $89. Waived if repair booked." data-testid="onb-pricing" />
              </div>
              <div className="space-y-1.5">
                <Label>Typical appointment availability</Label>
                <Input value={form.appointment_availability} onChange={upd("appointment_availability")} placeholder="Same-day or next-day for existing customers" data-testid="onb-availability" />
              </div>
            </div>
          )}

          <div className="mt-10 flex items-center justify-between">
            <Button type="button" variant="ghost" onClick={back} disabled={step === 0 || loading} data-testid="onb-back-btn">
              <ArrowLeft className="h-4 w-4 mr-1" /> Back
            </Button>
            {isLast ? (
              <Button className="btn-tenant h-11 px-6" disabled={loading} onClick={submit} data-testid="onb-finish-btn">
                {loading ? "Finishing…" : "Finish & launch"}
              </Button>
            ) : (
              <Button className="btn-tenant h-11 px-6" disabled={!stepValid() || loading} onClick={next} data-testid="onb-next-btn">
                Continue <ArrowRight className="h-4 w-4 ml-1" />
              </Button>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
