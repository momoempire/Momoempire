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
  LifeBuoy, Plus, Clock, AlertTriangle, ShieldAlert, CheckCircle2,
  Send, Lock, Eye, RotateCcw, User, BellOff,
} from "lucide-react";

const T = "supportTickets";

const STATUS_STYLES = {
  open: { cls: "bg-sky-100 text-sky-800" },
  in_progress: { cls: "bg-violet-100 text-violet-800" },
  waiting_on_customer: { cls: "bg-amber-100 text-amber-800" },
  resolved: { cls: "bg-emerald-100 text-emerald-800" },
  closed: { cls: "bg-muted text-muted-foreground" },
};

const PRIORITY_STYLES = {
  low: { cls: "bg-slate-100 text-slate-700" },
  medium: { cls: "bg-blue-100 text-blue-800" },
  high: { cls: "bg-orange-100 text-orange-800" },
  urgent: { cls: "bg-rose-100 text-rose-800" },
};

const SLA_STYLES = {
  on_track: { cls: "bg-emerald-100 text-emerald-800" },
  at_risk: { cls: "bg-amber-100 text-amber-800" },
  breached: { cls: "bg-rose-100 text-rose-800" },
  met: { cls: "bg-emerald-100 text-emerald-800" },
};

function SlaBadge({ sla, t }) {
  if (!sla) return null;
  const overall = sla.overall;
  const style = SLA_STYLES[overall] || SLA_STYLES.on_track;
  const label = {
    on_track: t(`${T}.sla_on_track`, "On track"),
    at_risk: t(`${T}.sla_at_risk`, "At risk"),
    breached: t(`${T}.sla_breached`, "Breached"),
  }[overall];
  return (
    <Badge className={style.cls} title={t(`${T}.sla_hint`, "First response + resolution timers. The clock pauses while waiting on you.")}>
      {overall === "breached" ? <ShieldAlert className="h-3 w-3 mr-1" /> : overall === "at_risk" ? <AlertTriangle className="h-3 w-3 mr-1" /> : <Clock className="h-3 w-3 mr-1" />}
      {label}
    </Badge>
  );
}

