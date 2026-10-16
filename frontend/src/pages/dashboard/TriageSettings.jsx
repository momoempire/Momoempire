import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errMessage } from "@/lib/api";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { toast } from "sonner";
import { CalendarCheck, Loader2, Save, Siren } from "lucide-react";

const ALL_CATEGORIES = [
  "plumbing",
  "hvac",
  "electrical",
  "appliance",
  "pest",
  "garage_door",
  "roofing",
];

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
  return (
    <Badge className="bg-sky-100 text-sky-800 hover:bg-sky-100">
      {t("triage.scheduled", "Scheduled")}
    </Badge>
  );
}

function StatusBadge({ status, t }) {
  if (status === "pending")
    return <Badge variant="outline">{t("triage.pending", "Pending")}</Badge>;
  if (status === "confirmed")
    return (
      <Badge className="bg-emerald-100 text-emerald-800 hover:bg-emerald-100">
        {t("triage.confirmed", "Confirmed")}
      </Badge>
    );
  return <Badge variant="secondary">{t("triage.declined", "Declined")}</Badge>;
}

// Tenant dashboard: triage + self-booking widget settings, and the incoming
// booking requests customers submit from the widget. Confirming a request
// turns it into a real appointment; declining just marks it declined.
export default function TriageSettings() {
  const { t } = useTranslation();
  const [settings, setSettings] = useState(null);
  const [saving, setSaving] = useState(false);
  const [requests, setRequests] = useState([]);
  const [loadingReqs, setLoadingReqs] = useState(true);
  const [actingId, setActingId] = useState(null);

  const loadSettings = async () => {
    try {
      const r = await api.get("/triage/settings");
      setSettings((prev) => ({ ...(prev || {}), ...r.data }));
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const loadRequests = async () => {
    setLoadingReqs(true);
    try {
      const r = await api.get("/triage/requests");
      setRequests(r.data.requests || []);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoadingReqs(false);
    }
  };

  useEffect(() => {
    loadSettings();
    loadRequests();
  }, []);

  const set = (k, v) => setSettings((s) => ({ ...s, [k]: v }));

  const toggleCategory = (cat) => {
    const cur = settings.covered_categories || [];
    set(
      "covered_categories",
      cur.includes(cat) ? cur.filter((c) => c !== cat) : [...cur, cat]
    );
  };

  const save = async () => {
    setSaving(true);
    try {
      await api.put("/triage/settings", {
        enabled: !!settings.enabled,
        covered_categories: settings.covered_categories || [],
        slot_buffer_minutes: Number(settings.slot_buffer_minutes ?? 15),
        lead_days: Number(settings.lead_days ?? 7),
        emergency_phone: settings.emergency_phone || "",
        emergency_message: settings.emergency_message || "",
        public_token: settings.public_token || "",
      });
      toast.success(t("triage.saved", "Triage settings saved."));
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setSaving(false);
    }
  };

  const act = async (id, action) => {
    setActingId(id);
    try {
      await api.post(`/triage/requests/${id}/${action}`);
      await loadRequests();
      toast.success(
        action === "confirm" ? t("triage.confirmed", "Confirmed") : t("triage.declined", "Declined")
      );
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setActingId(null);
    }
  };

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow={t("triage.widget_demo", "Widget demo")}
        title={t("triage.title", "Triage & Booking")}
        description={t(
          "triage.subtitle",
          "AI problem triage for your website widget, plus the booking requests customers send you."
        )}
        actions={
          <Button onClick={save} disabled={saving || !settings}>
            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
            {t("triage.save", "Save settings")}
          </Button>
        }
      />

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>{t("triage.widget_enabled", "Enable website booking widget")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-5">
            <label className="flex items-center gap-3 text-sm">
              <input
                type="checkbox"
                checked={!!settings?.enabled}
                onChange={(e) => set("enabled", e.target.checked)}
                className="h-4 w-4"
              />
              {t("triage.widget_enabled", "Enable website booking widget")}
            </label>

            <div>
              <p className="mb-1 text-sm font-medium">{t("triage.covered_categories", "Services covered by the widget")}</p>
              <p className="mb-2 text-xs text-muted-foreground">
                {t(
                  "triage.covered_hint",
                  "Empty means every detected category is bookable. Check only what your crews can do."
                )}
              </p>
              <div className="flex flex-wrap gap-2">
                {ALL_CATEGORIES.map((cat) => {
                  const on = (settings?.covered_categories || []).includes(cat);
                  return (
                    <button
                      key={cat}
                      type="button"
                      onClick={() => toggleCategory(cat)}
                      className={`rounded-full border px-3 py-1 text-xs capitalize transition ${
                        on ? "border-primary bg-primary/10 text-primary" : "text-muted-foreground"
                      }`}
                    >
                      {cat.replace(/_/g, " ")}
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <label className="block text-sm">
                <span className="mb-1 block font-medium">
                  {t("triage.slot_buffer_minutes", "Buffer around appointments (minutes)")}
                </span>
                <Input
                  type="number"
                  min={0}
                  max={120}
                  value={settings?.slot_buffer_minutes ?? 15}
                  onChange={(e) => set("slot_buffer_minutes", e.target.value)}
                />
              </label>
              <label className="block text-sm">
                <span className="mb-1 block font-medium">{t("triage.lead_days", "Booking window (days ahead)")}</span>
                <Input
                  type="number"
                  min={1}
                  max={30}
                  value={settings?.lead_days ?? 7}
                  onChange={(e) => set("lead_days", e.target.value)}
                />
              </label>
            </div>

            <div>
              <p className="mb-1 text-sm font-medium">{t("triage.emergency_phone", "Emergency phone number")}</p>
              <Input
                value={settings?.emergency_phone || ""}
                onChange={(e) => set("emergency_phone", e.target.value)}
                placeholder="555-0100"
              />
            </div>
            <div>
              <p className="mb-1 text-sm font-medium">{t("triage.emergency_message", "Emergency message for customers")}</p>
              <p className="mb-2 text-xs text-muted-foreground">
                {t(
                  "triage.emergency_message_hint",
                  "Shown when the AI detects an emergency. Defaults to a call-911-first message."
                )}
              </p>
              <textarea
                className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                rows={3}
                value={settings?.emergency_message || ""}
                onChange={(e) => set("emergency_message", e.target.value)}
              />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <CalendarCheck className="h-5 w-5" />
              {t("triage.requests", "Booking requests")}
            </CardTitle>
            <p className="text-xs text-muted-foreground">
              {t(
                "triage.requests_hint",
                "Customers' self-bookings. Confirm to turn a request into a real appointment."
              )}
            </p>
          </CardHeader>
          <CardContent>
            {loadingReqs ? (
              <div className="grid place-items-center py-8 text-muted-foreground">
                <Loader2 className="h-5 w-5 animate-spin" />
              </div>
            ) : requests.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted-foreground">
                {t("triage.no_requests", "No booking requests yet. Share your widget link to start receiving them.")}
              </p>
            ) : (
              <ul className="space-y-3">
                {requests.map((r) => (
                  <li key={r.id} className="rounded-lg border p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="font-medium">{r.customer_name}</p>
                        <p className="text-xs text-muted-foreground">{r.customer_phone}</p>
                        <p className="mt-2 text-sm">{r.description}</p>
                        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                          {r.category && (
                            <span className="rounded bg-muted px-2 py-0.5 capitalize">
                              {r.category.replace(/_/g, " ")}
                            </span>
                          )}
                          <UrgencyBadge urgency={r.urgency} t={t} />
                          <span>
                            {new Date(r.slot_start).toLocaleString()} · {r.estimated_duration_minutes}
                            {t("triage.minutes", " min")}
                          </span>
                        </div>
                      </div>
                      <StatusBadge status={r.status} t={t} />
                    </div>
                    {r.status === "pending" && (
                      <div className="mt-3 flex gap-2">
                        <Button
                          size="sm"
                          onClick={() => act(r.id, "confirm")}
                          disabled={actingId === r.id}
                        >
                          {actingId === r.id ? (
                            <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                          ) : null}
                          {t("triage.confirm", "Confirm")}
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => act(r.id, "decline")}
                          disabled={actingId === r.id}
                        >
                          {t("triage.decline", "Decline")}
                        </Button>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
