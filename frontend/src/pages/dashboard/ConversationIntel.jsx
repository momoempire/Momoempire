import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errMessage } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import {
  Smile, Meh, Frown, Flame, Bell, Settings, MessageSquare, Zap,
  CheckCircle, Search, TrendingUp, ShieldAlert, Activity,
} from "lucide-react";

const API = "/conversation-intel";

const SENTIMENT_STYLE = {
  positive: { cls: "bg-emerald-100 text-emerald-800", icon: Smile, labelKey: "ci.positive", fallback: "Positive" },
  neutral: { cls: "bg-amber-100 text-amber-800", icon: Meh, labelKey: "ci.neutral", fallback: "Neutral" },
  negative: { cls: "bg-rose-100 text-rose-800", icon: Frown, labelKey: "ci.negative", fallback: "Negative" },
};

const SEVERITY_STYLE = {
  critical: { cls: "bg-red-600 text-white", labelKey: "ci.critical", fallback: "Critical" },
  high: { cls: "bg-orange-100 text-orange-800", labelKey: "ci.high", fallback: "High" },
  medium: { cls: "bg-sky-100 text-sky-800", labelKey: "ci.medium", fallback: "Medium" },
  low: { cls: "bg-slate-100 text-slate-700", labelKey: "ci.low", fallback: "Low" },
};

