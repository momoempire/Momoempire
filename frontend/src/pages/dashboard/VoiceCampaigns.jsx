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
import { PhoneOutgoing, Plus, FlaskConical, Send, PhoneCall, FileText, Eye } from "lucide-react";

const BASE = "/tenants/voice";

export default function VoiceCampaigns() {
  const { t } = useTranslation();
  const [templates, setTemplates] = useState([]);
  const [campaigns, setCampaigns] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [showNew, setShowNew] = useState(false);
  const [name, setName] = useState("");
  const [templateId, setTemplateId] = useState("");
  const [audienceJson, setAudienceJson] = useState("[\n  {\n    \"customer_id\": \"\",\n    \"phone\": \"+1\",\n    \"variables\": {}\n  }\n]");
  const [dryRun, setDryRun] = useState(null); // launch plan preview
  const [dryRunFor, setDryRunFor] = useState(null);
  const [selected, setSelected] = useState(null); // campaign id for call log
  const [calls, setCalls] = useState([]);
  const [busy, setBusy] = useState(null);

  const load = () => {
    api.get(`${BASE}/templates`).then((r) => { setTemplates(r.data); if (!templateId && r.data[0]) setTemplateId(r.data[0].id); }).catch(() => {});
    api.get(`${BASE}/campaigns`).then((r) => setCampaigns(r.data)).catch(() => {});
    api.get(`${BASE}/jobs`).then((r) => setJobs(r.data)).catch(() => {});
  };
  useEffect(() => { load(); }, []);

  const create = async () => {
    let audience;
    try {
      audience = JSON.parse(audienceJson);
      if (!Array.isArray(audience) || audience.length === 0) throw new Error("array");
    } catch (e) { toast.error(t("voice.audience_invalid")); return; }
    setBusy("create");
    try {
      const { data } = await api.post(`${BASE}/campaigns`, { name, template_id: templateId, audience });
      toast.success(t("voice.created"));
      setShowNew(false); setName(""); setDryRun(null);
      load();
      return data.id;
    } catch (e) { toast.error(errMessage(e)); }
    finally { setBusy(null); }
  };

  const previewLaunch = async (id) => {
    setBusy(id);
    try {
      const { data } = await api.post(`${BASE}/campaigns/${id}/launch`, { dry_run: true });
      setDryRun(data); setDryRunFor(id);
    } catch (e) { toast.error(errMessage(e)); }
    finally { setBusy(null); }
  };

  const launch = async (id) => {
    if (!confirm(t("voice.confirm_launch"))) return;
    setBusy(id);
    try {
      const { data } = await api.post(`${BASE}/campaigns/${id}/launch`, { dry_run: false });
      toast.success(t("voice.launched_toast", { n: data.enqueued }));
      setDryRun(null); load();
    } catch (e) { toast.error(errMessage(e)); }
    finally { setBusy(null); }
  };

  const showCalls = async (id) => {
    setSelected(id);
    try { const { data } = await api.get(`${BASE}/campaigns/${id}/calls`); setCalls(data); }
    catch (e) { toast.error(errMessage(e)); }
  };

  const selectedTemplate = templates.find((x) => x.id === templateId);

  return (
    <div data-testid="voice-page">
      <PageHeader eyebrow={t("voice.eyebrow")} title={t("voice.title")} description={t("voice.sub")} actions={
        <Button size="sm" onClick={() => setShowNew(true)} data-testid="new-campaign-btn">
          <Plus className="h-4 w-4 mr-1" />{t("voice.new_campaign")}
        </Button>
      } />
      <Tabs defaultValue="campaigns">
        <TabsList>
          <TabsTrigger value="campaigns" data-testid="voice-tab-campaigns">{t("voice.tab_campaigns")} ({campaigns.length})</TabsTrigger>
          <TabsTrigger value="templates" data-testid="voice-tab-templates">{t("voice.tab_templates")}</TabsTrigger>
          <TabsTrigger value="calls" data-testid="voice-tab-calls">{t("voice.tab_calls")}</TabsTrigger>
          <TabsTrigger value="queue" data-testid="voice-tab-queue">{t("voice.tab_queue")} ({jobs.length})</TabsTrigger>
        </TabsList>

        <TabsContent value="campaigns">
          {showNew && (
            <div className="border rounded-lg p-4 mb-4 space-y-3" data-testid="campaign-form">
              <Input placeholder={t("voice.field_name")} value={name} onChange={(e) => setName(e.target.value)} />
              <select className="border rounded px-2 py-1 w-full" value={templateId} onChange={(e) => setTemplateId(e.target.value)} data-testid="template-select">
                {templates.map((tpl) => <option key={tpl.id} value={tpl.id}>{tpl.name}</option>)}
              </select>
              {selectedTemplate && (
                <p className="text-xs text-muted-foreground">{t("voice.variables_hint")}: {selectedTemplate.variables.join(", ")}</p>
              )}
              <Textarea rows={8} value={audienceJson} onChange={(e) => setAudienceJson(e.target.value)} data-testid="audience-json" />
              <div className="flex gap-2">
                <Button size="sm" disabled={busy === "create"} onClick={create}>{t("voice.save")}</Button>
                <Button size="sm" variant="outline" onClick={() => setShowNew(false)}>{t("voice.cancel")}</Button>
              </div>
            </div>
          )}
          {campaigns.length === 0 && <p className="text-sm text-muted-foreground">{t("voice.empty_campaigns")}</p>}
          {campaigns.map((c) => (
            <div key={c.id} className="border rounded-lg p-3 mb-2 flex items-center justify-between" data-testid={`campaign-row-${c.id}`}>
              <div>
                <div className="font-medium">{c.name}</div>
                <div className="text-xs text-muted-foreground">{c.template_id} · {(c.audience || []).length} {t("voice.calls")} · <Badge>{c.status}</Badge></div>
              </div>
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={() => previewLaunch(c.id)} disabled={busy === c.id} data-testid={`dry-run-${c.id}`}>
                  <FlaskConical className="h-4 w-4 mr-1" />{t("voice.dry_run")}
                </Button>
                <Button size="sm" variant="outline" onClick={() => showCalls(c.id)} data-testid={`calls-${c.id}`}>
                  <Eye className="h-4 w-4 mr-1" />{t("voice.tab_calls")}
                </Button>
                {c.status !== "launched" && (
                  <Button size="sm" onClick={() => launch(c.id)} disabled={busy === c.id} data-testid={`launch-${c.id}`}>
                    <Send className="h-4 w-4 mr-1" />{t("voice.launch")}
                  </Button>
                )}
              </div>
            </div>
          ))}
          {dryRun && (
            <div className="border rounded-lg p-4 mt-4" data-testid="dry-run-preview">
              <h3 className="font-medium mb-2">{t("voice.dry_run_title")}: {dryRunFor}</h3>
              <p className="text-sm">{t("voice.planned_calls", { n: dryRun.call_list.length })} · {t("voice.estimated_minutes", { n: dryRun.estimated_minutes })}</p>
              <ol className="text-sm list-decimal ml-5 my-2">
                {dryRun.order.map((p) => <li key={p}>{p}</li>)}
              </ol>
              {dryRun.blocked.length > 0 && (
                <div className="text-sm text-red-600">{t("voice.blocked_consent", { n: dryRun.blocked.length })}: {dryRun.blocked.map((b) => b.phone).join(", ")}</div>
              )}
              <div className="flex gap-2 mt-3">
                <Button size="sm" onClick={() => launch(dryRunFor)}><Send className="h-4 w-4 mr-1" />{t("voice.launch")}</Button>
                <Button size="sm" variant="outline" onClick={() => setDryRun(null)}>{t("voice.close")}</Button>
              </div>
            </div>
          )}
        </TabsContent>

        <TabsContent value="templates">
          {templates.map((tpl) => (
            <div key={tpl.id} className="border rounded-lg p-4 mb-2" data-testid={`template-${tpl.id}`}>
              <div className="font-medium flex items-center gap-2"><FileText className="h-4 w-4" />{tpl.name}</div>
              <p className="text-sm mt-1 italic">“{tpl.script}”</p>
              <div className="text-xs text-muted-foreground mt-1">{t("voice.variables_hint")}: {tpl.variables.join(", ")}</div>
            </div>
          ))}
        </TabsContent>

        <TabsContent value="calls">
          {!selected && <p className="text-sm text-muted-foreground">{t("voice.pick_campaign")}</p>}
          {selected && calls.length === 0 && <p className="text-sm text-muted-foreground">{t("voice.empty_calls")}</p>}
          {calls.map((c) => (
            <div key={c.id} className="border rounded-lg p-3 mb-2 text-sm" data-testid={`call-row-${c.id}`}>
              <div className="flex items-center gap-2">
                <PhoneCall className="h-4 w-4" />
                <span className="font-medium">{c.phone}</span>
                <Badge>{c.status}</Badge>
                <Badge>{c.outcome}</Badge>
                <span className="text-muted-foreground">{c.duration_seconds}s</span>
              </div>
              <p className="text-xs text-muted-foreground mt-1">{c.transcript}</p>
            </div>
          ))}
        </TabsContent>

        <TabsContent value="queue">
          {jobs.length === 0 && <p className="text-sm text-muted-foreground">{t("voice.empty_queue")}</p>}
          {jobs.map((j) => (
            <div key={j.id} className="border rounded-lg p-3 mb-2 text-sm" data-testid={`job-row-${j.id}`}>
              <span className="font-medium">{j.phone}</span>
              <span className="text-muted-foreground"> · {j.kind} · {t("voice.due_at")}: {j.due_at}</span>
              <Badge className="ml-2">{j.priority}</Badge>
            </div>
          ))}
        </TabsContent>
      </Tabs>
    </div>
  );
}
