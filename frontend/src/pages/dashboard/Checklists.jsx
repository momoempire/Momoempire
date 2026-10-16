import { useEffect, useMemo, useState } from "react";
import { api, errMessage } from "@/lib/api";
import { useTranslation } from "react-i18next";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";
import {
  ClipboardCheck, Plus, Pencil, Trash2, Loader2, ChevronUp, ChevronDown,
  Check, RotateCcw, Camera, ShieldCheck, ShieldAlert,
} from "lucide-react";

const BASE = "/checklists";
const TYPES = ["check", "text", "photo", "number"];

function newItem() {
  return { id: `tmp_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`, label: "", type: "check", required: false };
}

/* ---------------- template builder ---------------- */

function TemplateEditor({ initial, onDone, onCancel, t }) {
  const [name, setName] = useState(initial?.name || "");
  const [trade, setTrade] = useState(initial?.trade || "");
  const [items, setItems] = useState(() =>
    (initial?.items || []).map((i) => ({ ...i, id: i.id || `tmp_${Math.random().toString(36).slice(2, 7)}` }))
  );
  const [saving, setSaving] = useState(false);

  const updateItem = (id, patch) =>
    setItems((list) => list.map((i) => (i.id === id ? { ...i, ...patch } : i)));
  const removeItem = (id) => setItems((list) => list.filter((i) => i.id !== id));
  const moveItem = (idx, dir) =>
    setItems((list) => {
      const j = idx + dir;
      if (j < 0 || j >= list.length) return list;
      const next = [...list];
      [next[idx], next[j]] = [next[j], next[idx]];
      return next;
    });

  const save = async () => {
    setSaving(true);
    try {
      const payload = {
        name: name.trim(),
        trade: trade.trim(),
        items: items
          .filter((i) => i.label.trim())
          .map((i) => ({ label: i.label.trim(), type: i.type, required: !!i.required })),
      };
      if (initial?.id) {
        await api.put(`${BASE}/templates/${initial.id}`, payload);
      } else {
        await api.post(`${BASE}/templates`, payload);
      }
      toast.success(t("checklists.template_saved_toast"));
      onDone();
    } catch (e) {
      toast.error(errMessage(e) || t("checklists.save_error"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="surface p-4 space-y-4" data-testid="checklists-template-editor">
      <div className="grid sm:grid-cols-2 gap-3">
        <div className="space-y-1">
          <Label>{t("checklists.template_name")}</Label>
          <Input value={name} onChange={(e) => setName(e.target.value)}
            placeholder={t("checklists.template_name_placeholder")}
            data-testid="checklists-template-name" />
        </div>
        <div className="space-y-1">
          <Label>{t("checklists.template_trade")}</Label>
          <Input value={trade} onChange={(e) => setTrade(e.target.value)}
            placeholder={t("checklists.template_trade_placeholder")}
            data-testid="checklists-template-trade" />
        </div>
      </div>

      <div className="space-y-2">
        <Label>{t("checklists.items_heading")}</Label>
        {items.length === 0 && (
          <p className="text-sm text-muted-foreground">{t("checklists.no_items")}</p>
        )}
        {items.map((item, idx) => (
          <div key={item.id} className="flex items-center gap-2 border rounded-lg p-2">
            <div className="flex flex-col">
              <Button variant="ghost" size="sm" className="h-6 w-8 px-0" onClick={() => moveItem(idx, -1)}
                disabled={idx === 0} aria-label={t("checklists.move_up")}><ChevronUp className="h-4 w-4" /></Button>
              <Button variant="ghost" size="sm" className="h-6 w-8 px-0" onClick={() => moveItem(idx, 1)}
                disabled={idx === items.length - 1} aria-label={t("checklists.move_down")}><ChevronDown className="h-4 w-4" /></Button>
            </div>
            <Input value={item.label} onChange={(e) => updateItem(item.id, { label: e.target.value })}
              placeholder={t("checklists.item_label_placeholder")} className="flex-1" />
            <select
              value={item.type}
              onChange={(e) => updateItem(item.id, { type: e.target.value })}
              className="h-9 rounded-md border bg-background px-2 text-sm"
              aria-label={t("checklists.item_type")}
            >
              {TYPES.map((ty) => (
                <option key={ty} value={ty}>{t(`checklists.type_${ty}`)}</option>
              ))}
            </select>
            <label className="flex items-center gap-1 text-xs whitespace-nowrap cursor-pointer">
              <input type="checkbox" checked={item.required}
                onChange={(e) => updateItem(item.id, { required: e.target.checked })} />
              {t("checklists.item_required")}
            </label>
            <Button variant="ghost" size="sm" className="h-8 w-8 px-0" onClick={() => removeItem(item.id)}
              aria-label={t("checklists.remove_item")}><Trash2 className="h-4 w-4" /></Button>
          </div>
        ))}
        <Button variant="outline" size="sm" onClick={() => setItems((list) => [...list, newItem()])}>
          <Plus className="h-4 w-4 mr-1" /> {t("checklists.add_item")}
        </Button>
      </div>

      <div className="flex gap-2">
        <Button onClick={save} disabled={saving || !name.trim()}>
          {saving && <Loader2 className="h-4 w-4 mr-1 animate-spin" />}
          {t("checklists.save_template")}
        </Button>
        <Button variant="ghost" onClick={onCancel}>{t("checklists.cancel")}</Button>
      </div>
    </div>
  );
}

function TemplatesTab({ t }) {
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(null); // template object or "new"

  const load = async () => {
    setLoading(true);
    try {
      const res = await api.get(`${BASE}/templates`);
      setTemplates(res.data.templates || []);
    } catch (e) {
      toast.error(errMessage(e) || t("checklists.load_error"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const remove = async (tpl) => {
    if (tpl.builtin) return;
    if (!window.confirm(t("checklists.delete_confirm"))) return;
    try {
      await api.delete(`${BASE}/templates/${tpl.id}`);
      toast.success(t("checklists.template_deleted_toast"));
      load();
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  if (editing) {
    return (
      <TemplateEditor
        initial={editing === "new" ? null : editing}
        onDone={() => { setEditing(null); load(); }}
        onCancel={() => setEditing(null)}
        t={t}
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">{t("checklists.templates_heading")}</h2>
        <Button onClick={() => setEditing("new")} data-testid="checklists-new-template">
          <Plus className="h-4 w-4 mr-1" /> {t("checklists.new_template")}
        </Button>
      </div>
      {loading ? (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> {t("checklists.loading")}
        </div>
      ) : templates.length === 0 ? (
        <div className="surface p-8 text-center text-muted-foreground">{t("checklists.templates_empty")}</div>
      ) : (
        <div className="grid md:grid-cols-2 gap-4">
          {templates.map((tpl) => (
            <div key={tpl.id} className="surface p-4 space-y-2" data-testid={`checklists-template-${tpl.id}`}>
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="font-medium">{tpl.name}</div>
                  <div className="text-xs text-muted-foreground">
                    {tpl.trade || t("checklists.no_labels")} · {tpl.items.length} {t("checklists.items_heading").toLowerCase()}
                  </div>
                </div>
                {tpl.builtin && <Badge variant="secondary">{t("checklists.builtin")}</Badge>}
              </div>
              <ul className="text-sm space-y-1 max-h-40 overflow-y-auto">
                {tpl.items.map((it) => (
                  <li key={it.id} className="flex items-center gap-2">
                    <span className="truncate flex-1">{it.label}</span>
                    <span className="text-xs text-muted-foreground">{t(`checklists.type_${it.type}`)}</span>
                    {it.required && <Badge variant="outline" className="text-xs">{t("checklists.required_badge")}</Badge>}
                  </li>
                ))}
              </ul>
              <div className="flex gap-2 pt-1">
                <Button variant="outline" size="sm" onClick={() => setEditing(tpl)}>
                  <Pencil className="h-3 w-3 mr-1" /> {t("checklists.edit")}
                </Button>
                {!tpl.builtin && (
                  <Button variant="ghost" size="sm" onClick={() => remove(tpl)}>
                    <Trash2 className="h-3 w-3 mr-1" /> {t("checklists.delete")}
                  </Button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ---------------- runner ---------------- */

function ItemInput({ item, onChange, disabled, t }) {
  if (item.type === "check") return null;
  if (item.type === "photo") {
    return (
      <div className="flex items-center gap-2 mt-2">
        <Camera className="h-4 w-4 text-muted-foreground shrink-0" />
        <Input value={item.photo_url || ""} onChange={(e) => onChange("photo_url", e.target.value)}
          placeholder={t("checklists.photo_url_placeholder")} disabled={disabled} className="flex-1" />
      </div>
    );
  }
  return (
    <Input
      type={item.type === "number" ? "number" : "text"}
      value={item.value || ""}
      onChange={(e) => onChange("value", e.target.value)}
      placeholder={t("checklists.value_placeholder")}
      disabled={disabled}
      className="mt-2"
    />
  );
}

function RunCard({ instance, t, onUpdated }) {
  const [busy, setBusy] = useState({});
  const [gate, setGate] = useState(null);
  const [loadingGate, setLoadingGate] = useState(false);

  const loadGate = async () => {
    setLoadingGate(true);
    try {
      const res = await api.get(`${BASE}/instances/${instance.id}/gate`);
      setGate(res.data);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoadingGate(false);
    }
  };

  useEffect(() => { loadGate(); }, [instance.id, instance.updated_at]);

  const complete = async (item, done, patch = {}) => {
    setBusy((b) => ({ ...b, [item.item_id]: true }));
    try {
      const res = await api.post(
        `${BASE}/instances/${instance.id}/items/${item.item_id}/complete`,
        { done, value: patch.value ?? item.value, note: patch.note ?? item.note, photo_url: patch.photo_url ?? item.photo_url }
      );
      onUpdated(res.data);
    } catch (e) {
      toast.error(errMessage(e) || t("checklists.complete_error"));
    } finally {
      setBusy((b) => ({ ...b, [item.item_id]: false }));
    }
  };

  const prog = instance.progress || { required_done: 0, required_total: 0 };
  const pct = prog.required_total ? Math.round((prog.required_done / prog.required_total) * 100) : 100;

  return (
    <div className="surface p-4 space-y-3" data-testid={`checklists-run-${instance.id}`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="font-medium">{instance.template_name}</div>
          <div className="text-xs text-muted-foreground">
            {t("checklists.progress_required", { done: prog.required_done, total: prog.required_total })}
          </div>
        </div>
        {loadingGate ? (
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        ) : gate && (
          <Badge variant={gate.can_complete ? "default" : "secondary"}>
            {gate.can_complete ? (
              <><ShieldCheck className="h-3 w-3 mr-1" /> {t("checklists.ready_badge")}</>
            ) : (
              <><ShieldAlert className="h-3 w-3 mr-1" /> {t("checklists.blocked_badge")}</>
            )}
          </Badge>
        )}
      </div>

      <div className="h-2 rounded-full bg-muted overflow-hidden" aria-hidden="true">
        <div className={`h-full ${pct === 100 ? "bg-emerald-500" : "bg-sky-500"}`} style={{ width: `${pct}%` }} />
      </div>

      <div className="space-y-2">
        {instance.items.map((item) => (
          <div key={item.item_id}
            className={`border rounded-xl p-3 ${item.done ? "bg-muted/40" : ""}`}
            data-testid={`checklists-item-${item.item_id}`}>
            <div className="flex items-start gap-3">
              <button
                type="button"
                onClick={() => complete(item, !item.done)}
                disabled={busy[item.item_id]}
                aria-pressed={!!item.done}
                aria-label={item.label}
                className={`mt-0.5 h-10 w-10 shrink-0 rounded-lg border-2 flex items-center justify-center transition-colors
                  ${item.done ? "bg-emerald-500 border-emerald-500 text-white" : "border-muted-foreground/40 hover:border-sky-500"}`}
              >
                {busy[item.item_id]
                  ? <Loader2 className="h-5 w-5 animate-spin" />
                  : item.done ? <Check className="h-6 w-6" /> : null}
              </button>
              <div className="flex-1 min-w-0">
                <div className={`font-medium text-base ${item.done ? "line-through text-muted-foreground" : ""}`}>
                  {item.label}
                </div>
                <div className="flex items-center gap-2 mt-0.5">
                  <span className="text-xs text-muted-foreground">{t(`checklists.type_${item.type}`)}</span>
                  {item.required && <Badge variant="outline" className="text-xs">{t("checklists.required_badge")}</Badge>}
                  {item.done && <Badge variant="secondary" className="text-xs">{t("checklists.done_badge")}</Badge>}
                </div>
                {!item.done && (
                  <ItemInput
                    item={item} t={t}
                    onChange={(field, val) => onUpdated({
                      ...instance,
                      items: instance.items.map((i) => i.item_id === item.item_id ? { ...i, [field]: val } : i),
                    })}
                    disabled={false}
                  />
                )}
                {item.note && (
                  <div className="text-sm text-muted-foreground mt-1">{item.note}</div>
                )}
                {item.done && (item.value || item.photo_url) && (
                  <div className="text-sm text-muted-foreground mt-1 break-all">
                    {item.value}
                    {item.photo_url && <a href={item.photo_url} target="_blank" rel="noreferrer"
                      className="text-sky-600 underline block">{item.photo_url}</a>}
                  </div>
                )}
                {item.done && (
                  <Button variant="ghost" size="sm" className="mt-1 h-7 px-2 text-xs"
                    onClick={() => complete(item, false)}>
                    <RotateCcw className="h-3 w-3 mr-1" /> {t("checklists.reopen")}
                  </Button>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      {gate && !gate.can_complete && (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm space-y-1">
          <div className="font-medium text-amber-700 dark:text-amber-300">
            {t("checklists.gate_blocked", { n: gate.blocking.length })}
          </div>
          <div className="text-xs text-muted-foreground">{t("checklists.blocking_items")}</div>
          <ul className="list-disc list-inside text-xs">
            {gate.blocking.map((label, i) => <li key={i}>{label}</li>)}
          </ul>
        </div>
      )}
      {gate?.can_complete && (
        <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm text-emerald-700 dark:text-emerald-300">
          {t("checklists.gate_ready")}
        </div>
      )}
    </div>
  );
}

function RunnerTab({ t }) {
  const [templates, setTemplates] = useState([]);
  const [jobId, setJobId] = useState("");
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(false);
  const [starting, setStarting] = useState(false);
  const [templateId, setTemplateId] = useState("");

  const loadTemplates = async () => {
    try {
      const res = await api.get(`${BASE}/templates`);
      setTemplates(res.data.templates || []);
      setTemplateId((id) => id || (res.data.templates?.[0]?.id ?? ""));
    } catch (e) {
      toast.error(errMessage(e) || t("checklists.load_error"));
    }
  };

  const loadRuns = async (jid) => {
    if (!jid.trim()) { setRuns([]); return; }
    setLoading(true);
    try {
      const res = await api.get(`${BASE}/instances`, { params: { job_id: jid.trim() } });
      setRuns(res.data.instances || []);
    } catch (e) {
      toast.error(errMessage(e) || t("checklists.load_error"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadTemplates(); }, []);

  const start = async () => {
    if (!jobId.trim() || !templateId) return;
    setStarting(true);
    try {
      const res = await api.post(`${BASE}/instances`, { template_id: templateId, job_id: jobId.trim() });
      toast.success(t("checklists.run_started_toast"));
      setRuns((list) => [res.data, ...list]);
    } catch (e) {
      toast.error(errMessage(e) || t("checklists.start_error"));
    } finally {
      setStarting(false);
    }
  };

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold">{t("checklists.runner_heading")}</h2>

      <div className="surface p-4 space-y-3">
        <div className="grid sm:grid-cols-[1fr_1fr_auto] gap-3 items-end">
          <div className="space-y-1">
            <Label>{t("checklists.job_id_label")}</Label>
            <Input value={jobId} onChange={(e) => setJobId(e.target.value)}
              placeholder={t("checklists.job_id_placeholder")}
              data-testid="checklists-job-id"
              onKeyDown={(e) => { if (e.key === "Enter") loadRuns(jobId); }} />
          </div>
          <div className="space-y-1">
            <Label>{t("checklists.template_label")}</Label>
            <select value={templateId} onChange={(e) => setTemplateId(e.target.value)}
              className="h-10 w-full rounded-md border bg-background px-2 text-sm"
              data-testid="checklists-template-select">
              {templates.map((tpl) => (
                <option key={tpl.id} value={tpl.id}>{tpl.name}{tpl.trade ? ` (${tpl.trade})` : ""}</option>
              ))}
            </select>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => loadRuns(jobId)} disabled={!jobId.trim()}>
              {t("checklists.runs_for_job")}
            </Button>
            <Button onClick={start} disabled={starting || !jobId.trim() || !templateId}>
              {starting && <Loader2 className="h-4 w-4 mr-1 animate-spin" />}
              {t("checklists.start_run")}
            </Button>
          </div>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> {t("checklists.loading")}
        </div>
      ) : runs.length === 0 ? (
        <div className="surface p-8 text-center text-muted-foreground">{t("checklists.no_runs")}</div>
      ) : (
        <div className="space-y-4">
          {runs.map((run) => (
            <RunCard key={run.id} instance={run} t={t}
              onUpdated={(updated) => setRuns((list) => list.map((r) => (r.id === updated.id ? updated : r)))} />
          ))}
        </div>
      )}
    </div>
  );
}

export default function Checklists() {
  const { t } = useTranslation();
  const [tab, setTab] = useState("runner");

  const tabs = useMemo(() => ([
    { id: "runner", label: t("checklists.tab_runner"), icon: ClipboardCheck },
    { id: "templates", label: t("checklists.tab_templates"), icon: Pencil },
  ]), [t]);

  return (
    <div className="space-y-6" data-testid="checklists-page">
      <PageHeader
        eyebrow={t("checklists.eyebrow")}
        title={t("checklists.title")}
        description={t("checklists.sub")}
      />

      <div className="flex gap-2" role="tablist" aria-label={t("checklists.title")}>
        {tabs.map((tb) => (
          <Button key={tb.id} variant={tab === tb.id ? "default" : "outline"} size="sm"
            role="tab" aria-selected={tab === tb.id}
            onClick={() => setTab(tb.id)}
            data-testid={`checklists-tab-${tb.id}`}>
            <tb.icon className="h-4 w-4 mr-1" /> {tb.label}
          </Button>
        ))}
      </div>

      {tab === "runner" ? <RunnerTab t={t} /> : <TemplatesTab t={t} />}
    </div>
  );
}
