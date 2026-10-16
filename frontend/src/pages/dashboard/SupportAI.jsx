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
  Bot, Send, ThumbsUp, ThumbsDown, BookOpen, AlertTriangle, HeartPulse,
  CheckCircle2, ShieldCheck, ShieldAlert, ShieldX, RefreshCw, ClipboardList,
} from "lucide-react";

const TIER_STYLES = {
  low: { cls: "bg-emerald-100 text-emerald-800", icon: <ShieldCheck className="h-4 w-4" /> },
  watch: { cls: "bg-amber-100 text-amber-800", icon: <ShieldAlert className="h-4 w-4" /> },
  high: { cls: "bg-rose-100 text-rose-800", icon: <ShieldX className="h-4 w-4" /> },
};

function ConfidenceBar({ value }) {
  const pct = Math.round((value || 0) * 100);
  const color = pct >= 70 ? "bg-emerald-500" : pct >= 45 ? "bg-amber-500" : "bg-rose-500";
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 w-24 rounded-full bg-muted overflow-hidden">
        <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-muted-foreground">{pct}%</span>
    </div>
  );
}

function HealthRing({ score }) {
  const R = 44;
  const C = 2 * Math.PI * R;
  const color = score >= 75 ? "stroke-emerald-500" : score >= 50 ? "stroke-amber-500" : "stroke-rose-500";
  return (
    <svg width="120" height="120" viewBox="0 0 120 120" aria-label={`Health score ${score} of 100`}>
      <circle cx="60" cy="60" r={R} strokeWidth="10" fill="none" className="stroke-muted" />
      <circle
        cx="60" cy="60" r={R} strokeWidth="10" fill="none" strokeLinecap="round"
        className={color} strokeDasharray={C} strokeDashoffset={C - (C * Math.min(100, Math.max(0, score))) / 100}
        transform="rotate(-90 60 60)"
      />
      <text x="60" y="58" textAnchor="middle" className="fill-foreground" fontSize="26" fontWeight="bold">{score}</text>
      <text x="60" y="76" textAnchor="middle" className="fill-muted-foreground" fontSize="10">/ 100</text>
    </svg>
  );
}

