import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import "@/i18n/estimator";
import { api, errMessage } from "@/lib/api";
import { Logo } from "@/components/Logo";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import TurnstileWidget from "@/components/TurnstileWidget";
import { ESTIMATOR_RULES } from "@/config/estimatorRules";
import { matchPlan, formatPrice } from "@/lib/estimator";

// Instant-quote estimator (lead qualifier). Plans, prices and limits come from GET /api/plans;
// mapping rules live in config/estimatorRules.js (PLACEHOLDER — Brann to confirm).
// Waitlist-only mode (WL-002): leave /estimate out of the waitlist router to hide it.
// If it is shown under REACT_APP_WAITLIST_ONLY=true, prices and pricing/trial links are hidden
// (EMP-W-CF-030) so the result doesn't read as "buy now".

// Read at render time (inlined at build time by CRA) so tests can toggle it.
export const isWaitlistOnly = () => process.env.REACT_APP_WAITLIST_ONLY === "true";

const fieldCls = "w-full rounded-md bg-white/5 border border-white/20 text-white px-3 py-2";

function bandLabels(bands, t) {
  let prev = 0;
  return bands.map((b) => {
    let label;
    if (b.max === 0) label = t("estimator.band.none");
    else if (b.max === null) label = t("estimator.band.over", { n: prev.toLocaleString() });
    else if (prev === 0) label = t("estimator.band.upTo", { n: b.max.toLocaleString() });
    else label = t("estimator.band.range", { from: (prev + 1).toLocaleString(), to: b.max.toLocaleString() });
    if (b.max !== null) prev = b.max;
    return { id: b.id, label };
  });
}

