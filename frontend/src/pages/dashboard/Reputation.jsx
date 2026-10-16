import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errMessage } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { Star, MessageSquare, Send, Settings, BellOff, Sparkles, Globe, ShieldCheck } from "lucide-react";

const TONE_STYLES = {
  grateful: { label: "Grateful (5★)", cls: "bg-amber-100 text-amber-800" },
  appreciative: { label: "Appreciative (4★)", cls: "bg-sky-100 text-sky-800" },
  empathetic_action: { label: "Empathetic + action (≤3★)", cls: "bg-rose-100 text-rose-800" },
};

function Stars({ n }) {
  return (
    <span className="flex items-center gap-0.5" aria-label={`${n} stars`}>
      {[1, 2, 3, 4, 5].map((i) => (
        <Star key={i} className={`h-3.5 w-3.5 ${i <= n ? "fill-amber-400 text-amber-400" : "text-muted-foreground"}`} />
      ))}
    </span>
  );
}

function RequestModal({ onClose, onSent }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({ job_id: "", customer_name: "", customer_phone: "", customer_email: "", channel: "sms", signal_score: "" });
  const [sending, setSending] = useState(false);
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  const send = async () => {
    if (!form.job_id.trim()) return toast.error(t("reput.job_required", "Job ID is required"));
    if (!form.customer_phone.trim() && !form.customer_email.trim())
      return toast.error(t("reput.contact_required", "Phone or email is required"));
    setSending(true);
    try {
      const payload = {
        job_id: form.job_id.trim(),
        customer_name: form.customer_name.trim(),
        customer_phone: form.customer_phone.trim(),
        customer_email: form.customer_email.trim(),
        channel: form.channel,
        signal_score: form.signal_score ? Number(form.signal_score) : null,
      };
      const r = await api.post("/reputation/tenants/requests", payload);
      if (r.data.duplicate) toast.info(t("reput.already_sent", "A request was already sent for this job."));
      else toast.success(t("reput.request_sent", "Review request sent."));
      onSent(r.data);
      onClose();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-xl bg-card p-6 shadow-xl" data-testid="reput-request-modal">
        <h3 className="text-lg font-semibold mb-1">{t("reput.request_title", "Request a review")}</h3>
        <p className="text-sm text-muted-foreground mb-4">
          {t("reput.request_hint", "Sent at the perfect moment — right after a 5-star-signal job. No incentives, ever.")}
        </p>
        <div className="space-y-3">
          <Input placeholder={t("reput.job_id", "Job ID *")} value={form.job_id} onChange={(e) => set({ job_id: e.target.value })} />
          <Input placeholder={t("reput.customer_name", "Customer name")} value={form.customer_name} onChange={(e) => set({ customer_name: e.target.value })} />
          <Input placeholder={t("reput.customer_phone", "Customer phone")} value={form.customer_phone} onChange={(e) => set({ customer_phone: e.target.value })} />
          <Input placeholder={t("reput.customer_email", "Customer email")} value={form.customer_email} onChange={(e) => set({ customer_email: e.target.value })} />
          <div className="flex gap-2">
            <select value={form.channel} onChange={(e) => set({ channel: e.target.value })} className="rounded-md border bg-card px-3 py-2 text-sm">
              <option value="sms">SMS</option>
              <option value="email">Email</option>
            </select>
            <select value={form.signal_score} onChange={(e) => set({ signal_score: e.target.value })} className="rounded-md border bg-card px-3 py-2 text-sm flex-1">
              <option value="">{t("reput.signal_any", "Signal: none")}</option>
              <option value="5">{t("reput.signal_5star", "Signal: 5-star job (send now)")}</option>
              <option value="4">{t("reput.signal_good", "Signal: good (send now)")}</option>
              <option value="3">{t("reput.signal_weak", "Signal: weak (stagger +24h)")}</option>
            </select>
          </div>
          <p className="text-xs text-muted-foreground flex gap-1.5">
            <ShieldCheck className="h-3.5 w-3.5 shrink-0" />
            {t("reput.policy_note", "Never offer incentives for reviews. Opted-out customers are skipped automatically.")}
          </p>
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <Button variant="outline" onClick={onClose}>{t("reput.cancel", "Cancel")}</Button>
          <Button onClick={send} disabled={sending} data-testid="reput-request-send">
            <Send className="h-4 w-4 mr-1" /> {sending ? t("reput.sending", "Sending…") : t("reput.send", "Send")}
          </Button>
        </div>
      </div>
    </div>
  );
}

function PolicyCard({ policy, onSave }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({ auto_post_5star: policy?.auto_post_5star ?? true, require_approval_low: policy?.require_approval_low ?? true });
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (policy) setForm({ auto_post_5star: policy.auto_post_5star, require_approval_low: policy.require_approval_low });
  }, [policy]);
  const save = async () => {
    setSaving(true);
    try {
      const r = await api.post("/reputation/tenants/policy", form);
      onSave(r.data);
      toast.success(t("reput.policy_saved", "Policy saved."));
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="rounded-xl border bg-card p-5">
      <div className="flex items-center gap-2 mb-3">
        <Settings className="h-4 w-4 text-muted-foreground" />
        <h3 className="font-semibold text-sm">{t("reput.policy_title", "Auto-post policy")}</h3>
      </div>
      <label className="flex items-start gap-2 text-sm mb-3 cursor-pointer">
        <input type="checkbox" checked={form.auto_post_5star} onChange={(e) => setForm((f) => ({ ...f, auto_post_5star: e.target.checked }))} className="mt-1 rounded" />
        <span>
          {t("reput.auto_post_5star", "Auto-post 5★ responses")}
          <span className="block text-xs text-muted-foreground">{t("reput.auto_post_hint", "Grateful drafts for 5-star reviews post immediately; everything else needs one tap.")}</span>
        </span>
      </label>
      <label className="flex items-start gap-2 text-sm mb-4 cursor-pointer">
        <input type="checkbox" checked={form.require_approval_low} onChange={(e) => setForm((f) => ({ ...f, require_approval_low: e.target.checked }))} className="mt-1 rounded" />
        <span>
          {t("reput.require_approval_low", "Require approval for ≤3★")}
          <span className="block text-xs text-muted-foreground">{t("reput.require_approval_hint", "Empathetic responses to unhappy customers never go out without owner review.")}</span>
        </span>
      </label>
      <Button size="sm" onClick={save} disabled={saving} data-testid="reput-policy-save">
        {t("reput.save", "Save policy")}
      </Button>
    </div>
  );
}

