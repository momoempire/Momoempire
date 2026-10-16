import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errMessage } from "@/lib/api";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { toast } from "sonner";
import { Megaphone, Users, ListOrdered, Send, FlaskConical, Trash2, Plus, Eye } from "lucide-react";

const BASE = "/tenants/marketing";
const OPERATORS = ["equals", "not_equals", "contains", "not_contains", "starts_with", "gt", "gte", "lt", "lte", "in", "not_in", "is_empty", "is_not_empty"];

export default function Marketing() {
  const { t } = useTranslation();
  const [campaigns, setCampaigns] = useState([]);
  const [segments, setSegments] = useState([]);
  const [sequences, setSequences] = useState([]);
  const [showNew, setShowNew] = useState(null); // 'campaign' | 'segment' | 'sequence'
  const [preview, setPreview] = useState(null);
  const [stats, setStats] = useState(null);
  const [busy, setBusy] = useState(null);

  const load = () => {
    api.get(`${BASE}/campaigns`).then((r) => setCampaigns(r.data)).catch(() => {});
    api.get(`${BASE}/segments`).then((r) => setSegments(r.data)).catch(() => {});
    api.get(`${BASE}/sequences`).then((r) => setSequences(r.data)).catch(() => {});
  };
  useEffect(() => { load(); }, []);

  const del = async (kind, id) => {
    if (!confirm(t("marketing.confirm_delete"))) return;
    try { await api.delete(`${BASE}/${kind}/${id}`); toast.success(t("marketing.deleted")); load(); }
    catch (e) { toast.error(errMessage(e)); }
  };
  const send = async (id) => {
    if (!confirm(t("marketing.confirm_send"))) return;
    setBusy(id);
    try { const { data } = await api.post(`${BASE}/campaigns/${id}/send`); toast.success(t("marketing.sent_toast", { n: data.sent })); load(); }
    catch (e) { toast.error(errMessage(e)); }
    finally { setBusy(null); }
  };
  const testSend = async (id) => {
    const to = prompt(t("marketing.test_send_prompt"));
    if (!to) return;
    try { await api.post(`${BASE}/campaigns/${id}/test-send`, { to }); toast.success(t("marketing.test_sent_toast")); }
    catch (e) { toast.error(errMessage(e)); }
  };
  const showStats = async (id) => {
    try { const { data } = await api.get(`${BASE}/campaigns/${id}/stats`); setStats(data); }
    catch (e) { toast.error(errMessage(e)); }
  };
  const showPreview = async (id) => {
    try { const { data } = await api.post(`${BASE}/segments/${id}/preview`); setPreview(data); }
    catch (e) { toast.error(errMessage(e)); }
  };
  const processDue = async () => {
    try { const { data } = await api.post(`${BASE}/sequences/process-due`); toast.success(t("marketing.due_toast", { n: data.steps_sent })); }
    catch (e) { toast.error(errMessage(e)); }
  };

  return (
    <div data-testid="marketing-page">
      <PageHeader eyebrow={t("marketing.eyebrow")} title={t("marketing.title")} description={t("marketing.sub")} actions={
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => setShowNew("segment")} data-testid="new-segment-btn"><Users className="h-4 w-4 mr-1" />{t("marketing.new_segment")}</Button>
          <Button variant="outline" size="sm" onClick={() => setShowNew("sequence")} data-testid="new-sequence-btn"><ListOrdered className="h-4 w-4 mr-1" />{t("marketing.new_sequence")}</Button>
          <Button size="sm" onClick={() => setShowNew("campaign")} data-testid="new-campaign-btn"><Megaphone className="h-4 w-4 mr-1" />{t("marketing.new_campaign")}</Button>
        </div>
      } />
      <Tabs defaultValue="campaigns">
        <TabsList>
          <TabsTrigger value="campaigns" data-testid="mkt-tab-campaigns">{t("marketing.tab_campaigns")} ({campaigns.length})</TabsTrigger>
          <TabsTrigger value="segments" data-testid="mkt-tab-segments">{t("marketing.tab_segments")} ({segments.length})</TabsTrigger>
          <TabsTrigger value="sequences" data-testid="mkt-tab-sequences">{t("marketing.tab_sequences")} ({sequences.length})</TabsTrigger>
        </TabsList>

        <TabsContent value="campaigns">
          <div className="surface">
            {campaigns.length === 0 ? <Empty text={t("marketing.empty_campaigns")} /> : (
              <ul className="divide-y divide-border">
                {campaigns.map((c) => (
                  <li key={c.id} className="flex items-center gap-4 px-6 py-4" data-testid={`campaign-row-${c.id}`}>
                    <div className="flex-1 min-w-0">
                      <div className="font-medium">{c.name}</div>
                      <div className="text-xs text-muted-foreground">{c.channel} · {c.subject || "—"} · {t(`marketing.status_${c.status}`)}</div>
                    </div>
                    <Badge variant={c.status === "sent" ? "default" : "secondary"}>{c.status}</Badge>
                    <Button variant="ghost" size="sm" onClick={() => showStats(c.id)} data-testid={`campaign-stats-${c.id}`}><Eye className="h-4 w-4 mr-1" />{t("marketing.stats")}</Button>
                    <Button variant="ghost" size="sm" onClick={() => testSend(c.id)} data-testid={`campaign-test-${c.id}`}><FlaskConical className="h-4 w-4 mr-1" />{t("marketing.test_send")}</Button>
                    <Button variant="outline" size="sm" disabled={c.status !== "draft" || busy === c.id} onClick={() => send(c.id)} data-testid={`campaign-send-${c.id}`}><Send className="h-4 w-4 mr-1" />{t("marketing.send")}</Button>
                    <Button variant="ghost" size="icon" onClick={() => del("campaigns", c.id)}><Trash2 className="h-4 w-4" /></Button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          {stats && <StatsCard stats={stats} t={t} onClose={() => setStats(null)} />}
        </TabsContent>

        <TabsContent value="segments">
          <div className="surface">
            {segments.length === 0 ? <Empty text={t("marketing.empty_segments")} /> : (
              <ul className="divide-y divide-border">
                {segments.map((s) => (
                  <li key={s.id} className="flex items-center gap-4 px-6 py-4" data-testid={`segment-row-${s.id}`}>
                    <div className="flex-1 min-w-0">
                      <div className="font-medium">{s.name}</div>
                      <div className="text-xs text-muted-foreground font-mono">{s.rules.length} {t("marketing.rules")} · {s.match}</div>
                    </div>
                    <Button variant="ghost" size="sm" onClick={() => showPreview(s.id)} data-testid={`segment-preview-${s.id}`}><Eye className="h-4 w-4 mr-1" />{t("marketing.preview")}</Button>
                    <Button variant="ghost" size="icon" onClick={() => del("segments", s.id)}><Trash2 className="h-4 w-4" /></Button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          {preview && (
            <div className="surface mt-4 p-6" data-testid="segment-preview-card">
              <div className="font-medium mb-2">{t("marketing.preview_result", { n: preview.count })}</div>
              <ul className="text-sm text-muted-foreground space-y-1">
                {preview.sample.map((c) => <li key={c.id}>{c.name} — {c.email || c.phone}</li>)}
              </ul>
              <Button variant="ghost" size="sm" className="mt-2" onClick={() => setPreview(null)}>{t("marketing.close")}</Button>
            </div>
          )}
        </TabsContent>

        <TabsContent value="sequences">
          <div className="flex justify-end mb-2">
            <Button variant="outline" size="sm" onClick={processDue} data-testid="process-due-btn">{t("marketing.process_due")}</Button>
          </div>
          <div className="surface">
            {sequences.length === 0 ? <Empty text={t("marketing.empty_sequences")} /> : (
              <ul className="divide-y divide-border">
                {sequences.map((s) => (
                  <li key={s.id} className="flex items-center gap-4 px-6 py-4" data-testid={`sequence-row-${s.id}`}>
                    <div className="flex-1 min-w-0">
                      <div className="font-medium">{s.name}</div>
                      <div className="text-xs text-muted-foreground">
                        {s.steps.map((st, i) => `${t("marketing.day")} ${st.delay_days} · ${st.channel}`).join(" → ")}
                      </div>
                    </div>
                    <Badge variant="secondary">{s.steps.length} {t("marketing.steps")}</Badge>
                    <Button variant="ghost" size="icon" onClick={() => del("sequences", s.id)}><Trash2 className="h-4 w-4" /></Button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </TabsContent>
      </Tabs>

      {showNew === "campaign" && <CampaignForm segments={segments} sequences={sequences} t={t} onDone={() => { setShowNew(null); load(); }} onCancel={() => setShowNew(null)} />}
      {showNew === "segment" && <SegmentForm t={t} onDone={() => { setShowNew(null); load(); }} onCancel={() => setShowNew(null)} />}
      {showNew === "sequence" && <SequenceForm t={t} onDone={() => { setShowNew(null); load(); }} onCancel={() => setShowNew(null)} />}
    </div>
  );
}

function Empty({ text }) {
  return <div className="p-10 text-center text-sm text-muted-foreground">{text}</div>;
}

function StatsCard({ stats, t, onClose }) {
  return (
    <div className="surface mt-4 p-6" data-testid="campaign-stats-card">
      <div className="font-medium mb-2">{t("marketing.stats_title")}</div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
        <Stat label={t("marketing.stat_sent")} value={stats.total_sends} />
        <Stat label={t("marketing.stat_failed")} value={stats.by_status?.failed || 0} />
        <Stat label={t("marketing.stat_suppressed")} value={stats.suppressed} />
        <Stat label={t("marketing.stat_channels")} value={Object.entries(stats.by_channel || {}).map(([k, v]) => `${k}: ${v}`).join(", ") || "—"} />
      </div>
      <Button variant="ghost" size="sm" className="mt-3" onClick={onClose}>{t("marketing.close")}</Button>
    </div>
  );
}
function Stat({ label, value }) {
  return <div className="rounded border p-3"><div className="text-xs text-muted-foreground">{label}</div><div className="text-lg font-semibold">{value}</div></div>;
}

function CampaignForm({ segments, sequences, t, onDone, onCancel }) {
  const [f, setF] = useState({ name: "", channel: "email", segment_id: "", sequence_id: "", subject: "", body: "" });
  const save = async () => {
    try {
      await api.post(`${BASE}/campaigns`, { ...f, segment_id: f.segment_id || null, sequence_id: f.sequence_id || null });
      toast.success(t("marketing.created")); onDone();
    } catch (e) { toast.error(errMessage(e)); }
  };
  return (
    <FormShell title={t("marketing.new_campaign")} t={t} onCancel={onCancel} onSave={save} testid="campaign-form">
      <Input placeholder={t("marketing.field_name")} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} data-testid="cf-name" />
      <div className="flex gap-2">
        <select className="rounded border px-3 py-2 text-sm" value={f.channel} onChange={(e) => setF({ ...f, channel: e.target.value })} data-testid="cf-channel">
          <option value="email">{t("marketing.channel_email")}</option>
          <option value="sms">{t("marketing.channel_sms")}</option>
        </select>
        <select className="rounded border px-3 py-2 text-sm flex-1" value={f.segment_id} onChange={(e) => setF({ ...f, segment_id: e.target.value })} data-testid="cf-segment">
          <option value="">{t("marketing.pick_segment")}</option>
          {segments.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        <select className="rounded border px-3 py-2 text-sm flex-1" value={f.sequence_id} onChange={(e) => setF({ ...f, sequence_id: e.target.value })} data-testid="cf-sequence">
          <option value="">{t("marketing.no_sequence")}</option>
          {sequences.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </div>
      {f.channel === "email" && <Input placeholder={t("marketing.field_subject")} value={f.subject} onChange={(e) => setF({ ...f, subject: e.target.value })} data-testid="cf-subject" />}
      <Textarea rows={5} placeholder={t("marketing.field_body_hint")} value={f.body} onChange={(e) => setF({ ...f, body: e.target.value })} data-testid="cf-body" />
    </FormShell>
  );
}

function SegmentForm({ t, onDone, onCancel }) {
  const [name, setName] = useState("");
  const [match, setMatch] = useState("all");
  const [rules, setRules] = useState([{ field: "equipment_age_years", operator: "gte", value: "10" }]);
  const save = async () => {
    try { await api.post(`${BASE}/segments`, { name, match, rules }); toast.success(t("marketing.created")); onDone(); }
    catch (e) { toast.error(errMessage(e)); }
  };
  return (
    <FormShell title={t("marketing.new_segment")} t={t} onCancel={onCancel} onSave={save} testid="segment-form">
      <Input placeholder={t("marketing.field_name")} value={name} onChange={(e) => setName(e.target.value)} data-testid="sf-name" />
      <select className="rounded border px-3 py-2 text-sm w-40" value={match} onChange={(e) => setMatch(e.target.value)} data-testid="sf-match">
        <option value="all">{t("marketing.match_all")}</option>
        <option value="any">{t("marketing.match_any")}</option>
      </select>
      {rules.map((r, i) => (
        <div key={i} className="flex gap-2">
          <Input className="flex-1" placeholder={t("marketing.rule_field")} value={r.field} onChange={(e) => { const n = [...rules]; n[i].field = e.target.value; setRules(n); }} data-testid={`sf-field-${i}`} />
          <select className="rounded border px-2 py-2 text-sm" value={r.operator} onChange={(e) => { const n = [...rules]; n[i].operator = e.target.value; setRules(n); }} data-testid={`sf-op-${i}`}>
            {OPERATORS.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
          <Input className="w-32" placeholder={t("marketing.rule_value")} value={r.value} onChange={(e) => { const n = [...rules]; n[i].value = e.target.value; setRules(n); }} data-testid={`sf-value-${i}`} />
          <Button variant="ghost" size="icon" onClick={() => setRules(rules.filter((_, j) => j !== i))}><Trash2 className="h-4 w-4" /></Button>
        </div>
      ))}
      <Button variant="outline" size="sm" onClick={() => setRules([...rules, { field: "", operator: "equals", value: "" }])} data-testid="sf-add-rule"><Plus className="h-4 w-4 mr-1" />{t("marketing.add_rule")}</Button>
    </FormShell>
  );
}

function SequenceForm({ t, onDone, onCancel }) {
  const [name, setName] = useState("");
  const [steps, setSteps] = useState([{ channel: "email", delay_days: 0, subject: "", body: "" }]);
  const save = async () => {
    try {
      await api.post(`${BASE}/sequences`, { name, steps: steps.map((s) => ({ ...s, delay_days: Number(s.delay_days) || 0 })) });
      toast.success(t("marketing.created")); onDone();
    } catch (e) { toast.error(errMessage(e)); }
  };
  return (
    <FormShell title={t("marketing.new_sequence")} t={t} onCancel={onCancel} onSave={save} testid="sequence-form">
      <Input placeholder={t("marketing.field_name")} value={name} onChange={(e) => setName(e.target.value)} data-testid="qf-name" />
      {steps.map((s, i) => (
        <div key={i} className="rounded border p-3 space-y-2">
          <div className="flex gap-2 items-center">
            <Badge variant="secondary">{t("marketing.step_n", { n: i + 1 })}</Badge>
            <select className="rounded border px-2 py-1 text-sm" value={s.channel} onChange={(e) => { const n = [...steps]; n[i].channel = e.target.value; setSteps(n); }} data-testid={`qf-channel-${i}`}>
              <option value="email">{t("marketing.channel_email")}</option>
              <option value="sms">{t("marketing.channel_sms")}</option>
            </select>
            <Input type="number" min="0" className="w-28" placeholder={t("marketing.delay_days")} value={s.delay_days} onChange={(e) => { const n = [...steps]; n[i].delay_days = e.target.value; setSteps(n); }} data-testid={`qf-delay-${i}`} />
            <Button variant="ghost" size="icon" className="ml-auto" onClick={() => setSteps(steps.filter((_, j) => j !== i))}><Trash2 className="h-4 w-4" /></Button>
          </div>
          {s.channel === "email" && <Input placeholder={t("marketing.field_subject")} value={s.subject} onChange={(e) => { const n = [...steps]; n[i].subject = e.target.value; setSteps(n); }} data-testid={`qf-subject-${i}`} />}
          <Textarea rows={3} placeholder={t("marketing.field_body_hint")} value={s.body} onChange={(e) => { const n = [...steps]; n[i].body = e.target.value; setSteps(n); }} data-testid={`qf-body-${i}`} />
        </div>
      ))}
      <Button variant="outline" size="sm" onClick={() => setSteps([...steps, { channel: "email", delay_days: 3, subject: "", body: "" }])} data-testid="qf-add-step"><Plus className="h-4 w-4 mr-1" />{t("marketing.add_step")}</Button>
    </FormShell>
  );
}

function FormShell({ title, t, onCancel, onSave, children, testid }) {
  return (
    <div className="surface mt-4 p-6 space-y-3" data-testid={testid}>
      <div className="font-medium">{title}</div>
      {children}
      <div className="flex gap-2 justify-end">
        <Button variant="ghost" onClick={onCancel}>{t("marketing.cancel")}</Button>
        <Button onClick={onSave} data-testid={`${testid}-save`}>{t("marketing.save")}</Button>
      </div>
    </div>
  );
}