export default function SupportAI() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [lastAnswer, setLastAnswer] = useState(null);
  const [qaLog, setQaLog] = useState([]);
  const [health, setHealth] = useState(null);
  const [history, setHistory] = useState([]);
  const [churn, setChurn] = useState(null);
  const [loading, setLoading] = useState(true);
  const [playDone, setPlayDone] = useState({});
  const canManage = user && ["owner", "admin"].includes(user.role);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const calls = [api.get("/support-ai/health"), api.get("/support-ai/health/history")];
      if (canManage) calls.push(api.get("/support-ai/answers"), api.get("/support-ai/churn"));
      const res = await Promise.all(calls);
      setHealth(res[0].data);
      setHistory(res[1].data.history || []);
      if (canManage) {
        setQaLog(res[2].data.answers || []);
        setChurn(res[3].data);
      }
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  }, [canManage]);

  useEffect(() => { load(); }, [load]);

  const ask = async () => {
    if (!question.trim()) return toast.error(t("supportAI.question_required", "Type a question first."));
    setAsking(true);
    try {
      const r = await api.post("/support-ai/ask", { question: question.trim() });
      setLastAnswer(r.data);
      if (r.data.escalated) toast.info(t("supportAI.escalated_toast", "No confident answer found — opened a human support ticket."));
      else toast.success(t("supportAI.answered_toast", "Answer drafted from your knowledge base."));
      if (canManage) {
        const q = await api.get("/support-ai/answers");
        setQaLog(q.data.answers || []);
      }
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setAsking(false);
    }
  };

  const giveFeedback = async (id, helpful) => {
    try {
      await api.post(`/support-ai/answers/${encodeURIComponent(id)}/feedback`, { helpful });
      toast.success(t("supportAI.feedback_saved", "Thanks — your feedback was recorded."));
      setLastAnswer((a) => (a && a.id === id ? { ...a, feedback: { helpful } } : a));
      if (canManage) {
        const q = await api.get("/support-ai/answers");
        setQaLog(q.data.answers || []);
      }
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const factors = useMemo(() => {
    if (!health?.factors) return [];
    const labels = {
      usage_breadth: t("supportAI.f_usage", "Usage breadth"),
      login_recency: t("supportAI.f_login", "Login / activity recency"),
      ticket_sentiment: t("supportAI.f_tickets", "Support sentiment"),
      payment_health: t("supportAI.f_payment", "Payment health"),
    };
    const hints = {
      usage_breadth: (d) => `${(d.metrics_active || []).length}/5 ${t("supportAI.metrics_active", "metrics active (30d)")}`,
      login_recency: (d) => d.days_ago == null
        ? t("supportAI.no_login_signal", "No login signal yet")
        : `${d.days_ago}d ${t("supportAI.ago", "ago")}`,
      ticket_sentiment: (d) => `${d.open_tickets_30d ?? 0} ${t("supportAI.open_tickets", "open tickets (30d)")}`,
      payment_health: (d) => `${d.subscription_status ?? "trial"} · ${(d.plan_id ?? "trial").toUpperCase()}`,
    };
    return Object.entries(health.factors).map(([key, f]) => ({
      key, label: labels[key] || key, score: f.score, max: f.max,
      hint: hints[key] ? hints[key](f.detail || {}) : "",
    }));
  }, [health, t]);

  const tier = TIER_STYLES[churn?.tier] || TIER_STYLES.low;

  return (
    <div>
      <PageHeader
        title={t("supportAI.title", "AI Support Agent")}
        description={t("supportAI.description", "Answers from your knowledge base, shop health scoring, and churn early-warning.")}
        testId="sai-header"
        actions={
          <Button size="sm" variant="outline" onClick={load} data-testid="sai-refresh">
            <RefreshCw className="h-4 w-4 mr-1" /> {t("supportAI.refresh", "Refresh")}
          </Button>
        }
      />

      {loading ? (
        <div className="text-muted-foreground">{t("supportAI.loading", "Loading support AI…")}</div>
      ) : (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-3 mb-5">
            {/* Ask the agent */}
            <div className="rounded-xl border bg-card p-5 lg:col-span-2" data-testid="sai-ask-card">
              <div className="flex items-center gap-2 mb-3">
                <Bot className="h-4 w-4 text-violet-500" />
                <h3 className="font-semibold text-sm">{t("supportAI.ask_title", "Ask the support agent")}</h3>
              </div>
              <div className="flex gap-2 mb-3">
                <Input
                  placeholder={t("supportAI.ask_placeholder", "e.g. How do I assign jobs on the dispatch board?")}
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && ask()}
                  data-testid="sai-ask-input"
                />
                <Button onClick={ask} disabled={asking} data-testid="sai-ask-send">
                  <Send className="h-4 w-4 mr-1" /> {asking ? t("supportAI.asking", "Asking…") : t("supportAI.ask", "Ask")}
                </Button>
              </div>

              {lastAnswer && (
                <div className="rounded-lg border bg-muted/40 p-4" data-testid="sai-answer">
                  <div className="flex flex-wrap items-center gap-2 mb-2">
                    {lastAnswer.escalated ? (
                      <Badge className="bg-rose-100 text-rose-800">
                        <AlertTriangle className="h-3 w-3 mr-1" /> {t("supportAI.escalated", "Escalated to a human")}
                      </Badge>
                    ) : (
                      <Badge className="bg-emerald-100 text-emerald-800">
                        <CheckCircle2 className="h-3 w-3 mr-1" /> {t("supportAI.ai_answer", "AI answer")}
                      </Badge>
                    )}
                    {!lastAnswer.escalated && (
                      <span className="text-xs text-muted-foreground flex items-center gap-1">
                        {t("supportAI.confidence", "Confidence")} <ConfidenceBar value={lastAnswer.confidence} />
                      </span>
                    )}
                  </div>
                  <p className="text-sm whitespace-pre-line mb-3">{lastAnswer.answer}</p>
                  {lastAnswer.sources?.length > 0 && (
                    <div className="mb-3">
                      <div className="text-xs font-medium text-muted-foreground mb-1 flex items-center gap-1">
                        <BookOpen className="h-3.5 w-3.5" /> {t("supportAI.sources", "Sources")}
                      </div>
                      <ul className="space-y-1.5">
                        {lastAnswer.sources.map((s, i) => (
                          <li key={i} className="text-xs rounded-md bg-card border p-2">
                            <span className="font-medium">{s.doc_title}</span>
                            <span className="text-muted-foreground"> — {s.excerpt}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground">{t("supportAI.was_helpful", "Was this helpful?")}</span>
                    <Button size="sm" variant="outline" onClick={() => giveFeedback(lastAnswer.id, true)} data-testid="sai-fb-up">
                      <ThumbsUp className="h-3.5 w-3.5" />
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => giveFeedback(lastAnswer.id, false)} data-testid="sai-fb-down">
                      <ThumbsDown className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
              )}
            </div>

            {/* Health score */}
            <div className="rounded-xl border bg-card p-5" data-testid="sai-health-card">
              <div className="flex items-center gap-2 mb-3">
                <HeartPulse className="h-4 w-4 text-emerald-500" />
                <h3 className="font-semibold text-sm">{t("supportAI.health_title", "Shop health")}</h3>
              </div>
              <div className="flex justify-center mb-3"><HealthRing score={health?.score ?? 0} /></div>
              <div className="space-y-2.5">
                {factors.map((f) => (
                  <div key={f.key}>
                    <div className="flex justify-between text-xs mb-1">
                      <span className="text-muted-foreground">{f.label}</span>
                      <span className="font-medium">{f.score}/{f.max}</span>
                    </div>
                    <div className="h-1.5 rounded-full bg-muted overflow-hidden">
                      <div className="h-full bg-sky-500" style={{ width: `${(f.score / Math.max(1, f.max)) * 100}%` }} />
                    </div>
                    <div className="text-[11px] text-muted-foreground mt-0.5">{f.hint}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
            {/* Churn risk */}
            {canManage && (
              <div className="rounded-xl border bg-card p-5" data-testid="sai-churn-card">
                <div className="flex items-center gap-2 mb-3">
                  <ClipboardList className="h-4 w-4 text-muted-foreground" />
                  <h3 className="font-semibold text-sm">{t("supportAI.churn_title", "Churn risk")}</h3>
                </div>
                <div className="flex items-center gap-2 mb-3">
                  <Badge className={tier.cls}>{tier.icon}<span className="ml-1 capitalize">{churn?.tier ?? "low"}</span></Badge>
                </div>
                {churn?.reasons?.length > 0 ? (
                  <ul className="text-sm space-y-1.5 mb-4">
                    {churn.reasons.map((r, i) => (
                      <li key={i} className="flex gap-2"><AlertTriangle className="h-3.5 w-3.5 text-amber-500 shrink-0 mt-0.5" />{r}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-sm text-muted-foreground mb-4">{t("supportAI.churn_none", "No churn signals — this shop looks healthy.")}</p>
                )}
                <div className="text-xs font-medium text-muted-foreground mb-2">{t("supportAI.playbook", "Save playbook")}</div>
                <ul className="space-y-1.5">
                  {(churn?.playbook || []).map((step, i) => (
                    <li key={i}>
                      <label className="flex items-start gap-2 text-sm cursor-pointer">
                        <input
                          type="checkbox" className="mt-1 rounded" checked={!!playDone[i]}
                          onChange={(e) => setPlayDone((p) => ({ ...p, [i]: e.target.checked }))}
                          data-testid={`sai-playbook-${i}`}
                        />
                        <span className={playDone[i] ? "line-through text-muted-foreground" : ""}>{step}</span>
                      </label>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* QA log */}
            {canManage && (
              <div className="rounded-xl border bg-card p-5 lg:col-span-2" data-testid="sai-qa-card">
                <div className="flex items-center gap-2 mb-3">
                  <Bot className="h-4 w-4 text-violet-500" />
                  <h3 className="font-semibold text-sm">{t("supportAI.qa_title", "QA log — recent AI answers")}</h3>
                </div>
                {qaLog.length === 0 ? (
                  <p className="text-sm text-muted-foreground">{t("supportAI.qa_empty", "No answers yet. Ask a question above to start the QA log.")}</p>
                ) : (
                  <div className="space-y-2 max-h-96 overflow-y-auto">
                    {qaLog.map((a) => (
                      <div key={a.id} className="rounded-lg border p-3 text-sm">
                        <div className="flex flex-wrap items-center gap-2 mb-1">
                          {a.escalated
                            ? <Badge className="bg-rose-100 text-rose-800">{t("supportAI.escalated", "Escalated to a human")}</Badge>
                            : <Badge variant="secondary"><ConfidenceBar value={a.confidence} /></Badge>}
                          {a.feedback && (
                            a.feedback.helpful
                              ? <Badge className="bg-emerald-100 text-emerald-800"><ThumbsUp className="h-3 w-3 mr-1" /> {t("supportAI.helpful", "Helpful")}</Badge>
                              : <Badge className="bg-rose-100 text-rose-800"><ThumbsDown className="h-3 w-3 mr-1" /> {t("supportAI.not_helpful", "Not helpful")}</Badge>
                          )}
                        </div>
                        <div className="font-medium">{a.question}</div>
                        <div className="text-muted-foreground line-clamp-2">{a.answer}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