export default function Reputation() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [overview, setOverview] = useState(null);
  const [reviews, setReviews] = useState([]);
  const [loading, setLoading] = useState(true);
  const [needsResponseOnly, setNeedsResponseOnly] = useState(false);
  const [ratingFilter, setRatingFilter] = useState("");
  const [showRequest, setShowRequest] = useState(false);
  const [busyDraft, setBusyDraft] = useState(null);
  const canManage = user && ["owner", "admin"].includes(user.role);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [ov, rv] = await Promise.all([
        api.get("/reputation/tenants/overview"),
        api.get("/reputation/tenants/reviews"),
      ]);
      setOverview(ov.data);
      setReviews(rv.data.reviews || []);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = useMemo(() => reviews.filter((r) => {
    if (needsResponseOnly && ["responded", "posted"].includes(r.response_status)) return false;
    if (ratingFilter && String(r.rating) !== ratingFilter) return false;
    return true;
  }), [reviews, needsResponseOnly, ratingFilter]);

  const draft = async (id) => {
    setBusyDraft(id);
    try {
      const r = await api.post(`/api/reputation/tenants/reviews/${encodeURIComponent(id)}/draft`);
      toast.success(r.data.auto_posted ? t("reput.drafted_posted", "Draft posted (auto-post policy).") : t("reput.drafted", "Draft ready for your review."));
      load();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusyDraft(null);
    }
  };

  const approve = async (id) => {
    setBusyDraft(id);
    try {
      await api.post(`/api/reputation/tenants/reviews/${encodeURIComponent(id)}/approve`);
      toast.success(t("reput.approved", "Response approved and posted."));
      load();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusyDraft(null);
    }
  };

  const maxBucket = Math.max(1, ...(overview?.sentiment_trend || []).map((b) => b.positive + b.neutral + b.negative));

  return (
    <div>
      <PageHeader
        title={t("reput.title", "Autonomous Reputation")}
        description={t("reput.description", "Reviews requested at the perfect moment, responses drafted for one-tap approval, sentiment tracked.")}
        testId="reput-header"
        actions={
          canManage && (
            <Button size="sm" onClick={() => setShowRequest(true)} data-testid="reput-request-open">
              <Send className="h-4 w-4 mr-1" /> {t("reput.request_review", "Request review")}
            </Button>
          )
        }
      />

      {loading ? (
        <div className="text-muted-foreground">{t("reput.loading", "Loading reputation…")}</div>
      ) : (
        <>
          {/* KPI header */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5" data-testid="reput-kpis">
            {[
              { label: t("reput.avg_rating", "Avg rating"), value: overview?.avg_rating?.toFixed(1) ?? "—", icon: <Star className="h-4 w-4 fill-amber-400 text-amber-400" /> },
              { label: t("reput.review_count", "Reviews"), value: overview?.review_count ?? 0, icon: <MessageSquare className="h-4 w-4 text-sky-500" /> },
              { label: t("reput.response_rate", "Response rate"), value: `${Math.round((overview?.response_rate ?? 0) * 100)}%`, icon: <Sparkles className="h-4 w-4 text-violet-500" /> },
              { label: t("reput.requests_sent", "Requests sent"), value: overview?.review_requests_sent ?? 0, icon: <Send className="h-4 w-4 text-emerald-500" /> },
            ].map((k) => (
              <div key={k.label} className="rounded-xl border bg-card p-4">
                <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">{k.icon}{k.label}</div>
                <div className="text-2xl font-bold">{k.value}</div>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-3 mb-5">
            {/* Sentiment trend */}
            <div className="rounded-xl border bg-card p-5 lg:col-span-2">
              <h3 className="font-semibold text-sm mb-3">{t("reput.sentiment_trend", "Sentiment trend (last 4 weeks)")}</h3>
              <div className="flex items-end gap-4 h-32">
                {(overview?.sentiment_trend || []).map((b) => {
                  const total = b.positive + b.neutral + b.negative;
                  return (
                    <div key={b.week_start} className="flex-1 flex flex-col items-center gap-1">
                      <div className="w-full flex flex-col-reverse rounded-md overflow-hidden bg-muted/40" style={{ height: 96 }}>
                        <div className="bg-emerald-500" style={{ height: `${(b.positive / maxBucket) * 96}px` }} title={`+${b.positive}`} />
                        <div className="bg-amber-400" style={{ height: `${(b.neutral / maxBucket) * 96}px` }} title={`~${b.neutral}`} />
                        <div className="bg-rose-500" style={{ height: `${(b.negative / maxBucket) * 96}px` }} title={`-${b.negative}`} />
                      </div>
                      <span className="text-xs text-muted-foreground">{b.week_start.slice(5)}</span>
                    </div>
                  );
                })}
              </div>
              <div className="flex gap-4 mt-2 text-xs text-muted-foreground">
                <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-emerald-500" /> {t("reput.positive", "Positive")} ({overview?.sentiment?.positive ?? 0})</span>
                <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-amber-400" /> {t("reput.neutral", "Neutral")} ({overview?.sentiment?.neutral ?? 0})</span>
                <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-rose-500" /> {t("reput.negative", "Negative")} ({overview?.sentiment?.negative ?? 0})</span>
              </div>
            </div>
            {/* Monitoring stubs + policy */}
            <div className="space-y-3">
              <div className="rounded-xl border bg-card p-5">
                <div className="flex items-center gap-2 mb-3">
                  <Globe className="h-4 w-4 text-muted-foreground" />
                  <h3 className="font-semibold text-sm">{t("reput.monitoring", "Review-site monitoring")}</h3>
                </div>
                <div className="space-y-2">
                  {(overview?.monitoring || []).map((m) => (
                    <div key={m.site} className="flex items-center justify-between text-sm">
                      <span className="capitalize">{m.site}</span>
                      <Badge variant="secondary">{m.connected ? t("reput.connected", "Connected") : t("reput.pending_conn", "Pending connection")}</Badge>
                    </div>
                  ))}
                </div>
              </div>
              {canManage && <PolicyCard policy={overview?.policy} onSave={(p) => setOverview((o) => ({ ...o, policy: p }))} />}
            </div>
          </div>

          {/* Review inbox */}
          <div className="flex flex-wrap items-center gap-2 mb-3">
            <h3 className="font-semibold text-sm mr-2">{t("reput.inbox", "Review inbox")}</h3>
            <label className="flex items-center gap-1.5 text-sm text-muted-foreground cursor-pointer">
              <input type="checkbox" checked={needsResponseOnly} onChange={(e) => setNeedsResponseOnly(e.target.checked)} className="rounded" />
              {t("reput.needs_response", "Needs response")}
            </label>
            <select value={ratingFilter} onChange={(e) => setRatingFilter(e.target.value)} className="rounded-md border bg-card px-3 py-2 text-sm">
              <option value="">{t("reput.all_ratings", "All ratings")}</option>
              {[5, 4, 3, 2, 1].map((r) => <option key={r} value={r}>{r} ★</option>)}
            </select>
          </div>

          {filtered.length === 0 ? (
            <div className="rounded-xl border bg-muted/40 p-8 text-center text-muted-foreground">
              <BellOff className="h-6 w-6 mx-auto mb-2" />
              {t("reput.empty", "No reviews yet. Trigger a request to start building your reputation.")}
            </div>
          ) : (
            <div className="space-y-3">
              {filtered.map((r) => {
                const tone = TONE_STYLES[r.rating >= 5 ? "grateful" : r.rating === 4 ? "appreciative" : "empathetic_action"];
                const needs = !["responded", "posted"].includes(r.response_status);
                return (
                  <div key={r.id} className="rounded-xl border bg-card p-4" data-testid={`reput-review-${r.id}`}>
                    <div className="flex flex-wrap items-center gap-2 mb-2">
                      <Stars n={r.rating} />
                      <span className="font-medium text-sm">{r.author_name || t("reput.anonymous", "Anonymous")}</span>
                      <Badge variant="outline" className="capitalize">{r.source}</Badge>
                      <Badge className={tone.cls}>{tone.label}</Badge>
                      {needs ? (
                        <Badge className="bg-orange-100 text-orange-800">{t("reput.needs_response", "Needs response")}</Badge>
                      ) : (
                        <Badge className="bg-emerald-100 text-emerald-800">{r.response_status}</Badge>
                      )}
                    </div>
                    <p className="text-sm mb-3">{r.text || <span className="text-muted-foreground">{t("reput.no_text", "No written review.")}</span>}</p>
                    {r.draft_text && (
                      <div className="rounded-lg bg-muted/50 p-3 text-sm mb-3">
                        <div className="text-xs text-muted-foreground mb-1">{t("reput.ai_draft", "AI draft (template-based, not yet sent)")}</div>
                        {r.draft_text}
                      </div>
                    )}
                    <div className="flex gap-2">
                      {canManage && needs && (
                        <Button size="sm" variant="outline" onClick={() => draft(r.id)} disabled={busyDraft === r.id} data-testid={`reput-draft-${r.id}`}>
                          <Sparkles className="h-3.5 w-3.5 mr-1" /> {t("reput.draft_response", "Draft response")}
                        </Button>
                      )}
                      {canManage && r.response_status === "drafted" && (
                        <Button size="sm" onClick={() => approve(r.id)} disabled={busyDraft === r.id} data-testid={`reput-approve-${r.id}`}>
                          {t("reput.approve_post", "Approve & post")}
                        </Button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}

      {showRequest && <RequestModal onClose={() => setShowRequest(false)} onSent={load} />}
    </div>
  );
}