export default function Estimate() {
  const { t } = useTranslation();
  const [plans, setPlans] = useState(null);
  const [industries, setIndustries] = useState([]);
  const [answers, setAnswers] = useState({
    industry: "", locations: 1, users: 1,
    callsBand: ESTIMATOR_RULES.callBands[0].id, smsBand: ESTIMATOR_RULES.smsBands[1].id,
    aiMinutesBand: ESTIMATOR_RULES.aiMinutesBands[0].id, capabilities: [],
  });
  const waitlistOnly = isWaitlistOnly();
  const [result, setResult] = useState(null);
  const [email, setEmail] = useState("");
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  // Same bot checks as the landing waitlist form (PR #11): honeypot "website" + Turnstile token.
  const [website, setWebsite] = useState("");
  const [turnstileToken, setTurnstileToken] = useState("");
  const onTurnstileToken = useCallback((token) => setTurnstileToken(token), []);

  useEffect(() => {
    api.get("/plans").then((r) => setPlans(r.data)).catch(() => setPlans([]));
    api.get("/industries", { params: { active_only: true } }).then((r) => setIndustries(r.data || [])).catch(() => setIndustries([]));
  }, []);

  const callBands = useMemo(() => bandLabels(ESTIMATOR_RULES.callBands, t), [t]);
  const smsBands = useMemo(() => bandLabels(ESTIMATOR_RULES.smsBands, t), [t]);
  const aiBands = useMemo(() => [
    ...bandLabels(ESTIMATOR_RULES.aiMinutesBands, t),
    { id: ESTIMATOR_RULES.aiMinutesUnsureId, label: t("estimator.aiMinutesUnsure") },
  ], [t]);
  const errors = result?.status === "invalid" ? result.errors : {};
  const set = (k) => (e) => setAnswers({ ...answers, [k]: e.target.value });
  const toggleCap = (id) => setAnswers((a) => ({
    ...a, capabilities: a.capabilities.includes(id) ? a.capabilities.filter((c) => c !== id) : [...a.capabilities, id],
  }));

  const submit = (e) => {
    e.preventDefault();
    setResult(matchPlan(answers, plans));
    setSent(false);
  };

  const sendEmail = async (e) => {
    e.preventDefault();
    if (!email || !result) return;
    setSending(true);
    if (result.status === "invalid") return;
    const tier = result.status === "ok" ? result.plan.key : result.status === "custom" ? "custom" : "unavailable";
    const industryName = industries.find((i) => i.slug === answers.industry)?.name || answers.industry || "";
    const note = [
      "estimator", `tier=${tier}`, `industry=${answers.industry || "-"}`, `locations=${answers.locations}`,
      `users=${answers.users}`, `calls=${answers.callsBand}`, `sms=${answers.smsBand}`, `ai=${answers.aiMinutesBand}`,
      `capabilities=${answers.capabilities.join("+") || "-"}`,
    ].join("; ");
    try {
      await api.post("/public/waitlist", {
        email, industry: industryName.slice(0, 60), note: note.slice(0, 2000),
        source_detail: "estimator", estimated_tier: tier,
        website, turnstile_token: turnstileToken,
      });
      setSent(true);
    } catch (err) {
      toast.error(errMessage(err));
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="marketing-shell relative overflow-x-hidden min-h-screen" data-testid="estimate-page">
      <header className="border-b border-white/10">
        <div className="max-w-3xl mx-auto px-6 py-4 flex items-center justify-between">
          <Link to="/"><Logo variant="light" /></Link>
          {!waitlistOnly && (
            <Link to="/pricing" className="text-sm text-white/70 hover:text-white" data-testid="estimate-header-pricing-link">{t("estimator.result.seePricing")}</Link>
          )}
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-10 text-white">
        <h1 className="font-display text-3xl">{t("estimator.title")}</h1>
        {/* TODO(marketing): intro copy (estimator.intro) */}

        {plans === null ? (
          <p className="mt-6 text-white/60" role="status">{t("estimator.loading")}</p>
        ) : (
          <form onSubmit={submit} noValidate className="mt-8 space-y-6" data-testid="estimate-form">
            <div className="space-y-1.5">
              <label htmlFor="est-industry" className="text-sm text-white/80">{t("estimator.industry")}</label>
              <select id="est-industry" className={fieldCls} value={answers.industry} onChange={set("industry")} data-testid="estimate-industry">
                <option value="">—</option>
                {industries.map((i) => <option key={i.slug} value={i.slug}>{i.name}</option>)}
                <option value="other">{t("estimator.industryOther")}</option>
              </select>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <label htmlFor="est-locations" className="text-sm text-white/80">{t("estimator.locations")}</label>
                <Input id="est-locations" type="number" min={1} step={1} max={ESTIMATOR_RULES.maxLocationsInput} required value={answers.locations} onChange={set("locations")} aria-invalid={!!errors.locations} aria-describedby={errors.locations ? "est-locations-error" : undefined} className="bg-white/5 border-white/20 text-white" data-testid="estimate-locations" />
                {errors.locations && <p id="est-locations-error" className="text-sm text-red-300" data-testid="estimate-locations-error">{t(`estimator.errors.${errors.locations}`)}</p>}
              </div>
              <div className="space-y-1.5">
                <label htmlFor="est-users" className="text-sm text-white/80">{t("estimator.users")}</label>
                <Input id="est-users" type="number" min={1} step={1} max={ESTIMATOR_RULES.maxUsersInput} required value={answers.users} onChange={set("users")} aria-invalid={!!errors.users} aria-describedby={errors.users ? "est-users-error" : undefined} className="bg-white/5 border-white/20 text-white" data-testid="estimate-users" />
                {errors.users && <p id="est-users-error" className="text-sm text-red-300" data-testid="estimate-users-error">{t(`estimator.errors.${errors.users}`)}</p>}
              </div>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <label htmlFor="est-calls" className="text-sm text-white/80">{t("estimator.calls")}</label>
                <select id="est-calls" className={fieldCls} value={answers.callsBand} onChange={set("callsBand")} data-testid="estimate-calls">
                  {callBands.map((b) => <option key={b.id} value={b.id}>{b.label}</option>)}
                </select>
              </div>
              <div className="space-y-1.5">
                <label htmlFor="est-sms" className="text-sm text-white/80">{t("estimator.sms")}</label>
                <select id="est-sms" className={fieldCls} value={answers.smsBand} onChange={set("smsBand")} data-testid="estimate-sms">
                  {smsBands.map((b) => <option key={b.id} value={b.id}>{b.label}</option>)}
                </select>
              </div>
            </div>
            <div className="space-y-1.5">
              <label htmlFor="est-ai-minutes" className="text-sm text-white/80">{t("estimator.aiMinutes")}</label>
              <select id="est-ai-minutes" className={fieldCls} value={answers.aiMinutesBand} onChange={set("aiMinutesBand")} data-testid="estimate-ai-minutes">
                {aiBands.map((b) => <option key={b.id} value={b.id}>{b.label}</option>)}
              </select>
            </div>
            <fieldset className="space-y-2">
              <legend className="text-sm text-white/80">{t("estimator.capabilities")}</legend>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {ESTIMATOR_RULES.capabilities.map((c) => (
                  <label key={c.id} className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={answers.capabilities.includes(c.id)} onChange={() => toggleCap(c.id)} data-testid={`estimate-cap-${c.id}`} />
                    {t(`estimator.cap.${c.id}`)}
                  </label>
                ))}
              </div>
            </fieldset>
            <Button type="submit" className="bg-white text-black hover:bg-white/90" data-testid="estimate-submit">{t("estimator.submit")}</Button>
          </form>
        )}

        <div aria-live="polite">
          {result && result.status !== "invalid" && (
            <section className="mt-10 glass-crystal rounded-2xl p-7" data-testid="estimate-result">
              {result.status === "ok" && (
                <>
                  <div className="overline text-white/60">{t("estimator.result.heading")}</div>
                  <div className="font-display text-3xl mt-2" data-testid="estimate-plan-name">{result.plan.name}</div>
                  {waitlistOnly ? (
                    // TODO(Brann/marketing): "planned pricing at launch"-style wording (and whether to show
                    // the planned price at all) for waitlist-only mode. Copy not written: Brann's call.
                    <div data-testid="estimate-planned-pricing-slot" />
                  ) : (
                    <div className="mt-1 text-xl" data-testid="estimate-plan-price">
                      {formatPrice(result.plan.price_cents)}<span className="text-sm text-white/60">{t("estimator.result.perMonth")}</span>
                    </div>
                  )}
                  {/* TODO(marketing): why-this-plan copy */}
                  <h2 className="mt-5 text-sm text-white/70">{t("estimator.result.limitsHeading")}</h2>
                  <ul className="mt-2 text-sm grid grid-cols-2 gap-1">
                    {["calls", "sms", "ai_minutes", "locations", "users"].map((m) => (
                      typeof result.plan.limits?.[m] === "number" && (
                        <li key={m}>{t(`estimator.result.limit.${m}`)}: {result.plan.limits[m].toLocaleString()}</li>
                      )
                    ))}
                  </ul>
                </>
              )}
              {result.status === "custom" && (
                <>
                  <div className="font-display text-3xl" data-testid="estimate-plan-name">{result.plan?.name || t("estimator.result.customTitle")}</div>
                  <p className="mt-2 text-white/70">{t("estimator.result.customBody")}</p>
                  {result.exceeded.length > 0 && (
                    <p className="mt-1 text-sm text-white/60">
                      {t("estimator.result.exceeded", { list: result.exceeded.map((m) => t(`estimator.result.limit.${m}`)).join(", ") })}
                    </p>
                  )}
                  {/* TODO(marketing/Brann): custom-plan contact path (sales@ mailbox does not receive mail yet, EMP-WL-004) */}
                </>
              )}
              {result.status === "unavailable" && (
                <p className="text-white/70" data-testid="estimate-unavailable">{t("estimator.result.unavailable")}</p>
              )}
              {!waitlistOnly && (
                <Link to="/pricing" className="mt-4 inline-block text-sm underline text-white/80" data-testid="estimate-result-pricing-link">{t("estimator.result.seePricing")}</Link>
              )}

              <form onSubmit={sendEmail} className="mt-6 border-t border-white/10 pt-5 space-y-2" data-testid="estimate-email-form">
                <h2 className="text-sm text-white/80">{t("estimator.email.heading")}</h2>
                {sent ? (
                  <p className="text-sm text-emerald-200" role="status" data-testid="estimate-email-sent">{t("estimator.email.sent")}</p>
                ) : (
                  <div className="flex gap-2">
                    <label htmlFor="est-email" className="sr-only">{t("estimator.email.label")}</label>
                    <Input id="est-email" type="email" required maxLength={254} value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@yourbusiness.com" className="bg-white/5 border-white/20 text-white" data-testid="estimate-email" />
                    <Button type="submit" disabled={sending || !email} className="bg-white text-black hover:bg-white/90" data-testid="estimate-email-submit">{t("estimator.email.submit")}</Button>
                  </div>
                )}
                {/* Honeypot: hidden from people and screen readers; bots that fill it are silently ignored. */}
                <div aria-hidden="true" style={{ position: "absolute", left: "-10000px", width: 1, height: 1, overflow: "hidden" }}>
                  <label htmlFor="est-website">Website</label>
                  <input id="est-website" name="website" type="text" tabIndex={-1} autoComplete="off" value={website} onChange={(e) => setWebsite(e.target.value)} data-testid="estimate-honeypot" />
                </div>
                {!sent && <TurnstileWidget onToken={onTurnstileToken} />}
                {/* TODO(WL-010, Brann): consent line goes here. Wording not written; Brann's call. */}
                <div data-testid="estimate-consent-slot" />
                <Link to="/privacy" className="text-xs underline text-white/60">{t("estimator.email.privacy")}</Link>
              </form>
            </section>
          )}
        </div>
      </main>
    </div>
  );
}