function NewTicketModal({ onClose, onCreated }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({ subject: "", description: "", priority: "medium", category: "", contact_name: "", contact_email: "", contact_phone: "" });
  const [saving, setSaving] = useState(false);
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  const submit = async () => {
    if (!form.subject.trim()) return toast.error(t(`${T}.subject_required`, "Subject is required"));
    setSaving(true);
    try {
      const r = await api.post("/support-tickets/", {
        subject: form.subject.trim(),
        description: form.description.trim(),
        priority: form.priority,
        category: form.category.trim(),
        contact_name: form.contact_name.trim(),
        contact_email: form.contact_email.trim(),
        contact_phone: form.contact_phone.trim(),
      });
      toast.success(t(`${T}.ticket_created`, "Ticket opened. The platform team will respond per SLA."));
      onCreated(r.data);
      onClose();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-xl bg-card p-6 shadow-xl" data-testid="supptkt-new-modal">
        <h3 className="text-lg font-semibold mb-1">{t(`${T}.new_ticket`, "Open a support ticket")}</h3>
        <p className="text-sm text-muted-foreground mb-4">
          {t(`${T}.new_hint`, "The platform team triages every ticket against per-priority SLA targets.")}
        </p>
        <div className="space-y-3">
          <Input placeholder={t(`${T}.subject_ph`, "Subject *")} value={form.subject} onChange={(e) => set({ subject: e.target.value })} />
          <textarea
            className="w-full rounded-md border bg-card px-3 py-2 text-sm min-h-[96px]"
            placeholder={t(`${T}.description_ph`, "Describe the issue…")}
            value={form.description}
            onChange={(e) => set({ description: e.target.value })}
          />
          <div className="flex gap-2">
            <select value={form.priority} onChange={(e) => set({ priority: e.target.value })} className="rounded-md border bg-card px-3 py-2 text-sm">
              {["low", "medium", "high", "urgent"].map((p) => (
                <option key={p} value={p}>{t(`${T}.prio_${p}`, p)}</option>
              ))}
            </select>
            <Input placeholder={t(`${T}.category_ph`, "Category (billing, bug, onboarding…)")} value={form.category} onChange={(e) => set({ category: e.target.value })} />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            <Input placeholder={t(`${T}.contact_name`, "Contact name")} value={form.contact_name} onChange={(e) => set({ contact_name: e.target.value })} />
            <Input placeholder={t(`${T}.contact_email`, "Contact email")} value={form.contact_email} onChange={(e) => set({ contact_email: e.target.value })} />
            <Input placeholder={t(`${T}.contact_phone`, "Contact phone")} value={form.contact_phone} onChange={(e) => set({ contact_phone: e.target.value })} />
          </div>
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <Button variant="outline" onClick={onClose}>{t(`${T}.cancel`, "Cancel")}</Button>
          <Button onClick={submit} disabled={saving} data-testid="supptkt-new-submit">
            <Plus className="h-4 w-4 mr-1" /> {saving ? t(`${T}.opening`, "Opening…") : t(`${T}.open_ticket`, "Open ticket")}
          </Button>
        </div>
      </div>
    </div>
  );
}

function ReplyComposer({ ticketId, canManage, onPosted }) {
  const { t } = useTranslation();
  const [body, setBody] = useState("");
  const [kind, setKind] = useState("public");
  const [sending, setSending] = useState(false);

  const send = async () => {
    if (!body.trim()) return;
    setSending(true);
    try {
      await api.post(`/support-tickets/${encodeURIComponent(ticketId)}/replies`, {
        body: body.trim(),
        kind: canManage ? kind : "public",
        author_role: canManage && kind === "internal" ? "staff" : "customer",
      });
      setBody("");
      toast.success(t(`${T}.reply_posted`, "Reply posted."));
      onPosted();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="rounded-xl border bg-card p-3">
      <textarea
        className="w-full rounded-md border bg-card px-3 py-2 text-sm min-h-[72px]"
        placeholder={t(`${T}.reply_ph`, "Write a reply…")}
        value={body}
        onChange={(e) => setBody(e.target.value)}
      />
      <div className="flex items-center justify-between mt-2">
        {canManage ? (
          <div className="flex gap-2 text-sm">
            <label className="flex items-center gap-1.5 cursor-pointer">
              <input type="radio" checked={kind === "public"} onChange={() => setKind("public")} />
              <Eye className="h-3.5 w-3.5" /> {t(`${T}.kind_public`, "Public")}
            </label>
            <label className="flex items-center gap-1.5 cursor-pointer text-muted-foreground">
              <input type="radio" checked={kind === "internal"} onChange={() => setKind("internal")} />
              <Lock className="h-3.5 w-3.5" /> {t(`${T}.kind_internal`, "Internal note")}
            </label>
          </div>
        ) : <span />}
        <Button size="sm" onClick={send} disabled={sending || !body.trim()} data-testid="supptkt-reply-send">
          <Send className="h-3.5 w-3.5 mr-1" /> {t(`${T}.send`, "Send")}
        </Button>
      </div>
    </div>
  );
}

function TicketDrawer({ ticketId, onClose, canManage, reloadQueue }) {
  const { t } = useTranslation();
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [threadFilter, setThreadFilter] = useState("all"); // all | public | internal
  const [busy, setBusy] = useState(false);
  const [patching, setPatching] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get(`/support-tickets/${encodeURIComponent(ticketId)}`);
      setDetail(r.data);
    } catch (e) {
      toast.error(errMessage(e));
      onClose();
    } finally {
      setLoading(false);
    }
  }, [ticketId, onClose]);

  useEffect(() => { load(); }, [load]);

  const visibleReplies = useMemo(() => {
    const replies = detail?.replies || [];
    if (threadFilter === "public") return replies.filter((r) => r.kind === "public");
    if (threadFilter === "internal") return replies.filter((r) => r.kind === "internal");
    return replies;
  }, [detail, threadFilter]);

  const act = async (fn, okMsg) => {
    setBusy(true);
    try {
      await fn();
      toast.success(okMsg);
      await load();
      reloadQueue();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const resolve = () =>
    act(() => api.post(`/support-tickets/${encodeURIComponent(ticketId)}/resolve`, {}),
      t(`${T}.resolved_toast`, "Ticket resolved."));
  const reopen = () =>
    act(() => api.post(`/support-tickets/${encodeURIComponent(ticketId)}/reopen`, {}),
      t(`${T}.reopened_toast`, "Ticket re-opened."));

  const patchField = async (field, value) => {
    setPatching(field);
    try {
      await api.patch(`/support-tickets/${encodeURIComponent(ticketId)}`, { [field]: value });
      await load();
      reloadQueue();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setPatching(null);
    }
  };

  const ticket = detail?.ticket;
  const sla = detail?.sla;
  const finalState = ticket && ["resolved", "closed"].includes(ticket.status);

  return (
    <div className="fixed inset-0 z-50">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <aside className="absolute right-0 top-0 bottom-0 w-full max-w-xl bg-card shadow-xl flex flex-col" data-testid="supptkt-drawer">
        <div className="p-5 border-b">
          <div className="flex items-start justify-between gap-2">
            <div>
              <div className="flex flex-wrap items-center gap-2 mb-1">
                {ticket && <SlaBadge sla={sla} t={t} />}
                {ticket && (
                  <>
                    <Badge className={STATUS_STYLES[ticket.status]?.cls}>{t(`${T}.status_${ticket.status}`, ticket.status)}</Badge>
                    <Badge className={PRIORITY_STYLES[ticket.priority]?.cls}>{t(`${T}.prio_${ticket.priority}`, ticket.priority)}</Badge>
                  </>
                )}
              </div>
              <h3 className="text-lg font-semibold">{ticket?.subject || t(`${T}.loading`, "Loading…")}</h3>
              <p className="text-xs text-muted-foreground mt-1">
                {ticket?.id} · {t(`${T}.opened`, "opened")} {ticket?.created_at?.slice(0, 16).replace("T", " ")}
                {ticket?.assignee && <span> · <User className="h-3 w-3 inline" /> {ticket.assignee}</span>}
              </p>
            </div>
            <Button variant="outline" size="sm" onClick={onClose}>{t(`${T}.close`, "Close")}</Button>
          </div>
          {ticket?.description && <p className="text-sm mt-3 whitespace-pre-wrap">{ticket.description}</p>}

          {canManage && ticket && (
            <div className="flex flex-wrap gap-2 mt-4">
              <select
                value={ticket.status}
                onChange={(e) => patchField("status", e.target.value)}
                disabled={!!patching || busy || finalState}
                className="rounded-md border bg-card px-2 py-1.5 text-sm"
                title={finalState ? t(`${T}.final_hint`, "Use re-open for resolved/closed tickets") : ""}
              >
                {["open", "in_progress", "waiting_on_customer", "resolved", "closed"].map((s) => (
                  <option key={s} value={s}>{t(`${T}.status_${s}`, s)}</option>
                ))}
              </select>
              <select
                value={ticket.priority}
                onChange={(e) => patchField("priority", e.target.value)}
                disabled={!!patching || busy}
                className="rounded-md border bg-card px-2 py-1.5 text-sm"
              >
                {["low", "medium", "high", "urgent"].map((p) => (
                  <option key={p} value={p}>{t(`${T}.prio_${p}`, p)}</option>
                ))}
              </select>
              {!finalState ? (
                <Button size="sm" variant="outline" onClick={resolve} disabled={busy} data-testid="supptkt-resolve">
                  <CheckCircle2 className="h-3.5 w-3.5 mr-1" /> {t(`${T}.resolve`, "Resolve")}
                </Button>
              ) : (
                <Button size="sm" variant="outline" onClick={reopen} disabled={busy} data-testid="supptkt-reopen">
                  <RotateCcw className="h-3.5 w-3.5 mr-1" /> {t(`${T}.reopen`, "Re-open")}
                </Button>
              )}
            </div>
          )}
        </div>

        <div className="flex-1 overflow-y-auto p-5">
          {loading ? (
            <div className="text-muted-foreground text-sm">{t(`${T}.loading`, "Loading…")}</div>
          ) : (
            <>
              {sla && (
                <div className="grid grid-cols-2 gap-2 mb-4 text-sm">
                  <div className="rounded-lg border p-3">
                    <div className="text-xs text-muted-foreground mb-1">{t(`${T}.first_response`, "First response")}</div>
                    <Badge className={SLA_STYLES[sla.first_response.status]?.cls}>{t(`${T}.timer_${sla.first_response.status}`, sla.first_response.status)}</Badge>
                    <div className="text-xs text-muted-foreground mt-1">
                      {t(`${T}.target`, "target")} {Math.round(sla.first_response.target_minutes)} {t(`${T}.min`, "min")}
                    </div>
                  </div>
                  <div className="rounded-lg border p-3">
                    <div className="text-xs text-muted-foreground mb-1">{t(`${T}.resolution`, "Resolution")}</div>
                    <Badge className={SLA_STYLES[sla.resolution.status]?.cls}>{t(`${T}.timer_${sla.resolution.status}`, sla.resolution.status)}</Badge>
                    <div className="text-xs text-muted-foreground mt-1">
                      {t(`${T}.target`, "target")} {Math.round(sla.resolution.target_minutes)} {t(`${T}.min`, "min")}
                    </div>
                  </div>
                </div>
              )}

              <div className="flex items-center gap-2 mb-3">
                <h4 className="font-semibold text-sm">{t(`${T}.thread`, "Conversation")}</h4>
                <div className="flex gap-1 text-xs">
                  {[["all", t(`${T}.filter_all`, "All")], ["public", t(`${T}.filter_public`, "Public")], ["internal", t(`${T}.filter_internal`, "Internal")]].map(([v, label]) => (
                    <button
                      key={v}
                      onClick={() => setThreadFilter(v)}
                      className={`rounded-full px-2.5 py-1 ${threadFilter === v ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"}`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              {visibleReplies.length === 0 ? (
                <div className="rounded-xl border bg-muted/40 p-6 text-center text-muted-foreground text-sm mb-3">
                  <BellOff className="h-5 w-5 mx-auto mb-1" />
                  {t(`${T}.no_replies`, "No replies yet.")}
                </div>
              ) : (
                <div className="space-y-2 mb-4">
                  {visibleReplies.map((r) => (
                    <div key={r.id} className={`rounded-lg p-3 text-sm ${r.kind === "internal" ? "bg-amber-50 border border-amber-200" : "bg-muted/50"}`}>
                      <div className="flex items-center gap-2 text-xs text-muted-foreground mb-1">
                        {r.kind === "internal" ? <Lock className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
                        <span className="font-medium">{r.kind === "internal" ? t(`${T}.internal_note`, "Internal note") : t(`${T}.public_reply`, "Reply")}</span>
                        <span>· {r.author_role === "staff" ? t(`${T}.role_staff`, "Support team") : t(`${T}.role_customer`, "You")}</span>
                        <span>· {r.created_at?.slice(0, 16).replace("T", " ")}</span>
                      </div>
                      <p className="whitespace-pre-wrap">{r.body}</p>
                    </div>
                  ))}
                </div>
              )}

              {detail?.events?.length > 0 && (
                <details className="text-xs text-muted-foreground mb-4">
                  <summary className="cursor-pointer font-medium">{t(`${T}.audit_trail`, "Audit trail")} ({detail.events.length})</summary>
                  <ul className="mt-2 space-y-1">
                    {detail.events.map((e) => (
                      <li key={e.id}>
                        {e.at?.slice(0, 16).replace("T", " ")} — {t(`${T}.event_${e.kind}`, e.kind)}
                        {e.detail?.from && e.detail?.to ? `: ${e.detail.from} → ${e.detail.to}` : ""}
                      </li>
                    ))}
                  </ul>
                </details>
              )}

              <ReplyComposer ticketId={ticketId} canManage={canManage} onPosted={load} />
            </>
          )}
        </div>
      </aside>
    </div>
  );
}

export default function SupportTickets() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [overview, setOverview] = useState(null);
  const [tickets, setTickets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState("");
  const [priorityFilter, setPriorityFilter] = useState("");
  const [slaFilter, setSlaFilter] = useState("");
  const [showNew, setShowNew] = useState(false);
  const [openTicketId, setOpenTicketId] = useState(null);
  const canManage = user && ["owner", "admin"].includes(user.role);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [ov, tk] = await Promise.all([
        api.get("/support-tickets/sla/overview"),
        api.get("/support-tickets/"),
      ]);
      setOverview(ov.data);
      setTickets(tk.data.tickets || []);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = useMemo(() => tickets.filter((x) => {
    if (statusFilter && x.status !== statusFilter) return false;
    if (priorityFilter && x.priority !== priorityFilter) return false;
    if (slaFilter && x.sla?.overall !== slaFilter) return false;
    return true;
  }), [tickets, statusFilter, priorityFilter, slaFilter]);

  const counts = overview?.counts || {};

  return (
    <div>
      <PageHeader
        title={t(`${T}.title`, "Helpdesk")}
        description={t(`${T}.description`, "Support tickets for your shop — triaged by the platform team against per-priority SLA targets. The SLA clock pauses while a ticket waits on you.")}
        testId="supptkt-header"
        actions={
          <Button size="sm" onClick={() => setShowNew(true)} data-testid="supptkt-new-open">
            <Plus className="h-4 w-4 mr-1" /> {t(`${T}.new_ticket`, "Open a support ticket")}
          </Button>
        }
      />

      {loading ? (
        <div className="text-muted-foreground">{t(`${T}.loading`, "Loading tickets…")}</div>
      ) : (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5" data-testid="supptkt-kpis">
            {[
              { label: t(`${T}.kpi_open`, "Open tickets"), value: overview?.open_total ?? 0, icon: <LifeBuoy className="h-4 w-4 text-sky-500" /> },
              { label: t(`${T}.kpi_at_risk`, "At risk"), value: counts.at_risk ?? 0, icon: <AlertTriangle className="h-4 w-4 text-amber-500" /> },
              { label: t(`${T}.kpi_breached`, "Breached"), value: counts.breached ?? 0, icon: <ShieldAlert className="h-4 w-4 text-rose-500" /> },
              { label: t(`${T}.kpi_resolved_breached`, "Resolved past SLA"), value: overview?.resolved_breached ?? 0, icon: <Clock className="h-4 w-4 text-violet-500" /> },
            ].map((k) => (
              <div key={k.label} className="rounded-xl border bg-card p-4">
                <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">{k.icon}{k.label}</div>
                <div className="text-2xl font-bold">{k.value}</div>
              </div>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-2 mb-3">
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="rounded-md border bg-card px-3 py-2 text-sm">
              <option value="">{t(`${T}.all_statuses`, "All statuses")}</option>
              {["open", "in_progress", "waiting_on_customer", "resolved", "closed"].map((s) => (
                <option key={s} value={s}>{t(`${T}.status_${s}`, s)}</option>
              ))}
            </select>
            <select value={priorityFilter} onChange={(e) => setPriorityFilter(e.target.value)} className="rounded-md border bg-card px-3 py-2 text-sm">
              <option value="">{t(`${T}.all_priorities`, "All priorities")}</option>
              {["low", "medium", "high", "urgent"].map((p) => (
                <option key={p} value={p}>{t(`${T}.prio_${p}`, p)}</option>
              ))}
            </select>
            <select value={slaFilter} onChange={(e) => setSlaFilter(e.target.value)} className="rounded-md border bg-card px-3 py-2 text-sm">
              <option value="">{t(`${T}.all_sla`, "All SLA states")}</option>
              {["on_track", "at_risk", "breached"].map((s) => (
                <option key={s} value={s}>{t(`${T}.sla_${s}`, s)}</option>
              ))}
            </select>
          </div>

          {filtered.length === 0 ? (
            <div className="rounded-xl border bg-muted/40 p-8 text-center text-muted-foreground">
              <BellOff className="h-6 w-6 mx-auto mb-2" />
              {t(`${T}.empty`, "No tickets match. Open one and the platform team will pick it up.")}
            </div>
          ) : (
            <div className="space-y-2">
              {filtered.map((x) => (
                <button
                  key={x.id}
                  onClick={() => setOpenTicketId(x.id)}
                  className="w-full text-left rounded-xl border bg-card p-4 hover:border-primary/40 transition-colors"
                  data-testid={`supptkt-row-${x.id}`}
                >
                  <div className="flex flex-wrap items-center gap-2 mb-1">
                    <SlaBadge sla={x.sla} t={t} />
                    <Badge className={STATUS_STYLES[x.status]?.cls}>{t(`${T}.status_${x.status}`, x.status)}</Badge>
                    <Badge className={PRIORITY_STYLES[x.priority]?.cls}>{t(`${T}.prio_${x.priority}`, x.priority)}</Badge>
                    {x.assignee && <span className="text-xs text-muted-foreground flex items-center gap-1"><User className="h-3 w-3" />{x.assignee}</span>}
                  </div>
                  <div className="font-medium text-sm">{x.subject}</div>
                  <div className="text-xs text-muted-foreground mt-0.5">
                    {x.id} · {t(`${T}.opened`, "opened")} {x.created_at?.slice(0, 16).replace("T", " ")}
                  </div>
                </button>
              ))}
            </div>
          )}
        </>
      )}

      {showNew && <NewTicketModal onClose={() => setShowNew(false)} onCreated={load} />}
      {openTicketId && (
        <TicketDrawer
          ticketId={openTicketId}
          onClose={() => setOpenTicketId(null)}
          canManage={canManage}
          reloadQueue={load}
        />
      )}
    </div>
  );
}
