import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errMessage, API_BASE } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { FileText, Send, Eye, Plus, Camera, Wrench, Link2, Copy, Check, Calendar, X, Sparkles, BellOff } from "lucide-react";

const PRIORITY_STYLES = {
  high: "bg-rose-100 text-rose-800",
  medium: "bg-amber-100 text-amber-800",
  low: "bg-sky-100 text-sky-800",
};

function publicLink(token) {
  return `${API_BASE}/home-reports/public/${token}`;
}

function GenerateModal({ onClose, onGenerated }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({ job_id: "", technician_notes: "", before_photo_ids: "", after_photo_ids: "" });
  const [working, setWorking] = useState(false);
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const splitIds = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);

  const generate = async () => {
    if (!form.job_id.trim()) return toast.error(t("homeReports.job_required", "Job ID is required"));
    setWorking(true);
    try {
      const r = await api.post("/home-reports/generate", {
        job_id: form.job_id.trim(),
        technician_notes: form.technician_notes.trim(),
        before_photo_ids: splitIds(form.before_photo_ids),
        after_photo_ids: splitIds(form.after_photo_ids),
      });
      if (r.data.duplicate) toast.info(t("homeReports.already_exists", "A report already exists for this job."));
      else toast.success(t("homeReports.generated", "Home report generated."));
      onGenerated(r.data);
      onClose();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setWorking(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-xl bg-card p-6 shadow-xl" data-testid="hr-generate-modal">
        <h3 className="text-lg font-semibold mb-1">{t("homeReports.generate_title", "Generate home report")}</h3>
        <p className="text-sm text-muted-foreground mb-4">
          {t("homeReports.generate_hint", "Build a homeowner-facing report from a completed job: summary, photos, equipment snapshot, and AI recommendations.")}
        </p>
        <div className="space-y-3">
          <Input placeholder={t("homeReports.job_id", "Job ID *")} value={form.job_id} onChange={(e) => set({ job_id: e.target.value })} />
          <textarea
            placeholder={t("homeReports.tech_notes_ph", "Technician notes (shown to the homeowner)…")}
            value={form.technician_notes}
            onChange={(e) => set({ technician_notes: e.target.value })}
            rows={3}
            className="w-full rounded-md border bg-card px-3 py-2 text-sm"
          />
          <Input placeholder={t("homeReports.before_ids", "Before photo IDs (comma-separated, optional)")} value={form.before_photo_ids} onChange={(e) => set({ before_photo_ids: e.target.value })} />
          <Input placeholder={t("homeReports.after_ids", "After photo IDs (comma-separated, optional)")} value={form.after_photo_ids} onChange={(e) => set({ after_photo_ids: e.target.value })} />
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <Button variant="outline" onClick={onClose}>{t("homeReports.cancel", "Cancel")}</Button>
          <Button onClick={generate} disabled={working} data-testid="hr-generate-submit">
            <Sparkles className="h-4 w-4 mr-1" /> {working ? t("homeReports.generating", "Generating…") : t("homeReports.generate", "Generate")}
          </Button>
        </div>
      </div>
    </div>
  );
}

function ShareModal({ report, onClose, onSent }) {
  const { t } = useTranslation();
  const [channel, setChannel] = useState(report?.sent_channel || "link");
  const [copied, setCopied] = useState(false);
  const [sending, setSending] = useState(false);
  const link = report?.share_token ? publicLink(report.share_token) : "";

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error(t("homeReports.copy_failed", "Could not copy to clipboard."));
    }
  };

  const send = async () => {
    setSending(true);
    try {
      const r = await api.post(`/home-reports/${encodeURIComponent(report.id)}/send`, { channel });
      toast.success(t("homeReports.sent", "Report marked as sent."));
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
      <div className="w-full max-w-md rounded-xl bg-card p-6 shadow-xl" data-testid="hr-share-modal">
        <div className="flex items-start justify-between mb-1">
          <h3 className="text-lg font-semibold">{t("homeReports.share_title", "Share with the homeowner")}</h3>
          <button onClick={onClose} aria-label={t("homeReports.close", "Close")}><X className="h-5 w-5 text-muted-foreground" /></button>
        </div>
        <p className="text-sm text-muted-foreground mb-4">
          {t("homeReports.share_hint", "The homeowner opens this magic link — no login needed. Every view is tracked.")}
        </p>
        <div className="flex items-center gap-2 rounded-lg border bg-muted/40 px-3 py-2 mb-4">
          <Link2 className="h-4 w-4 shrink-0 text-muted-foreground" />
          <span className="text-xs truncate flex-1" data-testid="hr-share-link">{link}</span>
          <Button size="sm" variant="outline" onClick={copy} data-testid="hr-share-copy">
            {copied ? <Check className="h-3.5 w-3.5 mr-1 text-emerald-600" /> : <Copy className="h-3.5 w-3.5 mr-1" />}
            {copied ? t("homeReports.copied", "Copied") : t("homeReports.copy", "Copy")}
          </Button>
        </div>
        <label className="block text-sm mb-1">{t("homeReports.channel", "How was it sent?")}</label>
        <select value={channel} onChange={(e) => setChannel(e.target.value)} className="rounded-md border bg-card px-3 py-2 text-sm w-full mb-5">
          <option value="link">{t("homeReports.channel_link", "Link (copied / other)")}</option>
          <option value="sms">{t("homeReports.channel_sms", "SMS")}</option>
          <option value="email">{t("homeReports.channel_email", "Email")}</option>
        </select>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>{t("homeReports.cancel", "Cancel")}</Button>
          <Button onClick={send} disabled={sending} data-testid="hr-share-send">
            <Send className="h-4 w-4 mr-1" /> {sending ? t("homeReports.sending", "Sending…") : t("homeReports.mark_sent", "Mark sent")}
          </Button>
        </div>
      </div>
    </div>
  );
}

