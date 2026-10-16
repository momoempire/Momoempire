import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errMessage } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import {
  Megaphone, PhoneMissed, Languages, Play, Pause, XCircle,
  Send, Clock, Globe, Plus,
} from "lucide-react";

const TRIGGER_OPTIONS = [
  { value: "no_booking_days", def: "Win-back (no booking in N days)" },
  { value: "job_completed_days", def: "Review nudge (job completed N days ago)" },
  { value: "maintenance_due", def: "Reminder (maintenance due within N days)" },
];

function CampaignModal({ templates, onClose, onSaved }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({ name: "", trigger_type: "no_booking_days", days: 90, template_key: "win_back" });
  const [saving, setSaving] = useState(false);
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const keys = Object.keys(templates || {});

  const save = async () => {
    if (!form.name.trim()) return toast.error(t("outreach.name_required", "Campaign name is required"));
    setSaving(true);
    try {
      await api.post("/outreach/campaigns", { ...form, name: form.name.trim(), days: Number(form.days) });
      toast.success(t("outreach.campaign_created", "Campaign created."));
      onSaved();
      onClose();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-xl bg-card p-6 shadow-xl" data-testid="outreach-campaign-modal">
        <h3 className="text-lg font-semibold mb-1">{t("outreach.new_campaign", "New outreach campaign")}</h3>
        <p className="text-sm text-muted-foreground mb-4">
          {t("outreach.new_hint", "Pick a trigger and a template. Messages go out in each customer's language.")}
        </p>
        <div className="space-y-3">
          <Input placeholder={t("outreach.campaign_name", "Campaign name *")} value={form.name} onChange={(e) => set({ name: e.target.value })} />
          <select value={form.trigger_type} onChange={(e) => set({ trigger_type: e.target.value })} className="rounded-md border bg-card px-3 py-2 text-sm w-full">
            {TRIGGER_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{t(`outreach.trigger_${o.value}`, o.def)}</option>
            ))}
          </select>
          <div className="flex gap-2">
            <Input type="number" min={1} value={form.days} onChange={(e) => set({ days: e.target.value })} className="w-28" aria-label={t("outreach.days", "Days")} />
            <select value={form.template_key} onChange={(e) => set({ template_key: e.target.value })} className="rounded-md border bg-card px-3 py-2 text-sm flex-1">
              {keys.map((k) => <option key={k} value={k}>{k}</option>)}
            </select>
          </div>
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <Button variant="outline" onClick={onClose}>{t("outreach.cancel", "Cancel")}</Button>
          <Button onClick={save} disabled={saving} data-testid="outreach-campaign-save">
            {t("outreach.create", "Create campaign")}
          </Button>
        </div>
      </div>
    </div>
  );
}

