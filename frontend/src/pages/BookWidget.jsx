import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api, errMessage } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Loader2, Siren, Wrench, CheckCircle2, ArrowLeft } from "lucide-react";

// Public demo of the website self-booking widget — minimal chrome, no auth.
// Real route (wired at integration): /book/:shopSlug. Works standalone too:
// enter a business slug to run the full customer flow against the live
// public endpoints:
//   POST /public/triage/:slug/analyze   {description} -> category/urgency/slots hint
//   GET  /public/triage/:slug/slots     -> real open slots
//   POST /public/triage/:slug/book      -> pending booking request

const fmtDate = (iso) =>
  new Date(iso).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
const fmtTime = (iso) =>
  new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });

function UrgencyBadge({ urgency, t }) {
  if (urgency === "emergency")
    return (
      <Badge className="bg-red-100 text-red-800 hover:bg-red-100">
        <Siren className="mr-1 h-3 w-3" />
        {t("triage.emergency", "Emergency")}
      </Badge>
    );
  if (urgency === "same-day")
    return (
      <Badge className="bg-amber-100 text-amber-800 hover:bg-amber-100">
        {t("triage.same_day", "Same-day")}
      </Badge>
    );
  return <Badge className="bg-sky-100 text-sky-800 hover:bg-sky-100">{t("triage.scheduled", "Scheduled")}</Badge>;
}