function PreviewModal({ report, onClose }) {
  const { t } = useTranslation();
  const r = report || {};
  const photos = r.photos || { before: [], after: [] };
  const recs = r.recommendations || [];
  const equip = r.equipment_snapshot || [];
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 overflow-y-auto">
      <div className="w-full max-w-2xl rounded-xl bg-card p-6 shadow-xl my-8" data-testid="hr-preview-modal">
        <div className="flex items-start justify-between mb-4">
          <div>
            <p className="text-xs text-muted-foreground">{r.business_name}</p>
            <h3 className="text-xl font-bold">{r.service_summary}</h3>
            <p className="text-sm text-muted-foreground flex items-center gap-1.5 mt-1">
              <Calendar className="h-3.5 w-3.5" />
              {r.service_date ? new Date(r.service_date).toLocaleString() : ""}
              {r.customer_name ? ` · ${r.customer_name}` : ""}
              {r.technician ? ` · ${t("homeReports.tech", "Tech")}: ${r.technician}` : ""}
            </p>
          </div>
          <button onClick={onClose} aria-label={t("homeReports.close", "Close")}><X className="h-5 w-5 text-muted-foreground" /></button>
        </div>

        {r.service_detail && (
          <div className="mb-4">
            <h4 className="font-semibold text-sm mb-1">{t("homeReports.what_done", "What was done")}</h4>
            <p className="text-sm">{r.service_detail}</p>
          </div>
        )}
        {r.technician_notes && (
          <div className="rounded-lg bg-muted/50 p-3 text-sm mb-4">
            <div className="text-xs text-muted-foreground mb-1">{t("homeReports.tech_notes", "Technician notes")}</div>
            {r.technician_notes}
          </div>
        )}

        {(photos.before.length > 0 || photos.after.length > 0) && (
          <div className="mb-4">
            <h4 className="font-semibold text-sm mb-2 flex items-center gap-1.5"><Camera className="h-4 w-4" /> {t("homeReports.before_after", "Before / after")}</h4>
            <div className="grid grid-cols-2 gap-3">
              {["before", "after"].map((side) => (
                <div key={side}>
                  <div className="text-xs text-muted-foreground mb-1 capitalize">{t(`homeReports.${side}`, side)}</div>
                  {(photos[side] || []).length === 0 ? (
                    <div className="rounded-lg border border-dashed p-4 text-center text-xs text-muted-foreground">{t("homeReports.no_photo", "No photo")}</div>
                  ) : (
                    (photos[side] || []).map((p) => (
                      <div key={p.id} className="rounded-lg border p-2 text-xs mb-1">
                        <span className="font-medium">{p.filename || p.id}</span>
                        {p.notes && <span className="block text-muted-foreground">{p.notes}</span>}
                      </div>
                    ))
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {equip.length > 0 && (
          <div className="mb-4">
            <h4 className="font-semibold text-sm mb-2 flex items-center gap-1.5"><Wrench className="h-4 w-4" /> {t("homeReports.equipment", "Equipment health")}</h4>
            <div className="space-y-2">
              {equip.map((e, i) => (
                <div key={i} className="rounded-lg border p-3 text-sm flex items-center justify-between gap-2">
                  <div>
                    <span className="font-medium capitalize">{e.equipment_type}</span>
                    {[e.make, e.model].filter(Boolean).join(" ") && (
                      <span className="text-muted-foreground"> · {[e.make, e.model].filter(Boolean).join(" ")}</span>
                    )}
                    {e.install_date && (
                      <span className="block text-xs text-muted-foreground">{t("homeReports.installed", "Installed")}: {new Date(e.install_date).toLocaleDateString()}</span>
                    )}
                  </div>
                  <Badge className="shrink-0">{e.health}</Badge>
                </div>
              ))}
            </div>
          </div>
        )}

        {recs.length > 0 && (
          <div className="mb-2">
            <h4 className="font-semibold text-sm mb-2 flex items-center gap-1.5"><Sparkles className="h-4 w-4" /> {t("homeReports.recommendations", "AI recommendations")}</h4>
            <div className="space-y-2">
              {recs.map((rec, i) => (
                <div key={i} className="rounded-lg border p-3 text-sm">
                  <div className="flex items-center gap-2 mb-1">
                    <Badge className={PRIORITY_STYLES[rec.priority] || PRIORITY_STYLES.low}>
                      {t(`homeReports.priority_${rec.priority}`, rec.priority)}
                    </Badge>
                    <span className="font-medium">{rec.title}</span>
                  </div>
                  <p className="text-muted-foreground">{rec.text}</p>
                  {rec.due && <p className="text-xs mt-1"><span className="text-muted-foreground">{t("homeReports.due", "When")}: </span>{rec.due}</p>}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function HomeReports() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [reports, setReports] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showGenerate, setShowGenerate] = useState(false);
  const [preview, setPreview] = useState(null);
  const [shareFor, setShareFor] = useState(null);
  const canManage = user && ["owner", "admin"].includes(user.role);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get("/home-reports/");
      setReports(r.data.reports || []);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const counts = useMemo(() => ({
    total: reports.length,
    sent: reports.filter((r) => r.status === "sent").length,
    views: reports.reduce((n, r) => n + (r.tracking?.views || 0), 0),
  }), [reports]);

  return (
    <div>
      <PageHeader
        title={t("homeReports.title", "AI Home Reports")}
        description={t("homeReports.description", "Beautiful homeowner-facing reports after every visit — summary, before/after photos, equipment health, and AI recommendations, shared by magic link.")}
        testId="hr-header"
        actions={
          canManage && (
            <Button size="sm" onClick={() => setShowGenerate(true)} data-testid="hr-generate-open">
              <Plus className="h-4 w-4 mr-1" /> {t("homeReports.new_report", "Generate report")}
            </Button>
          )
        }
      />

      {loading ? (
        <div className="text-muted-foreground">{t("homeReports.loading", "Loading home reports…")}</div>
      ) : (
        <>
          {/* KPI header */}
          <div className="grid grid-cols-2 lg:grid-cols-3 gap-3 mb-5" data-testid="hr-kpis">
            {[
              { label: t("homeReports.kpi_reports", "Reports"), value: counts.total, icon: <FileText className="h-4 w-4 text-violet-500" /> },
              { label: t("homeReports.kpi_sent", "Sent"), value: counts.sent, icon: <Send className="h-4 w-4 text-emerald-500" /> },
              { label: t("homeReports.kpi_views", "Homeowner views"), value: counts.views, icon: <Eye className="h-4 w-4 text-sky-500" /> },
            ].map((k) => (
              <div key={k.label} className="rounded-xl border bg-card p-4">
                <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">{k.icon}{k.label}</div>
                <div className="text-2xl font-bold">{k.value}</div>
              </div>
            ))}
          </div>

          {reports.length === 0 ? (
            <div className="rounded-xl border bg-muted/40 p-8 text-center text-muted-foreground">
              <BellOff className="h-6 w-6 mx-auto mb-2" />
              {t("homeReports.empty", "No reports yet. Generate one from a completed job to wow the homeowner.")}
            </div>
          ) : (
            <div className="space-y-3">
              {reports.map((r) => {
                const tr = r.tracking || {};
                const photos = r.photos || { before: [], after: [] };
                const photoCount = (photos.before || []).length + (photos.after || []).length;
                return (
                  <div key={r.id} className="rounded-xl border bg-card p-4" data-testid={`hr-report-${r.id}`}>
                    <div className="flex flex-wrap items-center gap-2 mb-1">
                      <span className="font-medium text-sm">{r.customer_name || t("homeReports.anonymous", "Homeowner")}</span>
                      <span className="text-sm text-muted-foreground">· {r.service_summary}</span>
                    </div>
                    <div className="text-xs text-muted-foreground mb-2">
                      {r.service_date ? new Date(r.service_date).toLocaleDateString() : ""}
                      {r.technician ? ` · ${t("homeReports.tech", "Tech")}: ${r.technician}` : ""}
                      {photoCount > 0 && ` · ${photoCount} ${t("homeReports.photos", "photos")}`}
                      {(r.recommendations || []).length > 0 && ` · ${(r.recommendations || []).length} ${t("homeReports.recs", "recs")}`}
                    </div>
                    <div className="flex flex-wrap items-center gap-2 mb-3">
                      {r.status === "sent" ? (
                        <Badge className="bg-emerald-100 text-emerald-800">{t("homeReports.sent_badge", "Sent")}</Badge>
                      ) : (
                        <Badge variant="secondary">{t("homeReports.draft_badge", "Draft")}</Badge>
                      )}
                      <Badge variant="outline" className="flex items-center gap-1">
                        <Eye className="h-3 w-3" /> {tr.views || 0} {t("homeReports.views", "views")}
                      </Badge>
                      {tr.last_viewed_at && (
                        <span className="text-xs text-muted-foreground">
                          {t("homeReports.last_viewed", "Last viewed")}: {new Date(tr.last_viewed_at).toLocaleString()}
                        </span>
                      )}
                    </div>
                    <div className="flex gap-2">
                      <Button size="sm" variant="outline" onClick={() => setPreview(r)} data-testid={`hr-preview-${r.id}`}>
                        <FileText className="h-3.5 w-3.5 mr-1" /> {t("homeReports.preview", "Preview")}
                      </Button>
                      {canManage && (
                        <Button size="sm" onClick={() => setShareFor(r)} data-testid={`hr-share-${r.id}`}>
                          <Send className="h-3.5 w-3.5 mr-1" /> {r.status === "sent" ? t("homeReports.resend", "Resend") : t("homeReports.share", "Share")}
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

      {showGenerate && <GenerateModal onClose={() => setShowGenerate(false)} onGenerated={load} />}
      {preview && <PreviewModal report={preview} onClose={() => setPreview(null)} />}
      {shareFor && <ShareModal report={shareFor} onClose={() => setShareFor(null)} onSent={load} />}
    </div>
  );
}
