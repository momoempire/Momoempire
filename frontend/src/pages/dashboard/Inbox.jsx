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
  MessageSquare, Phone, Mail, Globe, Send, CheckCheck, RotateCcw,
  Sparkles, UserPlus, Inbox as InboxIcon,
} from "lucide-react";

const CHANNEL_META = {
  sms: { icon: MessageSquare, label: "SMS", cls: "bg-emerald-100 text-emerald-800" },
  voice: { icon: Phone, label: "Voice", cls: "bg-violet-100 text-violet-800" },
  email: { icon: Mail, label: "Email", cls: "bg-sky-100 text-sky-800" },
  chat: { icon: Globe, label: "Web chat", cls: "bg-amber-100 text-amber-800" },
};

const STATUS_META = {
  open: { label: "Open", cls: "bg-blue-100 text-blue-800" },
  pending: { label: "Pending", cls: "bg-amber-100 text-amber-800" },
  resolved: { label: "Resolved", cls: "bg-emerald-100 text-emerald-800" },
};

function ChannelBadge({ channel }) {
  const meta = CHANNEL_META[channel] || CHANNEL_META.chat;
  const Icon = meta.icon;
  return (
    <Badge className={meta.cls} variant="secondary">
      <Icon className="h-3 w-3 mr-1" /> {meta.label}
    </Badge>
  );
}

function StatusBadge({ status }) {
  const meta = STATUS_META[status] || STATUS_META.open;
  return <Badge className={meta.cls} variant="secondary">{meta.label}</Badge>;
}