export default function BookWidget() {
  const { shopSlug: routeSlug } = useParams();
  const { t } = useTranslation();
  const [slug, setSlug] = useState(routeSlug || "");
  const [step, setStep] = useState("describe"); // describe | result | details | done
  const [description, setDescription] = useState("");
  const [triage, setTriage] = useState(null);
  const [slots, setSlots] = useState([]);
  const [slotInfo, setSlotInfo] = useState(null);
  const [chosen, setChosen] = useState(null);
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [receipt, setReceipt] = useState(null);

  const reset = useCallback(() => {
    setStep("describe");
    setTriage(null);
    setSlots([]);
    setChosen(null);
    setReceipt(null);
    setError("");
  }, []);

  useEffect(() => {
    if (routeSlug) {
      setSlug(routeSlug);
      reset();
    }
  }, [routeSlug, reset]);

  const analyze = async () => {
    if (!slug.trim() || !description.trim()) {
      setError(t("triage.required", "This field is required."));
      return;
    }
    setBusy(true);
    setError("");
    try {
      const r = await api.post(`/public/triage/${slug.trim()}/analyze`, { description: description.trim() });
      const res = r.data;
      setTriage(res);
      const s = await api.get(`/public/triage/${slug.trim()}/slots`, {
        params: { category: res.category || "", urgency: res.urgency },
      });
      setSlots(s.data.slots || []);
      setSlotInfo(s.data);
      setStep("result");
    } catch (e) {
      setError(e?.response?.status === 404 ? t("triage.invalid_slug", "We couldn't find that business. Check the slug and try again.") : errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const book = async () => {
    if (!chosen || !name.trim() || !phone.trim()) {
      setError(t("triage.required", "This field is required."));
      return;
    }
    setBusy(true);
    setError("");
    try {
      const r = await api.post(`/public/triage/${slug.trim()}/book`, {
        triage_id: triage.triage_id,
        slot_start: chosen.start,
        slot_end: chosen.end,
        customer_name: name.trim(),
        customer_phone: phone.trim(),
      });
      setReceipt(r.data);
      setStep("done");
    } catch (e) {
      setError(e?.response?.status === 409 ? t("triage.no_slots", "No open slots in this window — try another category or call us.") : errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="min-h-screen bg-background px-4 py-10">
      <div className="mx-auto w-full max-w-xl">
        <div className="mb-6 text-center">
          <p className="overline mb-2">{t("triage.widget_demo", "Widget demo")}</p>
          <h1 className="font-display text-3xl tracking-tight">
            {triage?.shop?.name || t("triage.title", "Triage & Booking")}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {t("triage.widget_demo_hint", "This is how customers see it. Enter a business slug to try the live widget endpoints.")}
          </p>
        </div>

        <Card>
          <CardContent className="space-y-5 pt-6">
            {!routeSlug && step === "describe" && (
              <label className="block text-sm">
                <span className="mb-1 block font-medium">{t("triage.shop_slug", "Business slug")}</span>
                <Input value={slug} onChange={(e) => setSlug(e.target.value)} placeholder="shop-a" />
              </label>
            )}

            {step === "describe" && (
              <>
                <label className="block text-sm">
                  <span className="mb-1 block font-medium">
                    {t("triage.describe_problem", "Describe the problem in your own words")}
                  </span>
                  <textarea
                    className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                    rows={4}
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder={t("triage.describe_placeholder", "e.g. water is leaking under the kitchen sink…")}
                  />
                </label>
                <Button className="w-full" onClick={analyze} disabled={busy}>
                  {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Wrench className="mr-2 h-4 w-4" />}
                  {busy ? t("triage.analyzing", "Analyzing…") : t("triage.analyze", "Check what's wrong")}
                </Button>
              </>
            )}

            {step === "result" && triage && (
              <>
                {triage.urgency === "emergency" && (
                  <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-900">
                    <p className="flex items-center gap-2 font-semibold">
                      <Siren className="h-4 w-4" /> {t("triage.emergency", "Emergency")}
                    </p>
                    <p className="mt-1">{triage.emergency_message}</p>
                    {triage.emergency_phone && (
                      <p className="mt-1 font-semibold">
                        <a href={`tel:${triage.emergency_phone}`}>{triage.emergency_phone}</a>
                      </p>
                    )}
                  </div>
                )}

                <div className="flex flex-wrap items-center gap-2">
                  {triage.category && (
                    <Badge variant="outline" className="capitalize">
                      {triage.category.replace(/_/g, " ")}
                    </Badge>
                  )}
                  <UrgencyBadge urgency={triage.urgency} t={t} />
                  <span className="text-xs text-muted-foreground">
                    {t("triage.estimated_duration", "Estimated visit")}: {triage.estimated_duration_minutes}
                    {t("triage.minutes", " min")}
                  </span>
                </div>

                {!triage.covered && (
                  <p className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                    {t("triage.not_covered", "This looks outside what we cover online — please call us instead.")}
                  </p>
                )}

                {triage.clarifying_questions?.length > 0 && (
                  <div className="rounded-lg bg-muted p-4">
                    <p className="mb-2 text-sm font-medium">{t("triage.questions_title", "A couple of quick questions")}</p>
                    <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                      {triage.clarifying_questions.map((q, i) => (
                        <li key={i}>{q}</li>
                      ))}
                    </ul>
                  </div>
                )}

                <div>
                  <p className="mb-2 text-sm font-medium">{t("triage.pick_slot", "Pick a time")}</p>
                  {slots.length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      {t("triage.no_slots", "No open slots in this window — try another category or call us.")}
                    </p>
                  ) : (
                    <ul className="grid grid-cols-2 gap-2">
                      {slots.slice(0, 12).map((s) => (
                        <li key={s.start}>
                          <button
                            type="button"
                            onClick={() => {
                              setChosen(s);
                              setStep("details");
                            }}
                            className="w-full rounded-lg border px-3 py-2 text-left text-sm transition hover:border-primary hover:bg-primary/5"
                          >
                            <span className="block font-medium">{fmtDate(s.start)}</span>
                            <span className="text-muted-foreground">
                              {fmtTime(s.start)} – {fmtTime(s.end)}
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                <Button variant="ghost" size="sm" onClick={() => setStep("describe")}>
                  <ArrowLeft className="mr-1 h-3 w-3" /> {t("triage.back", "Back")}
                </Button>
              </>
            )}

            {step === "details" && (
              <>
                <div className="rounded-lg bg-muted p-4 text-sm">
                  <p className="font-medium">
                    {chosen && `${fmtDate(chosen.start)} · ${fmtTime(chosen.start)} – ${fmtTime(chosen.end)}`}
                  </p>
                  <p className="text-muted-foreground">
                    {slotInfo?.duration_minutes}
                    {t("triage.minutes", " min")} {t("triage.slot", "Slot")}
                    {triage?.category ? ` · ${triage.category.replace(/_/g, " ")}` : ""}
                  </p>
                </div>
                <label className="block text-sm">
                  <span className="mb-1 block font-medium">{t("triage.name", "Full name")}</span>
                  <Input value={name} onChange={(e) => setName(e.target.value)} />
                </label>
                <label className="block text-sm">
                  <span className="mb-1 block font-medium">{t("triage.phone", "Phone number")}</span>
                  <Input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="555-0100" />
                </label>
                <div className="flex gap-2">
                  <Button className="flex-1" onClick={book} disabled={busy}>
                    {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                    {busy ? t("triage.booking", "Sending…") : t("triage.book", "Request booking")}
                  </Button>
                  <Button variant="outline" onClick={() => setStep("result")}>
                    {t("triage.back", "Back")}
                  </Button>
                </div>
              </>
            )}

            {step === "done" && receipt && (
              <div className="py-6 text-center">
                <CheckCircle2 className="mx-auto mb-3 h-12 w-12 text-emerald-500" />
                <p className="text-lg font-semibold">{t("triage.booking_received", "Request received!")}</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  {t("triage.booking_received_hint", "The shop will confirm your booking shortly. We'll text you at")}{" "}
                  {phone}
                </p>
                <Button className="mt-6" variant="outline" onClick={reset}>
                  {t("triage.book_another", "Book another visit")}
                </Button>
              </div>
            )}

            {error && (
              <p role="alert" className="text-sm text-red-600">
                {error}
              </p>
            )}
          </CardContent>
        </Card>

      </div>
    </main>
  );
}