function SentimentChip({ label }) {
  const { t } = useTranslation();
  const s = SENTIMENT_STYLE[label] || SENTIMENT_STYLE.neutral;
  const Icon = s.icon;
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${s.cls}`}>
      <Icon className="h-3 w-3" /> {t(s.labelKey, s.fallback)}
    </span>
  );
}

function SeverityBadge({ severity }) {
  const { t } = useTranslation();
  const s = SEVERITY_STYLE[severity] || SEVERITY_STYLE.medium;
  return <Badge className={s.cls}>{t(s.labelKey, s.fallback)}</Badge>;
}

function RulesEditor({ rules, onSaved }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const next = {};
    for (const r of rules || []) {
      next[r.key] = {
        enabled: r.enabled !== false,
        severity: r.severity || "medium",
        auto_ticket: !!r.auto_ticket,
        params: { ...(r.params || {}) },
      };
    }
    setForm(next);
  }, [rules]);

  const set = (key, patch) => setForm((f) => ({ ...f, [key]: { ...f[key], ...patch } }));
  const setParam = (key, pkey, value) =>
    setForm((f) => ({ ...f, [key]: { ...f[key], params: { ...f[key].params, [pkey]: value } } }));

  const save = async () => {
    setSaving(true);
    try {
      const payload = {
        rules: (rules || []).map((r) => ({
          key: r.key,
          name: r.name || r.key,
          description: r.description || "",
          enabled: form[r.key]?.enabled ?? true,
          severity: form[r.key]?.severity || "medium",
          auto_ticket: form[r.key]?.auto_ticket ?? false,
          params: form[r.key]?.params || {},
        })),
      };
      const resp = await api.put(API + "/rules", payload);
      onSaved(resp.data.rules || []);
      toast.success(t("conversationIntel.rules_saved", "Escalation rules saved."));
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setSaving(false);
    }
  };

  if (!rules || rules.length === 0) return null;

  return (
    <div className="rounded-xl border bg-card p-5" data-testid="ci-rules">
      <div className="flex items-center gap-2 mb-1">
        <Settings className="h-4 w-4 text-muted-foreground" />
        <h3 className="font-semibold text-sm">{t("conversationIntel.rules_title", "Escalation rules")}</h3>
      </div>
      <p className="text-xs text-muted-foreground mb-4">
        {t("conversationIntel.rules_hint", "Fired automatically every time a thread is analyzed. Keyword scoring is template-based — no LLM.")}
      </p>
      <div className="space-y-4">
        {rules.map((r) => {
          const f = form[r.key] || { enabled: true, severity: "medium", auto_ticket: false, params: {} };
          return (
            <div key={r.key} className="rounded-lg border p-3" data-testid={`ci-rule-${r.key}`}>
              <div className="flex flex-wrap items-center gap-2 mb-1">
                <label className="flex items-center gap-2 text-sm font-medium cursor-pointer">
                  <input type="checkbox" checked={f.enabled} onChange={(e) => set(r.key, { enabled: e.target.checked })} className="rounded" />
                  {r.name || r.key}
                </label>
                <select
                  value={f.severity}
                  onChange={(e) => set(r.key, { severity: e.target.value })}
                  className="rounded-md border bg-card px-2 py-1 text-xs"
                >
                  {["critical", "high", "medium", "low"].map((s) => (
                    <option key={s} value={s}>{t(`ci.${s}`, s)}</option>
                  ))}
                </select>
                {f.enabled && <SeverityBadge severity={f.severity} />}
              </div>
              {r.description && <p className="text-xs text-muted-foreground mb-2">{r.description}</p>}
              <div className="flex flex-wrap items-center gap-2 mb-2">
                {Object.entries(f.params || {}).map(([pkey, pval]) => (
                  <label key={pkey} className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    {pkey}
                    <Input
                      type={typeof pval === "number" ? "number" : "text"}
                      value={pval}
                      onChange={(e) => {
                        const v = typeof pval === "number" ? Number(e.target.value) : e.target.value;
                        setParam(r.key, pkey, v);
                      }}
                      className="h-7 w-20 text-xs"
                    />
                  </label>
                ))}
                <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
                  <input type="checkbox" checked={f.auto_ticket} onChange={(e) => set(r.key, { auto_ticket: e.target.checked })} className="rounded" />
                  {t("conversationIntel.auto_ticket", "Auto-open support ticket")}
                </label>
              </div>
            </div>
          );
        })}
      </div>
      <Button size="sm" className="mt-4" onClick={save} disabled={saving} data-testid="ci-rules-save">
        {saving ? t("conversationIntel.saving", "Saving…") : t("conversationIntel.save_rules", "Save rules")}
      </Button>
    </div>
  );
}

export default function ConversationIntel() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const canManage = user && ["owner", "admin"].includes(user.role);

  const [overview, setOverview] = useState(null);
  const [escalations, setEscalations] = useState([]);
  const [escStatus, setEscStatus] = useState("open");
  const [rules, setRules] = useState([]);
  const [loading, setLoading] = useState(true);
  const [threadId, setThreadId] = useState("");
  const [timeline, setTimeline] = useState(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [resolving, setResolving] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [ov, eq, rl] = await Promise.all([
        api.get(API + "/sentiment/overview"),
        api.get(API + "/escalations", { params: { status: escStatus } }),
        api.get(API + "/rules"),
      ]);
      setOverview(ov.data);
      setEscalations(eq.data.escalations || []);
      setRules(rl.data.rules || []);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  }, [escStatus]);

  useEffect(() => { load(); }, [load]);

  const analyze = async () => {
    const id = threadId.trim();
    if (!id) return toast.error(t("conversationIntel.thread_id_required", "Enter a thread ID to analyze."));
    setAnalyzing(true);
    setTimeline(null);
    try {
      await api.post(`${API}/analyze/thread/${encodeURIComponent(id)}`);
      const tl = await api.get(`${API}/threads/${encodeURIComponent(id)}/timeline`);
      setTimeline(tl.data);
      toast.success(t("conversationIntel.analyzed", "Thread analyzed."));
      load();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setAnalyzing(false);
    }
  };

  const resolve = async (id) => {
    setResolving(id);
    try {
      await api.post(`${API}/escalations/${encodeURIComponent(id)}/resolve`);
      toast.success(t("conversationIntel.resolved", "Escalation resolved."));
      load();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setResolving(null);
    }
  };

  const trend = overview?.sentiment_trend || [];
  const maxBucket = Math.max(1, ...trend.map((b) => (b.positive || 0) + (b.neutral || 0) + (b.negative || 0)));
  const dist = overview?.distribution || { positive: 0, neutral: 0, negative: 0 };

  const kpis = useMemo(() => [
    { label: t("conversationIntel.positive_threads", "Positive threads"), value: dist.positive, icon: <Smile className="h-4 w-4 text-emerald-500" /> },
    { label: t("conversationIntel.neutral_threads", "Neutral threads"), value: dist.neutral, icon: <Meh className="h-4 w-4 text-amber-500" /> },
    { label: t("conversationIntel.negative_threads", "Negative threads"), value: dist.negative, icon: <Frown className="h-4 w-4 text-rose-500" /> },
    { label: t("conversationIntel.open_escalations", "Open escalations"), value: overview?.open_escalations ?? 0, icon: <Flame className="h-4 w-4 text-orange-500" /> },
  ], [t, dist, overview]);

  return (
    <div>
      <PageHeader
        title={t("conversationIntel.title", "Conversation Intelligence")}
        description={t("conversationIntel.description", "Every customer conversation watched for sentiment — nothing angry slips through.")}
        testId="ci-header"
        actions={
          <div className="flex gap-2">
            <Input
              placeholder={t("conversationIntel.thread_id_ph", "Thread ID to analyze…")}
              value={threadId}
              onChange={(e) => setThreadId(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") analyze(); }}
              className="h-8 w-48 text-sm"
              data-testid="ci-thread-input"
            />
            <Button size="sm" onClick={analyze} disabled={analyzing} data-testid="ci-analyze-btn">
              <Search className="h-4 w-4 mr-1" /> {analyzing ? t("conversationIntel.analyzing", "Analyzing…") : t("conversationIntel.analyze", "Analyze")}
            </Button>
          </div>
        }
      />

      {loading ? (
        <div className="text-muted-foreground">{t("conversationIntel.loading", "Loading conversation intelligence…")}</div>
      ) : (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5" data-testid="ci-kpis">
            {kpis.map((k) => (
              <div key={k.label} className="rounded-xl border bg-card p-4">
                <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">{k.icon}{k.label}</div>
                <div className="text-2xl font-bold">{k.value}</div>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-3 mb-5">
            <div className="rounded-xl border bg-card p-5 lg:col-span-2">
              <h3 className="font-semibold text-sm mb-3 flex items-center gap-2">
                <TrendingUp className="h-4 w-4 text-muted-foreground" />
                {t("conversationIntel.trend_title", "Sentiment trend (last 4 weeks)")}
              </h3>
              <div className="flex items-end gap-4 h-32">
                {trend.map((b) => {
                  const total = (b.positive || 0) + (b.neutral || 0) + (b.negative || 0);
                  return (
                    <div key={b.week_start} className="flex-1 flex flex-col items-center gap-1">
                      <div className="w-full flex flex-col-reverse rounded-md overflow-hidden bg-muted/40" style={{ height: 96 }}>
                        <div className="bg-emerald-500" style={{ height: `${((b.positive || 0) / maxBucket) * 96}px` }} title={`+${b.positive || 0}`} />
                        <div className="bg-amber-400" style={{ height: `${((b.neutral || 0) / maxBucket) * 96}px` }} title={`~${b.neutral || 0}`} />
                        <div className="bg-rose-500" style={{ height: `${((b.negative || 0) / maxBucket) * 96}px` }} title={`-${b.negative || 0}`} />
                      </div>
                      <span className="text-xs text-muted-foreground">{b.week_start.slice(5)}</span>
                    </div>
                  );
                })}
              </div>
              <div className="flex gap-4 mt-2 text-xs text-muted-foreground">
                <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-emerald-500" /> {t("conversationIntel.positive", "Positive")} ({dist.positive})</span>
                <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-amber-400" /> {t("conversationIntel.neutral", "Neutral")} ({dist.neutral})</span>
                <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-rose-500" /> {t("conversationIntel.negative", "Negative")} ({dist.negative})</span>
              </div>
            </div>

            <div className="space-y-3">
              <div className="rounded-xl border bg-card p-5">
                <div className="flex items-center gap-2 mb-3">
                  <ShieldAlert className="h-4 w-4 text-muted-foreground" />
                  <h3 className="font-semibold text-sm">{t("conversationIntel.queue_summary", "Escalations by severity")}</h3>
                </div>
                {["critical", "high", "medium", "low"].map((sev) => (
                  <div key={sev} className="flex items-center justify-between text-sm py-1">
                    <SeverityBadge severity={sev} />
                    <span className="font-semibold">{overview?.open_by_severity?.[sev] ?? 0}</span>
                  </div>
                ))}
              </div>
              {canManage && <RulesEditor rules={rules} onSaved={setRules} />}
            </div>
          </div>

          {/* Escalations queue */}
          <div className="flex flex-wrap items-center gap-2 mb-3">
            <h3 className="font-semibold text-sm mr-2 flex items-center gap-1.5">
              <Bell className="h-4 w-4 text-muted-foreground" /> {t("conversationIntel.escalations_title", "Escalation queue")}
            </h3>
            <select value={escStatus} onChange={(e) => setEscStatus(e.target.value)} className="rounded-md border bg-card px-3 py-2 text-sm">
              <option value="open">{t("conversationIntel.show_open", "Open")}</option>
              <option value="resolved">{t("conversationIntel.show_resolved", "Resolved")}</option>
              <option value="all">{t("conversationIntel.show_all", "All")}</option>
            </select>
          </div>

          {escalations.length === 0 ? (
            <div className="rounded-xl border bg-muted/40 p-8 text-center text-muted-foreground mb-5">
              <CheckCircle className="h-6 w-6 mx-auto mb-2 text-emerald-500" />
              {t("conversationIntel.empty_queue", "No escalations — every conversation is healthy.")}
            </div>
          ) : (
            <div className="space-y-3 mb-5">
              {escalations.map((e) => (
                <div key={e.id} className="rounded-xl border bg-card p-4" data-testid={`ci-esc-${e.id}`}>
                  <div className="flex flex-wrap items-center gap-2 mb-2">
                    <SeverityBadge severity={e.severity} />
                    <span className="font-medium text-sm">{e.rule_name}</span>
                    <span className="text-xs text-muted-foreground">
                      {e.customer_name || t("conversationIntel.unknown_customer", "Unknown customer")} · {e.channel || "—"} · {t("conversationIntel.thread", "Thread")} {e.thread_id}
                    </span>
                    {e.status === "resolved" ? (
                      <Badge className="bg-emerald-100 text-emerald-800">{t("conversationIntel.resolved_badge", "Resolved")}</Badge>
                    ) : (
                      <Badge className="bg-orange-100 text-orange-800">{t("conversationIntel.open_badge", "Open")}</Badge>
                    )}
                  </div>
                  <p className="text-sm mb-1">{e.reason}</p>
                  {e.message_excerpt && (
                    <p className="text-xs text-muted-foreground italic mb-2">“{e.message_excerpt}”</p>
                  )}
                  <div className="flex gap-2">
                    {canManage && e.status === "open" && (
                      <Button size="sm" variant="outline" onClick={() => resolve(e.id)} disabled={resolving === e.id} data-testid={`ci-resolve-${e.id}`}>
                        <CheckCircle className="h-3.5 w-3.5 mr-1" /> {t("conversationIntel.resolve", "Resolve")}
                      </Button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Per-thread timeline */}
          {timeline && (
            <div className="rounded-xl border bg-card p-5 mb-5" data-testid="ci-timeline">
              <h3 className="font-semibold text-sm mb-1 flex items-center gap-2">
                <Activity className="h-4 w-4 text-muted-foreground" />
                {t("conversationIntel.timeline_title", "Sentiment timeline")} — {timeline.thread_id}
              </h3>
              <p className="text-xs text-muted-foreground mb-4">
                {t("conversationIntel.overall", "Overall")}: <SentimentChip label={timeline.analysis?.overall_label} />{" "}
                {t("conversationIntel.score", "score")} {timeline.analysis?.avg_score ?? 0} ·{" "}
                {timeline.analysis?.customer_message_count ?? 0} {t("conversationIntel.customer_messages", "customer messages")}
              </p>
              <div className="space-y-2">
                {(timeline.analysis?.message_scores || []).map((m) => (
                  <div key={m.message_id || m.created_at} className="flex items-start gap-3 rounded-lg border p-3">
                    <span className={`mt-0.5 h-2.5 w-2.5 shrink-0 rounded-full ${
                      m.label === "positive" ? "bg-emerald-500" : m.label === "negative" ? "bg-rose-500" : "bg-amber-400"
                    }`} />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm">{m.excerpt}</p>
                      <div className="flex flex-wrap items-center gap-2 mt-1.5">
                        <SentimentChip label={m.label} />
                        {(m.churn || []).length > 0 && (
                          <Badge className="bg-red-100 text-red-800">
                            <Zap className="h-3 w-3 mr-1" /> {t("conversationIntel.churn", "Churn")}: {(m.churn || []).join(", ")}
                          </Badge>
                        )}
                        <span className="text-xs text-muted-foreground">
                          {t("conversationIntel.score", "score")} {m.score} · {m.created_at ? m.created_at.slice(0, 16).replace("T", " ") : ""}
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="rounded-xl border bg-muted/40 p-4 text-xs text-muted-foreground flex gap-2">
            <MessageSquare className="h-4 w-4 shrink-0 mt-0.5" />
            {t("conversationIntel.how_it_works", "Sentiment is scored keyword-by-keyword (template-based, no LLM): only customer messages count, business replies never do. Escalation rules fire on analyze — the thread is flagged urgent, the owner is paged, and a support ticket can open automatically.")}
          </div>
        </>
      )}
    </div>
  );
}