export default function Inbox() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [threads, setThreads] = useState([]);
  const [loading, setLoading] = useState(true);
  const [channelFilter, setChannelFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [suggestions, setSuggestions] = useState([]);
  const [composer, setComposer] = useState("");
  const [sending, setSending] = useState(false);
  const [assignee, setAssignee] = useState("");
  const canManage = user && ["owner", "admin"].includes(user.role);

  const loadThreads = useCallback(async () => {
    try {
      const r = await api.get("/inbox/threads");
      setThreads(r.data.threads || []);
    } catch (e) {
      toast.error(errMessage(e));
    }
  }, []);

  useEffect(() => {
    (async () => {
      setLoading(true);
      await loadThreads();
      setLoading(false);
    })();
  }, [loadThreads]);

  const filtered = useMemo(() => threads.filter((th) => {
    if (channelFilter && th.channel !== channelFilter) return false;
    if (statusFilter && th.status !== statusFilter) return false;
    return true;
  }), [threads, channelFilter, statusFilter]);

  const openThread = async (id) => {
    setSelectedId(id);
    setDetail(null);
    setSuggestions([]);
    try {
      const [d, s] = await Promise.all([
        api.get(`/inbox/threads/${encodeURIComponent(id)}`),
        api.get(`/inbox/threads/${encodeURIComponent(id)}/suggestions`),
      ]);
      setDetail(d.data);
      setSuggestions(s.data.suggestions || []);
      setAssignee(d.data.thread?.assignee || "");
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const refreshDetail = async (id) => {
    const d = await api.get(`/inbox/threads/${encodeURIComponent(id)}`);
    setDetail(d.data);
    loadThreads();
  };

  const send = async () => {
    const body = composer.trim();
    if (!body || !selectedId) return;
    setSending(true);
    try {
      await api.post(`/inbox/threads/${encodeURIComponent(selectedId)}/messages`, { body });
      setComposer("");
      toast.success(t("inbox.sent", "Message sent."));
      await refreshDetail(selectedId);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setSending(false);
    }
  };

  const doAssign = async () => {
    if (!selectedId) return;
    try {
      const r = await api.post(`/inbox/threads/${encodeURIComponent(selectedId)}/assign`, {
        assignee: assignee.trim() || null,
      });
      toast.success(r.data.assignee
        ? t("inbox.assigned", "Thread assigned.")
        : t("inbox.unassigned", "Thread unassigned."));
      await refreshDetail(selectedId);
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const setStatus = async (action) => {
    if (!selectedId) return;
    try {
      await api.post(`/inbox/threads/${encodeURIComponent(selectedId)}/${action}`);
      toast.success(action === "resolve"
        ? t("inbox.resolved", "Thread resolved.")
        : t("inbox.reopened", "Thread reopened."));
      await refreshDetail(selectedId);
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const selected = detail?.thread || null;

  return (
    <div>
      <PageHeader
        title={t("inbox.title", "Omnichannel Inbox")}
        description={t("inbox.description", "Every customer conversation — SMS, voice calls, email, web chat — in one place.")}
        testId="inbox-header"
        actions={
          <div className="flex gap-2">
            <select value={channelFilter} onChange={(e) => setChannelFilter(e.target.value)} className="rounded-md border bg-card px-3 py-2 text-sm">
              <option value="">{t("inbox.all_channels", "All channels")}</option>
              {Object.entries(CHANNEL_META).map(([k, m]) => (
                <option key={k} value={k}>{m.label}</option>
              ))}
            </select>
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="rounded-md border bg-card px-3 py-2 text-sm">
              <option value="">{t("inbox.all_statuses", "All statuses")}</option>
              {Object.entries(STATUS_META).map(([k, m]) => (
                <option key={k} value={k}>{m.label}</option>
              ))}
            </select>
          </div>
        }
      />

      {loading ? (
        <div className="text-muted-foreground">{t("inbox.loading", "Loading inbox…")}</div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-3" data-testid="inbox-panes">
          {/* Thread list */}
          <div className="rounded-xl border bg-card overflow-hidden lg:col-span-1">
            <div className="px-4 py-3 border-b flex items-center gap-2">
              <InboxIcon className="h-4 w-4 text-muted-foreground" />
              <h3 className="font-semibold text-sm">{t("inbox.threads", "Threads")} ({filtered.length})</h3>
            </div>
            <div className="divide-y max-h-[70vh] overflow-y-auto">
              {filtered.length === 0 && (
                <div className="p-8 text-center text-muted-foreground text-sm">
                  {t("inbox.empty", "No threads yet. Ingested SMS and call transcripts will appear here.")}
                </div>
              )}
              {filtered.map((th) => (
                <button
                  key={th.id}
                  onClick={() => openThread(th.id)}
                  data-testid={`inbox-thread-${th.id}`}
                  className={`w-full text-left px-4 py-3 hover:bg-muted/40 transition-colors ${selectedId === th.id ? "bg-muted/60" : ""}`}
                >
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <span className="font-medium text-sm truncate">{th.customer_name || th.customer_contact || t("inbox.unknown", "Unknown")}</span>
                    <ChannelBadge channel={th.channel} />
                  </div>
                  <p className="text-xs text-muted-foreground truncate mb-1.5">
                    {th.last_message_preview || t("inbox.no_messages", "No messages yet.")}
                  </p>
                  <div className="flex items-center gap-1.5">
                    <StatusBadge status={th.status} />
                    {th.assignee && (
                      <Badge variant="outline" className="text-xs"><UserPlus className="h-3 w-3 mr-1" />{th.assignee}</Badge>
                    )}
                  </div>
                </button>
              ))}
            </div>
          </div>

          {/* Conversation view */}
          <div className="rounded-xl border bg-card lg:col-span-2 flex flex-col min-h-[60vh]">
            {!selected ? (
              <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground p-10">
                <MessageSquare className="h-8 w-8 mb-3" />
                {t("inbox.pick_thread", "Pick a thread to read the conversation.")}
              </div>
            ) : (
              <>
                <div className="px-5 py-4 border-b">
                  <div className="flex flex-wrap items-center gap-2 mb-2">
                    <h3 className="font-semibold">{selected.customer_name || selected.customer_contact}</h3>
                    <ChannelBadge channel={selected.channel} />
                    <StatusBadge status={selected.status} />
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    {canManage && (
                      <>
                        <Input
                          placeholder={t("inbox.assign_placeholder", "Assign to user or queue…")}
                          value={assignee}
                          onChange={(e) => setAssignee(e.target.value)}
                          className="w-48"
                          data-testid="inbox-assignee"
                        />
                        <Button size="sm" variant="outline" onClick={doAssign} data-testid="inbox-assign-btn">
                          <UserPlus className="h-3.5 w-3.5 mr-1" /> {t("inbox.assign", "Assign")}
                        </Button>
                      </>
                    )}
                    {selected.status === "resolved" ? (
                      <Button size="sm" variant="outline" onClick={() => setStatus("reopen")} data-testid="inbox-reopen">
                        <RotateCcw className="h-3.5 w-3.5 mr-1" /> {t("inbox.reopen", "Reopen")}
                      </Button>
                    ) : (
                      <Button size="sm" variant="outline" onClick={() => setStatus("resolve")} data-testid="inbox-resolve">
                        <CheckCheck className="h-3.5 w-3.5 mr-1" /> {t("inbox.resolve", "Resolve")}
                      </Button>
                    )}
                  </div>
                </div>

                <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3 max-h-[46vh]" data-testid="inbox-messages">
                  {(detail?.messages || []).map((m) => (
                    <div key={m.id} className={`flex ${m.direction === "out" ? "justify-end" : "justify-start"}`}>
                      <div className={`max-w-[75%] rounded-xl px-3.5 py-2.5 text-sm whitespace-pre-wrap ${
                        m.direction === "out" ? "bg-primary text-primary-foreground" : "bg-muted"
                      }`}>
                        {m.body}
                        <div className={`text-[11px] mt-1 ${m.direction === "out" ? "text-primary-foreground/70" : "text-muted-foreground"}`}>
                          {m.sender === "customer" ? t("inbox.customer", "Customer") : t("inbox.shop", "Shop")} · {new Date(m.created_at).toLocaleString()}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>

                {suggestions.length > 0 && (
                  <div className="px-5 pb-2">
                    <div className="flex items-center gap-1.5 text-xs text-muted-foreground mb-1.5">
                      <Sparkles className="h-3.5 w-3.5" /> {t("inbox.ai_suggestions", "AI suggestions (drafts — review before sending)")}
                    </div>
                    <div className="flex flex-wrap gap-2" data-testid="inbox-suggestions">
                      {suggestions.map((s) => (
                        <button
                          key={s.kind}
                          onClick={() => setComposer(s.draft)}
                          className="rounded-full border px-3 py-1.5 text-xs hover:bg-muted/60 transition-colors"
                          title={s.draft}
                        >
                          {s.label}
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                <div className="px-5 py-4 border-t">
                  <div className="flex gap-2">
                    <Input
                      placeholder={t("inbox.reply_placeholder", "Write a reply…")}
                      value={composer}
                      onChange={(e) => setComposer(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter") send(); }}
                      data-testid="inbox-composer"
                    />
                    <Button onClick={send} disabled={sending || !composer.trim()} data-testid="inbox-send">
                      <Send className="h-4 w-4 mr-1" /> {t("inbox.send", "Send")}
                    </Button>
                  </div>
                  <p className="text-[11px] text-muted-foreground mt-1.5">
                    {t("inbox.channel_note", `Sending via ${CHANNEL_META[selected.channel]?.label || selected.channel}. Live provider dispatch is stubbed until connection.`)}
                  </p>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
