import { useEffect, useRef, useState } from "react";
import { api, errMessage, API_BASE } from "@/lib/api";
import { fmtUSD } from "@/lib/money";
import { useTranslation } from "react-i18next";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import {
  Video, Plus, X, Loader2, Link2, Copy, Clock, CheckCircle2, ArrowLeft,
} from "lucide-react";

const BASE = "/video-estimates/tenants/requests";
const STATUS_FLOW = ["requested", "uploaded", "in-review", "estimated"];

function fmtTimestamp(sec) {
  const s = Math.max(0, Math.floor(Number(sec) || 0));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

function StatusBadge({ status }) {
  const tone =
    status === "estimated"
      ? "bg-emerald-100 text-emerald-900"
      : status === "in-review"
        ? "bg-amber-100 text-amber-900"
        : status === "uploaded"
          ? "bg-blue-100 text-blue-900"
          : "bg-slate-200 text-slate-700";
  return <Badge className={tone}>{status}</Badge>;
}

function PipelineBar({ counts, active, onPick }) {
  return (
    <div className="flex flex-wrap gap-2 items-center" data-testid="video-estimates-pipeline">
      {[{ id: null, label: "All" }, ...STATUS_FLOW.map((s) => ({ id: s, label: s }))].map((step) => (
        <Button
          key={step.id || "all"}
          variant={active === step.id ? "default" : "outline"}
          size="sm"
          onClick={() => onPick(step.id)}
        >
          {step.id && <span className="mr-1.5 inline-block w-2 h-2 rounded-full bg-current opacity-60" />}
          {step.label}
          <span className="ml-1.5 opacity-70">{counts[step.id || "all"] || 0}</span>
        </Button>
      ))}
    </div>
  );
}

function CreateModal({ onClose, onCreated, t }) {
  const [form, setForm] = useState({ customer_name: "", customer_email: "", customer_phone: "", job_type: "", notes: "" });
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState(null);
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  const save = async () => {
    if (!form.customer_name.trim()) return toast.error(t("video_estimates.customer_required"));
    setBusy(true);
    try {
      const { data } = await api.post(BASE, {
        customer_name: form.customer_name.trim(),
        customer_email: form.customer_email.trim() || null,
        customer_phone: form.customer_phone.trim() || null,
        job_type: form.job_type.trim(),
        notes: form.notes,
      });
      setCreated(data);
      onCreated(data);
      toast.success(t("video_estimates.request_created"));
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const copyLink = async () => {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(window.location.origin + created.upload_link);
      toast.success(t("video_estimates.link_copied"));
    } catch {
      toast.error(t("video_estimates.copy_failed"));
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-background border rounded-xl p-6 w-full max-w-lg space-y-4" data-testid="video-request-modal">
        <div className="flex items-center justify-between">
          <h3 className="font-medium text-lg">{t("video_estimates.new_request")}</h3>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label={t("video_estimates.close")}>
            <X className="h-4 w-4" />
          </Button>
        </div>
        {!created ? (
          <>
            <div className="grid sm:grid-cols-2 gap-3">
              <Input
                value={form.customer_name}
                onChange={(e) => set({ customer_name: e.target.value })}
                placeholder={t("video_estimates.customer_placeholder")}
              />
              <Input
                value={form.job_type}
                onChange={(e) => set({ job_type: e.target.value })}
                placeholder={t("video_estimates.job_type_placeholder")}
              />
              <Input
                value={form.customer_email}
                onChange={(e) => set({ customer_email: e.target.value })}
                placeholder={t("video_estimates.email_placeholder")}
              />
              <Input
                value={form.customer_phone}
                onChange={(e) => set({ customer_phone: e.target.value })}
                placeholder={t("video_estimates.phone_placeholder")}
              />
            </div>
            <Input
              value={form.notes}
              onChange={(e) => set({ notes: e.target.value })}
              placeholder={t("video_estimates.notes_placeholder")}
            />
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={onClose}>{t("video_estimates.cancel")}</Button>
              <Button onClick={save} disabled={busy}>
                {busy ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Link2 className="h-4 w-4 mr-2" />}
                {t("video_estimates.create_and_get_link")}
              </Button>
            </div>
          </>
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">{t("video_estimates.share_hint")}</p>
            <div className="flex items-center gap-2">
              <Input
                readOnly
                value={window.location.origin + created.upload_link}
                data-testid="video-share-link"
              />
              <Button variant="outline" size="sm" onClick={copyLink}>
                <Copy className="h-4 w-4 mr-1" /> {t("video_estimates.copy_link")}
              </Button>
            </div>
            <div className="flex justify-end">
              <Button onClick={onClose}>{t("video_estimates.done")}</Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function NoteComposer({ videoRef, onAdd, t }) {
  const [timestamp, setTimestamp] = useState("");
  const [note, setNote] = useState("");

  const add = () => {
    const sec = Number(timestamp);
    if (!(sec >= 0)) return toast.error(t("video_estimates.timestamp_required"));
    if (!note.trim()) return toast.error(t("video_estimates.note_required"));
    onAdd({ timestamp_sec: sec, note: note.trim() });
    setNote("");
  };

  const useCurrentTime = () => {
    if (videoRef.current) setTimestamp(String(Math.floor(videoRef.current.currentTime)));
  };

  return (
    <div className="border rounded-lg p-3 space-y-2">
      <div className="text-sm font-medium flex items-center gap-1">
        <Clock className="h-3.5 w-3.5" /> {t("video_estimates.add_timestamped_note")}
      </div>
      <div className="flex gap-2">
        <Input
          type="number"
          min="0"
          step="1"
          value={timestamp}
          onChange={(e) => setTimestamp(e.target.value)}
          placeholder={t("video_estimates.seconds_placeholder")}
          className="w-28"
        />
        <Input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder={t("video_estimates.note_placeholder")}
          className="flex-1"
        />
      </div>
      <div className="flex gap-2">
        <Button variant="outline" size="sm" onClick={useCurrentTime}>
          {t("video_estimates.use_current_time")}
        </Button>
        <Button size="sm" onClick={add}>
          <Plus className="h-3.5 w-3.5 mr-1" /> {t("video_estimates.add_note")}
        </Button>
      </div>
    </div>
  );
}

function DetailView({ requestId, onBack, onChanged, t }) {
  const [detail, setDetail] = useState(null);
  const [busy, setBusy] = useState(false);
  const [pendingNotes, setPendingNotes] = useState([]);
  const [lines, setLines] = useState([]);
  const [title, setTitle] = useState("");
  const videoRef = useRef(null);

  const load = async () => {
    setBusy(true);
    try {
      const { data } = await api.get(`${BASE}/${requestId}`);
      setDetail(data);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => { load(); }, [requestId]);

  const review = async (withEstimate) => {
    if (withEstimate) {
      const payloadLines = lines
        .filter((l) => l.description.trim())
        .map((l) => ({
          description: l.description.trim(),
          qty: Math.max(0.1, Number(l.qty) || 1),
          unit_price: Math.max(0, Number(l.unit_price) || 0),
        }));
      if (!payloadLines.length) return toast.error(t("video_estimates.one_line_required"));
      setBusy(true);
      try {
        const { data } = await api.post(`${BASE}/${requestId}/review`, {
          timestamped_notes: pendingNotes,
          lines: payloadLines,
          title: title.trim() || t("video_estimates.default_title"),
        });
        toast.success(t("video_estimates.estimate_created"));
        setPendingNotes([]);
        setLines([]);
        onChanged();
        load();
      } catch (e) {
        toast.error(errMessage(e));
        setBusy(false);
      }
      return;
    }
    if (!pendingNotes.length) return toast.error(t("video_estimates.note_required"));
    setBusy(true);
    try {
      await api.post(`${BASE}/${requestId}/review`, { timestamped_notes: pendingNotes });
      toast.success(t("video_estimates.notes_saved"));
      setPendingNotes([]);
      onChanged();
      load();
    } catch (e) {
      toast.error(errMessage(e));
      setBusy(false);
    }
  };

  if (!detail) {
    return (
      <div className="p-6 text-sm text-muted-foreground flex items-center gap-2">
        <Loader2 className="h-4 w-4 animate-spin" /> {t("video_estimates.loading")}
      </div>
    );
  }

  const timeline = [...(detail.review_notes || []), ...pendingNotes].sort(
    (a, b) => (a.timestamp_sec || 0) - (b.timestamp_sec || 0)
  );
  const canReview = detail.status === "uploaded" || detail.status === "in-review";

  return (
    <div className="space-y-6" data-testid="video-estimate-detail">
      <Button variant="ghost" size="sm" onClick={onBack}>
        <ArrowLeft className="h-4 w-4 mr-1" /> {t("video_estimates.back_to_list")}
      </Button>
      <div className="flex items-start justify-between">
        <div>
          <h3 className="font-medium text-lg">{detail.customer_name}</h3>
          <p className="text-sm text-muted-foreground">
            {detail.job_type || t("video_estimates.no_job_type")}
            {detail.notes ? ` · ${detail.notes}` : ""}
          </p>
        </div>
        <StatusBadge status={detail.status} />
      </div>

      {detail.video ? (
        <video
          ref={videoRef}
          src={`${API_BASE}/video-estimates/tenants/requests/${detail.id}/video`}
          controls
          className="w-full max-h-[480px] rounded-lg border bg-black"
          data-testid="video-player"
        />
      ) : (
        <div className="border rounded-lg p-6 text-center text-sm text-muted-foreground">
          {t("video_estimates.no_video_yet")}
        </div>
      )}

      <div className="space-y-2">
        <div className="text-sm font-medium">{t("video_estimates.notes_timeline")}</div>
        {timeline.length === 0 && (
          <p className="text-sm text-muted-foreground">{t("video_estimates.no_notes_yet")}</p>
        )}
        {timeline.map((n, i) => (
          <div key={i} className="flex gap-3 items-start border rounded-lg p-2.5 text-sm">
            <Badge variant="outline" className="shrink-0 font-mono">
              {fmtTimestamp(n.timestamp_sec)}
            </Badge>
            <span>{n.note}</span>
          </div>
        ))}
      </div>

      {canReview && (
        <NoteComposer
          videoRef={videoRef}
          t={t}
          onAdd={(n) => setPendingNotes((p) => [...p, n])}
        />
      )}

      {canReview && (
        <div className="border rounded-lg p-4 space-y-3">
          <h3 className="font-medium">{t("video_estimates.convert_to_estimate")}</h3>
          <div className="space-y-2">
            {lines.map((l, i) => (
              <div key={i} className="flex gap-2">
                <Input
                  value={l.description}
                  onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, description: e.target.value } : x)))}
                  placeholder={t("video_estimates.line_desc_placeholder")}
                  className="flex-1"
                />
                <Input
                  type="number" min="0.1" step="0.1" value={l.qty}
                  onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, qty: e.target.value } : x)))}
                  className="w-20" aria-label={t("video_estimates.qty")}
                />
                <Input
                  type="number" min="0" step="0.01" value={l.unit_price}
                  onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, unit_price: e.target.value } : x)))}
                  className="w-28" aria-label={t("video_estimates.price")}
                />
                <Button variant="ghost" size="sm" onClick={() => setLines(lines.filter((_, j) => j !== i))}>
                  <X className="h-4 w-4" />
                </Button>
              </div>
            ))}
          </div>
          <Button
            variant="outline" size="sm"
            onClick={() => setLines([...lines, { description: "", qty: 1, unit_price: "" }])}
          >
            <Plus className="h-3 w-3 mr-1" /> {t("video_estimates.add_line")}
          </Button>
          <Input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder={t("video_estimates.title_placeholder")}
          />
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => review(false)} disabled={busy || !pendingNotes.length}>
              {busy ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Clock className="h-4 w-4 mr-2" />}
              {t("video_estimates.save_notes")}
            </Button>
            <Button onClick={() => review(true)} disabled={busy || !lines.length}>
              {busy ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <CheckCircle2 className="h-4 w-4 mr-2" />}
              {t("video_estimates.create_estimate")}
            </Button>
          </div>
          {lines.length > 0 && (
            <p className="text-sm text-muted-foreground">
              {t("video_estimates.total")}: {fmtUSD(lines.reduce((s, l) => s + (Number(l.qty) || 0) * (Number(l.unit_price) || 0), 0))}
            </p>
          )}
        </div>
      )}

      {detail.quote_id && (
        <div className="border rounded-lg p-4 flex items-center gap-2 text-sm">
          <CheckCircle2 className="h-4 w-4 text-green-600" />
          {t("video_estimates.estimate_linked", { quoteId: detail.quote_id })}
        </div>
      )}
    </div>
  );
}