function TemplateManager({ templates, canManage, onSaved }) {
  const { t } = useTranslation();
  const [lang, setLang] = useState("en");
  const [drafts, setDrafts] = useState({});
  const [savingKey, setSavingKey] = useState(null);

  useEffect(() => {
    const d = {};
    for (const [key, langs] of Object.entries(templates || {})) d[key] = langs[lang] || "";
    setDrafts(d);
  }, [templates, lang]);

  const save = async (key) => {
    setSavingKey(key);
    try {
      await api.put("/outreach/templates", { key, lang, text: drafts[key] || "" });
      toast.success(t("outreach.template_saved", "Template saved."));
      onSaved();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setSavingKey(null);
    }
  };

  return (
    <div className="rounded-xl border bg-card p-5" data-testid="outreach-templates">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Languages className="h-4 w-4 text-muted-foreground" />
          <h3 className="font-semibold text-sm">{t("outreach.templates_title", "Message templates")}</h3>
        </div>
        <div className="flex gap-1 rounded-md border p-0.5">
          {["en", "es"].map((l) => (
            <button
              key={l}
              onClick={() => setLang(l)}
              className={`rounded px-3 py-1 text-xs font-medium ${lang === l ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}
            >
              {l === "en" ? "EN" : "ES"}
            </button>
          ))}
        </div>
      </div>
      <div className="space-y-3">
        {Object.keys(templates || {}).map((key) => (
          <div key={key}>
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-mono text-muted-foreground">{key}</span>
              {canManage && (
                <Button size="sm" variant="outline" onClick={() => save(key)} disabled={savingKey === key}>
                  {t("outreach.save", "Save")}
                </Button>
              )}
            </div>
            <textarea
              value={drafts[key] ?? ""}
              onChange={(e) => setDrafts((d) => ({ ...d, [key]: e.target.value }))}
              disabled={!canManage}
              rows={2}
              className="w-full rounded-md border bg-card px-3 py-2 text-sm disabled:opacity-60"
            />
          </div>
        ))}
      </div>
      <p className="text-xs text-muted-foreground mt-3 flex gap-1.5">
        <Globe className="h-3.5 w-3.5 shrink-0" />
        {t("outreach.templates_hint", "Placeholders: {name}, {business_name}, {days}. Missing languages fall back to English.")}
      </p>
    </div>
  );
}

export default function Outreach() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [campaigns, setCampaigns] = useState([]);
  const [queue, setQueue] = useState([]);
  const [missed, setMissed] = useState([]);
  const [templates, setTemplates] = useState({});
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [busy, setBusy] = useState(null);
  const canManage = user && ["owner", "admin"].includes(user.role);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [c, q, m, tp] = await Promise.all([
        api.get("/outreach/campaigns"),
        api.get("/outreach/queue"),
        api.get("/outreach/missed-calls"),
        api.get("/outreach/templates"),
      ]);
      setCampaigns(c.data.campaigns || []);
      setQueue(q.data.messages || []);
      setMissed(m.data.missed_calls || []);
      setTemplates(tp.data.templates || {});
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const toggle = async (id) => {
    setBusy(id);
    try {
      const r = await api.post(`/outreach/campaigns/${encodeURIComponent(id)}/toggle`);
      toast.success(r.data.enabled ? t("outreach.enabled", "Campaign enabled.") : t("outreach.paused", "Campaign paused."));
      load();
    } catch (e) { toast.error(errMessage(e)); } finally { setBusy(null); }
  };

  const runNow = async (id) => {
    setBusy(id);
    try {
      const r = await api.post(`/outreach/campaigns/${encodeURIComponent(id)}/run`);
      toast.success(t("outreach.run_done", "Messages queued."));
      load();
    } catch (e) { toast.error(errMessage(e)); } finally { setBusy(null); }
  };

  const cancelMsg = async (id) => {
    try {
      await api.post(`/outreach/queue/${encodeURIComponent(id)}/cancel`);
      toast.success(t("outreach.canceled", "Message canceled."));
      load();
    } catch (e) { toast.error(errMessage(e)); }
  };

  const rescue = async (id) => {
    setBusy(id);
    try {
      await api.post(`/outreach/missed-calls/${encodeURIComponent(id)}/rescue`);
      toast.success(t("outreach.rescue_queued", "Rescue SMS queued."));
      load();
    } catch (e) { toast.error(errMessage(e)); } finally { setBusy(null); }
  };

  return (
    <div>
      <PageHeader
        title={t("outreach.title", "Proactive Outreach")}
        description={t("outreach.description", "Trigger-based win-backs and reminders, missed-call rescue, multilingual templates.")}
        testId="outreach-header"
        actions={
          canManage && (
            <Button size="sm" onClick={() => setShowModal(true)} data-testid="outreach-new">
              <Plus className="h-4 w-4 mr-1" /> {t("outreach.new_campaign", "New campaign")}
            </Button>
          )
        }
      />

      {loading ? (
        <div className="text-muted-foreground">{t("outreach.loading", "Loading outreach…")}</div>
      ) : (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5" data-testid="outreach-kpis">
            {[
              { label: t("outreach.kpi_campaigns", "Campaigns"), value: campaigns.length, icon: <Megaphone className="h-4 w-4 text-violet-500" /> },
              { label: t("outreach.kpi_active", "Active"), value: campaigns.filter((c) => c.enabled).length, icon: <Play className="h-4 w-4 text-emerald-500" /> },
              { label: t("outreach.kpi_queued", "Queued messages"), value: queue.length, icon: <Send className="h-4 w-4 text-sky-500" /> },
              { label: t("outreach.kpi_missed", "Missed calls"), value: missed.length, icon: <PhoneMissed className="h-4 w-4 text-rose-500" /> },
            ].map((k) => (
              <div key={k.label} className="rounded-xl border bg-card p-4">
                <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">{k.icon}{k.label}</div>
                <div className="text-2xl font-bold">{k.value}</div>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mb-5">
            {/* Campaigns */}
            <div className="rounded-xl border bg-card p-5">
              <div className="flex items-center gap-2 mb-3">
                <Megaphone className="h-4 w-4 text-muted-foreground" />
                <h3 className="font-semibold text-sm">{t("outreach.campaigns_title", "Campaigns")}</h3>
              </div>
              {campaigns.length === 0 ? (
                <p className="text-sm text-muted-foreground">{t("outreach.no_campaigns", "No campaigns yet. Create one to start proactive outreach.")}</p>
              ) : (
                <div className="space-y-3">
                  {campaigns.map((c) => (
                    <div key={c.id} className="rounded-lg border p-3" data-testid={`outreach-campaign-${c.id}`}>
                      <div className="flex flex-wrap items-center gap-2 mb-1">
                        <span className="font-medium text-sm">{c.name}</span>
                        <Badge variant={c.enabled ? "default" : "secondary"}>
                          {c.enabled ? t("outreach.active", "Active") : t("outreach.paused_badge", "Paused")}
                        </Badge>
                        <Badge variant="outline">{c.trigger_label || c.trigger_type}</Badge>
                        <Badge variant="outline" className="font-mono">{c.template_key}</Badge>
                      </div>
                      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground mb-2">
                        <span className="flex items-center gap-1"><Clock className="h-3 w-3" />
                          {c.last_run_at
                            ? t("outreach.last_run", "Last run") + `: ${c.last_run_at.slice(0, 10)} (${c.last_run_queued} ${t("outreach.queued", "queued")})`
                            : t("outreach.never_run", "Never run")}
                        </span>
                      </div>
                      {canManage && (
                        <div className="flex gap-2">
                          <Button size="sm" variant="outline" onClick={() => toggle(c.id)} disabled={busy === c.id}>
                            {c.enabled ? <Pause className="h-3.5 w-3.5 mr-1" /> : <Play className="h-3.5 w-3.5 mr-1" />}
                            {c.enabled ? t("outreach.pause", "Pause") : t("outreach.resume", "Resume")}
                          </Button>
                          <Button size="sm" onClick={() => runNow(c.id)} disabled={busy === c.id || !c.enabled}>
                            <Send className="h-3.5 w-3.5 mr-1" /> {t("outreach.run_now", "Run now")}
                          </Button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Missed-call rescue */}
            <div className="rounded-xl border bg-card p-5">
              <div className="flex items-center gap-2 mb-3">
                <PhoneMissed className="h-4 w-4 text-muted-foreground" />
                <h3 className="font-semibold text-sm">{t("outreach.missed_title", "Missed-call rescue")}</h3>
              </div>
              {missed.length === 0 ? (
                <p className="text-sm text-muted-foreground">{t("outreach.no_missed", "No missed calls. Rescue SMS goes out automatically when one lands.")}</p>
              ) : (
                <div className="space-y-3">
                  {missed.slice(0, 10).map((m) => (
                    <div key={m.id} className="rounded-lg border p-3 flex flex-wrap items-center gap-2" data-testid={`outreach-missed-${m.id}`}>
                      <div className="flex-1 min-w-[10rem]">
                        <div className="text-sm font-medium">{m.caller_name || m.caller_phone || t("outreach.unknown", "Unknown caller")}</div>
                        <div className="text-xs text-muted-foreground">{m.caller_phone} · {(m.created_at || "").slice(0, 16).replace("T", " ")}</div>
                      </div>
                      {m.rescued ? (
                        <Badge className="bg-emerald-100 text-emerald-800">
                          {m.rescue?.escalated ? t("outreach.escalated", "Escalated") : t("outreach.rescued", "Rescue queued")}
                        </Badge>
                      ) : (
                        canManage && (
                          <Button size="sm" variant="outline" onClick={() => rescue(m.id)} disabled={busy === m.id}>
                            <Send className="h-3.5 w-3.5 mr-1" /> {t("outreach.send_rescue", "Send rescue")}
                          </Button>
                        )
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Queue */}
          <div className="rounded-xl border bg-card p-5 mb-5">
            <div className="flex items-center gap-2 mb-3">
              <Send className="h-4 w-4 text-muted-foreground" />
              <h3 className="font-semibold text-sm">{t("outreach.queue_title", "Message queue")}</h3>
              <Badge variant="secondary">{queue.length} {t("outreach.pending", "pending")}</Badge>
            </div>
            {queue.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("outreach.queue_empty", "Nothing queued. Run a campaign or rescue a missed call.")}</p>
            ) : (
              <div className="space-y-2">
                {queue.map((m) => (
                  <div key={m.id} className="rounded-lg border p-3 flex flex-wrap items-start gap-2" data-testid={`outreach-queue-${m.id}`}>
                    <div className="flex-1 min-w-[12rem]">
                      <div className="flex flex-wrap items-center gap-1.5 mb-1">
                        <Badge variant="outline">{m.kind}</Badge>
                        <Badge variant="outline" className="uppercase">{m.lang}</Badge>
                        <span className="text-xs text-muted-foreground">{m.to_phone}</span>
                      </div>
                      <p className="text-sm">{m.body}</p>
                    </div>
                    {canManage && (
                      <Button size="sm" variant="ghost" onClick={() => cancelMsg(m.id)}>
                        <XCircle className="h-4 w-4 mr-1" /> {t("outreach.cancel", "Cancel")}
                      </Button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          <TemplateManager templates={templates} canManage={canManage} onSaved={load} />
        </>
      )}

      {showModal && <CampaignModal templates={templates} onClose={() => setShowModal(false)} onSaved={load} />}
    </div>
  );
}