export default function VideoEstimates() {
  const { t } = useTranslation();
  const [requests, setRequests] = useState([]);
  const [statusFilter, setStatusFilter] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [showCreate, setShowCreate] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = async (status = statusFilter) => {
    setBusy(true);
    try {
      const { data } = await api.get(BASE, { params: status ? { status } : {} });
      setRequests(data.requests || []);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => { load(null); }, []);

  const pickStatus = (s) => {
    setStatusFilter(s);
    load(s);
  };

  const counts = { all: requests.length };
  requests.forEach((r) => { counts[r.status] = (counts[r.status] || 0) + 1; });

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      <PageHeader
        title={t("video_estimates.title")}
        description={t("video_estimates.subtitle")}
        actions={
          <Button onClick={() => setShowCreate(true)}>
            <Plus className="h-4 w-4 mr-1" /> {t("video_estimates.new_request")}
          </Button>
        }
      />

      {selectedId ? (
        <DetailView
          requestId={selectedId}
          onBack={() => setSelectedId(null)}
          onChanged={() => load(statusFilter)}
          t={t}
        />
      ) : (
        <>
          <PipelineBar counts={counts} active={statusFilter} onPick={pickStatus} />
          <div className="rounded-xl border overflow-hidden">
            <table className="w-full text-sm" data-testid="video-estimates-table">
              <thead className="bg-muted/60 text-left">
                <tr>
                  <th className="px-4 py-2.5 font-medium">{t("video_estimates.customer")}</th>
                  <th className="px-4 py-2.5 font-medium">{t("video_estimates.job_type")}</th>
                  <th className="px-4 py-2.5 font-medium">{t("video_estimates.status")}</th>
                  <th className="px-4 py-2.5 font-medium">{t("video_estimates.notes")}</th>
                  <th className="px-4 py-2.5 font-medium text-right">{t("video_estimates.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {requests.map((r) => (
                  <tr key={r.id} className="border-t hover:bg-muted/30" data-testid={`video-request-row-${r.id}`}>
                    <td className="px-4 py-2.5">
                      <div className="font-medium">{r.customer_name}</div>
                      <div className="text-xs text-muted-foreground">
                        {r.video
                          ? `${fmtTimestamp(r.video.duration_seconds)} · ${(r.video.size_bytes / 1048576).toFixed(1)} MB`
                          : t("video_estimates.awaiting_upload")}
                      </div>
                    </td>
                    <td className="px-4 py-2.5">{r.job_type || "—"}</td>
                    <td className="px-4 py-2.5"><StatusBadge status={r.status} /></td>
                    <td className="px-4 py-2.5 text-muted-foreground">
                      {(r.review_notes || []).length} {t("video_estimates.notes_count")}
                    </td>
                    <td className="px-4 py-2.5">
                      <div className="flex justify-end">
                        <Button variant="ghost" size="sm" onClick={() => setSelectedId(r.id)}>
                          <Video className="h-4 w-4 mr-1" /> {t("video_estimates.review")}
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
                {!requests.length && !busy && (
                  <tr>
                    <td colSpan={5} className="px-4 py-8 text-center text-muted-foreground">
                      {t("video_estimates.empty")}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}

      {showCreate && (
        <CreateModal
          t={t}
          onClose={() => setShowCreate(false)}
          onCreated={() => load(statusFilter)}
        />
      )}
    </div>
  );
}
