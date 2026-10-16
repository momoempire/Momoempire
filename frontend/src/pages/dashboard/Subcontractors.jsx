import { useEffect, useState } from "react";
import { api, errMessage } from "@/lib/api";
import { fmtUSD } from "@/lib/money";
import { useAuth } from "@/context/AuthContext";
import { useTranslation } from "react-i18next";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import {
  HardHat,
  ShieldAlert,
  ShieldCheck,
  ShieldQuestion,
  Plus,
  X,
  Loader2,
  FileText,
  Pencil,
  Trash2,
  Truck,
} from "lucide-react";

const BASE = "/subcontractors";

// ---------- compliance badge ----------

function InsuranceBadge({ sub, t }) {
  const info = sub?.insurance || {};
  const status = info.status || "missing";
  if (status === "expired")
    return (
      <Badge variant="destructive" data-testid="sub-insurance-expired">
        <ShieldAlert className="h-3 w-3 mr-1" />
        {t("insurance_expired")}
      </Badge>
    );
  if (status === "expiring_soon")
    return (
      <Badge variant="outline" className="border-amber-500 text-amber-600" data-testid="sub-insurance-soon">
        <ShieldAlert className="h-3 w-3 mr-1" />
        {t("insurance_days_left", { n: info.days_until_expiry })}
      </Badge>
    );
  if (status === "valid")
    return (
      <Badge variant="secondary" data-testid="sub-insurance-valid">
        <ShieldCheck className="h-3 w-3 mr-1" />
        {t("insurance_valid")}
      </Badge>
    );
  return (
    <Badge variant="outline" data-testid="sub-insurance-missing">
      <ShieldQuestion className="h-3 w-3 mr-1" />
      {t("insurance_missing")}
    </Badge>
  );
}

// ---------- sub form (add / edit) ----------

const EMPTY_FORM = {
  name: "",
  trade: "",
  phone: "",
  rate_type: "hourly",
  rate: "",
  insurance_expiry: "",
  license: "",
  w9_on_file: false,
  active: true,
};

function SubFormDialog({ open, initial, onClose, onSaved, t }) {
  const [form, setForm] = useState(EMPTY_FORM);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) setForm({ ...EMPTY_FORM, ...(initial || {}) });
  }, [open, initial]);

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const save = async () => {
    if (!form.name.trim() || !form.trade.trim()) {
      toast.error(errMessage(t("name") + " / " + t("trade")));
      return;
    }
    setBusy(true);
    try {
      const payload = {
        name: form.name.trim(),
        trade: form.trade.trim(),
        phone: form.phone.trim(),
        rate_type: form.rate_type,
        rate: parseFloat(form.rate) || 0,
        insurance_expiry: form.insurance_expiry || null,
        license: form.license.trim(),
        w9_on_file: !!form.w9_on_file,
        active: !!form.active,
      };
      if (initial?.id) {
        await api.patch(`${BASE}/subs/${initial.id}`, payload);
      } else {
        await api.post(`${BASE}/subs`, payload);
      }
      toast.success(t("saved_toast"));
      onSaved();
      onClose();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent data-testid="sub-form-dialog">
        <DialogHeader>
          <DialogTitle>{initial?.id ? t("edit_sub") : t("add_sub")}</DialogTitle>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-3">
          <label className="space-y-1">
            <span className="text-sm text-muted-foreground">{t("name")}</span>
            <Input value={form.name} onChange={(e) => set("name", e.target.value)} data-testid="sub-form-name" />
          </label>
          <label className="space-y-1">
            <span className="text-sm text-muted-foreground">{t("trade")}</span>
            <Input value={form.trade} onChange={(e) => set("trade", e.target.value)} data-testid="sub-form-trade" />
          </label>
          <label className="space-y-1">
            <span className="text-sm text-muted-foreground">{t("phone")}</span>
            <Input value={form.phone} onChange={(e) => set("phone", e.target.value)} />
          </label>
          <label className="space-y-1">
            <span className="text-sm text-muted-foreground">{t("license")}</span>
            <Input value={form.license} onChange={(e) => set("license", e.target.value)} />
          </label>
          <label className="space-y-1">
            <span className="text-sm text-muted-foreground">{t("rate_type")}</span>
            <select
              value={form.rate_type}
              onChange={(e) => set("rate_type", e.target.value)}
              className="flex h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
            >
              <option value="hourly">{t("hourly")}</option>
              <option value="flat">{t("flat")}</option>
            </select>
          </label>
          <label className="space-y-1">
            <span className="text-sm text-muted-foreground">{t("rate")}</span>
            <Input type="number" min="0" step="0.01" value={form.rate} onChange={(e) => set("rate", e.target.value)} />
          </label>
          <label className="space-y-1 col-span-2">
            <span className="text-sm text-muted-foreground">{t("insurance_expiry")}</span>
            <Input type="date" value={form.insurance_expiry || ""} onChange={(e) => set("insurance_expiry", e.target.value)} />
          </label>
          <label className="flex items-center gap-2 text-sm col-span-1">
            <input type="checkbox" checked={form.w9_on_file} onChange={(e) => set("w9_on_file", e.target.checked)} />
            {t("w9_on_file")}
          </label>
          <label className="flex items-center gap-2 text-sm col-span-1">
            <input type="checkbox" checked={form.active} onChange={(e) => set("active", e.target.checked)} />
            {t("active")}
          </label>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>{t("cancel")}</Button>
          <Button onClick={save} disabled={busy} data-testid="sub-form-save">
            {busy && <Loader2 className="h-4 w-4 mr-1 animate-spin" />}
            {t("save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------- detail drawer ----------

function DetailDrawer({ sub, onClose, onChanged, canManage, t }) {
  const [jobs, setJobs] = useState([]);
  const [payments, setPayments] = useState([]);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [jobId, setJobId] = useState("");
  const [dispatching, setDispatching] = useState(false);
  const [payForm, setPayForm] = useState({
    job_id: "",
    amount: "",
    date: new Date().toISOString().slice(0, 10),
    method: "check",
    note: "",
  });
  const [paying, setPaying] = useState(false);
  const yearNow = new Date().getFullYear();
  const [year, setYear] = useState(yearNow);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const [j, p, s] = await Promise.all([
        api.get(`${BASE}/subs/${sub.id}/jobs`),
        api.get(`${BASE}/subs/${sub.id}/payments`),
        api.get(`${BASE}/subs/${sub.id}/pay-summary`, { params: { year } }),
      ]);
      setJobs(j.data.jobs || []);
      setPayments(p.data.payments || []);
      setSummary(s.data);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sub.id, year]);

  const dispatchJob = async () => {
    if (!jobId.trim()) return;
    setDispatching(true);
    try {
      await api.post(`${BASE}/subs/${sub.id}/dispatch`, { job_id: jobId.trim() });
      toast.success(t("dispatched_toast", { name: sub.name }));
      setJobId("");
      load();
      onChanged();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setDispatching(false);
    }
  };

  const recordPayment = async () => {
    if (!payForm.job_id.trim() || !payForm.amount) return;
    setPaying(true);
    try {
      await api.post(`${BASE}/subs/${sub.id}/payments`, {
        job_id: payForm.job_id.trim(),
        amount: parseFloat(payForm.amount),
        date: payForm.date,
        method: payForm.method,
        note: payForm.note.trim(),
      });
      toast.success(t("payment_recorded_toast", { amount: fmtUSD(parseFloat(payForm.amount)) }));
      setPayForm((f) => ({ ...f, job_id: "", amount: "", note: "" }));
      load();
      onChanged();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setPaying(false);
    }
  };

  const remove = async () => {
    try {
      await api.delete(`${BASE}/subs/${sub.id}`);
      toast.success(t("deleted_toast"));
      onChanged();
      onClose();
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const methodLabel = (m) => t(`method_${m}`) || m;

  return (
    <div className="fixed inset-0 z-50" data-testid="sub-detail-drawer">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <aside className="absolute inset-y-0 right-0 w-full max-w-xl bg-background border-l shadow-xl overflow-y-auto">
        <div className="p-6 space-y-6">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <HardHat className="h-5 w-5 text-muted-foreground shrink-0" />
                <h2 className="text-xl font-semibold truncate">{sub.name}</h2>
              </div>
              <div className="text-sm text-muted-foreground mt-1">
                {sub.trade}
                {sub.phone ? ` · ${sub.phone}` : ""}
                {sub.license ? ` · ${sub.license}` : ""} ·{" "}
                {sub.rate_type === "hourly" ? `${fmtUSD(sub.rate)}/hr` : fmtUSD(sub.rate)}
              </div>
              <div className="flex flex-wrap gap-1.5 mt-2">
                <InsuranceBadge sub={sub} t={t} />
                {sub.w9_on_file ? (
                  <Badge variant="secondary"><FileText className="h-3 w-3 mr-1" />{t("w9_badge")}</Badge>
                ) : (
                  <Badge variant="outline">{t("no_w9_badge")}</Badge>
                )}
                {!sub.active && <Badge variant="outline">{t("inactive_badge")}</Badge>}
              </div>
            </div>
            <Button variant="ghost" size="icon" onClick={onClose} data-testid="sub-drawer-close">
              <X className="h-4 w-4" />
            </Button>
          </div>

          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={onClose}>
              {t("back_to_list")}
            </Button>
            {!confirmDelete ? (
              <Button variant="ghost" size="sm" className="text-destructive" onClick={() => setConfirmDelete(true)}>
                <Trash2 className="h-4 w-4 mr-1" /> {t("delete")}
              </Button>
            ) : (
              <>
                <span className="text-sm text-muted-foreground">{t("confirm_delete")}</span>
                <Button variant="destructive" size="sm" onClick={remove}>{t("delete")}</Button>
                <Button variant="ghost" size="sm" onClick={() => setConfirmDelete(false)}>{t("cancel")}</Button>
              </>
            )}
          </div>

          {/* jobs */}
          <section className="space-y-3">
            <h3 className="font-medium flex items-center gap-2">
              <Truck className="h-4 w-4" /> {t("jobs_section")}
            </h3>
            <div className="flex gap-2">
              <Input
                value={jobId}
                onChange={(e) => setJobId(e.target.value)}
                placeholder={t("job_id_placeholder")}
                data-testid="sub-dispatch-job-id"
              />
              <Button size="sm" onClick={dispatchJob} disabled={dispatching || !jobId.trim()} data-testid="sub-dispatch-btn">
                {dispatching ? <Loader2 className="h-4 w-4 animate-spin" /> : t("dispatch")}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">{t("dispatch_hint")}</p>
            {jobs.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("jobs_empty")}</p>
            ) : (
              <ul className="space-y-2">
                {jobs.map((j) => (
                  <li key={j.id} className="border rounded-lg p-3 text-sm flex justify-between gap-2">
                    <span className="min-w-0">
                      <span className="font-medium truncate block">{j.service || j.id}</span>
                      <span className="text-muted-foreground">
                        {j.customer_name}
                        {j.start_at ? ` · ${j.start_at.slice(0, 10)}` : ""}
                      </span>
                    </span>
                    {j.status && <Badge variant="outline">{j.status}</Badge>}
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* payments */}
          <section className="space-y-3">
            <h3 className="font-medium">{t("payments_section")}</h3>
            {canManage ? (
              <div className="border rounded-lg p-4 space-y-3">
                <div className="grid grid-cols-2 gap-2">
                  <label className="space-y-1">
                    <span className="text-xs text-muted-foreground">{t("job_id_label")}</span>
                    <Input value={payForm.job_id} onChange={(e) => setPayForm((f) => ({ ...f, job_id: e.target.value }))} placeholder={t("job_id_placeholder")} data-testid="sub-pay-job" />
                  </label>
                  <label className="space-y-1">
                    <span className="text-xs text-muted-foreground">{t("amount")}</span>
                    <Input type="number" min="0" step="0.01" value={payForm.amount} onChange={(e) => setPayForm((f) => ({ ...f, amount: e.target.value }))} data-testid="sub-pay-amount" />
                  </label>
                  <label className="space-y-1">
                    <span className="text-xs text-muted-foreground">{t("date")}</span>
                    <Input type="date" value={payForm.date} onChange={(e) => setPayForm((f) => ({ ...f, date: e.target.value }))} />
                  </label>
                  <label className="space-y-1">
                    <span className="text-xs text-muted-foreground">{t("method")}</span>
                    <select
                      value={payForm.method}
                      onChange={(e) => setPayForm((f) => ({ ...f, method: e.target.value }))}
                      className="flex h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
                    >
                      {["check", "cash", "ach", "card", "other"].map((m) => (
                        <option key={m} value={m}>{methodLabel(m)}</option>
                      ))}
                    </select>
                  </label>
                  <label className="space-y-1 col-span-2">
                    <span className="text-xs text-muted-foreground">{t("note")}</span>
                    <Input value={payForm.note} onChange={(e) => setPayForm((f) => ({ ...f, note: e.target.value }))} placeholder={t("note_placeholder")} />
                  </label>
                </div>
                <Button size="sm" onClick={recordPayment} disabled={paying || !payForm.job_id.trim() || !payForm.amount} data-testid="sub-pay-submit">
                  {paying ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Plus className="h-4 w-4 mr-1" />}
                  {t("add_payment")}
                </Button>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">{t("owner_only")}</p>
            )}
            {payments.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("payments_empty")}</p>
            ) : (
              <ul className="space-y-2">
                {payments.map((p) => (
                  <li key={p.id} className="border rounded-lg p-3 text-sm flex justify-between gap-2">
                    <span className="min-w-0">
                      <span className="font-medium">{fmtUSD(p.amount)}</span>
                      <span className="text-muted-foreground"> · {p.date} · {methodLabel(p.method)}</span>
                      {p.note && <span className="block text-muted-foreground truncate">{p.note}</span>}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* 1099 summary */}
          <section className="space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="font-medium">{t("summary_title")}</h3>
              <select
                value={year}
                onChange={(e) => setYear(parseInt(e.target.value, 10))}
                className="h-9 rounded-md border border-input bg-background px-2 text-sm"
                aria-label={t("year")}
              >
                {[yearNow, yearNow - 1, yearNow - 2].map((y) => (
                  <option key={y} value={y}>{y}</option>
                ))}
              </select>
            </div>
            {loading ? (
              <div className="flex items-center gap-2 text-muted-foreground text-sm">
                <Loader2 className="h-4 w-4 animate-spin" /> {t("loading")}
              </div>
            ) : summary ? (
              <div className="space-y-3" data-testid="sub-1099-summary">
                <div className="surface p-4 flex items-center justify-between gap-3">
                  <div>
                    <div className="text-xs text-muted-foreground">{t("total_paid")} · {summary.year}</div>
                    <div className="text-2xl font-semibold">{fmtUSD(summary.total_paid)}</div>
                    <div className="text-xs text-muted-foreground mt-1">
                      {t("payment_count")}: {summary.payment_count} · {t("filing_threshold_label")}: {fmtUSD(summary.filing_threshold)}
                    </div>
                  </div>
                  {summary.ready_to_file ? (
                    <Badge className="bg-green-600" data-testid="sub-ready-to-file">
                      <ShieldCheck className="h-3 w-3 mr-1" /> {t("ready_to_file")}
                    </Badge>
                  ) : (
                    <Badge variant="outline" data-testid="sub-not-ready">
                      <ShieldAlert className="h-3 w-3 mr-1" /> {t("not_ready")}
                    </Badge>
                  )}
                </div>
                {!summary.ready_to_file && (
                  <p className="text-xs text-muted-foreground">
                    {!summary.w9_on_file ? t("w9_needed") : t("below_threshold", { threshold: fmtUSD(summary.filing_threshold) })}
                  </p>
                )}
                <div>
                  <div className="text-sm font-medium mb-2">{t("per_job")}</div>
                  {summary.per_job.length === 0 ? (
                    <p className="text-sm text-muted-foreground">{t("no_breakdown")}</p>
                  ) : (
                    <ul className="space-y-2">
                      {summary.per_job.map((row) => (
                        <li key={row.job_id} className="border rounded-lg p-3 text-sm flex justify-between gap-2">
                          <span className="min-w-0">
                            <span className="font-medium truncate block">{row.job_label || row.job_id}</span>
                            <span className="text-muted-foreground">{t("payment_count")}: {row.payments}</span>
                          </span>
                          <span className="font-medium shrink-0">{fmtUSD(row.total)}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            ) : null}
          </section>
        </div>
      </aside>
    </div>
  );
}

// ---------- main page ----------

export default function Subcontractors() {
  const { t } = useTranslation("subcontractors");
  const { user } = useAuth();
  const canManage = user?.role === "owner" || user?.role === "admin" || user?.role === "platform_admin";

  const [subs, setSubs] = useState([]);
  const [alerts, setAlerts] = useState({ expired: [], expiring_soon: [], count: 0 });
  const [loading, setLoading] = useState(true);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [selected, setSelected] = useState(null);

  const load = async () => {
    setLoading(true);
    try {
      const [s, a] = await Promise.all([
        api.get(`${BASE}/subs`),
        api.get(`${BASE}/alerts`),
      ]);
      setSubs(s.data.subcontractors || []);
      setAlerts(a.data);
    } catch (e) {
      toast.error(t("load_failed"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const openAdd = () => {
    setEditing(null);
    setFormOpen(true);
  };
  const openEdit = (sub) => {
    setEditing(sub);
    setFormOpen(true);
  };

  return (
    <div className="space-y-6" data-testid="subcontractors-page">
      <PageHeader
        eyebrow={t("eyebrow")}
        title={t("title")}
        description={t("sub")}
        actions={
          <Button onClick={openAdd} data-testid="sub-add-btn">
            <Plus className="h-4 w-4 mr-1" /> {t("add_sub")}
          </Button>
        }
      />

      {!loading && alerts.count > 0 && (
        <div className="surface p-4 space-y-2" data-testid="sub-alerts">
          <div className="flex items-center gap-2 font-medium text-sm">
            <ShieldAlert className="h-4 w-4 text-amber-600" />
            {t("alerts_title")} ({alerts.count})
          </div>
          <ul className="space-y-1 text-sm">
            {alerts.expired.map((a) => (
              <li key={a.sub_id} className="flex items-center gap-2">
                <Badge variant="destructive">{t("expired_label")}</Badge>
                <span>{a.name} · {a.trade} · {t("insurance_days_overdue", { n: Math.abs(a.days_until_expiry) })}</span>
              </li>
            ))}
            {alerts.expiring_soon.map((a) => (
              <li key={a.sub_id} className="flex items-center gap-2">
                <Badge variant="outline" className="border-amber-500 text-amber-600">{t("expiring_soon_label")}</Badge>
                <span>{a.name} · {a.trade} · {t("insurance_days_left", { n: a.days_until_expiry })}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> {t("loading")}
        </div>
      ) : subs.length === 0 ? (
        <div className="surface p-8 text-center text-muted-foreground" data-testid="sub-empty">
          <HardHat className="h-8 w-8 mx-auto mb-2 opacity-50" />
          {t("empty")}
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {subs.map((sub) => (
            <div
              key={sub.id}
              className="surface p-4 space-y-3 cursor-pointer hover:border-primary/40 transition-colors"
              onClick={() => setSelected(sub)}
              data-testid={`sub-card-${sub.id}`}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="font-medium truncate">{sub.name}</div>
                  <div className="text-sm text-muted-foreground truncate">
                    {sub.trade}
                    {sub.phone ? ` · ${sub.phone}` : ""}
                  </div>
                </div>
                {!sub.active && <Badge variant="outline">{t("inactive_badge")}</Badge>}
              </div>
              <div className="text-sm text-muted-foreground">
                {sub.rate_type === "hourly" ? `${fmtUSD(sub.rate)}/hr` : fmtUSD(sub.rate)}
                {sub.license ? ` · ${sub.license}` : ""}
              </div>
              <div className="flex flex-wrap items-center gap-1.5">
                <InsuranceBadge sub={sub} t={t} />
                {sub.w9_on_file ? (
                  <Badge variant="secondary"><FileText className="h-3 w-3 mr-1" />{t("w9_badge")}</Badge>
                ) : (
                  <Badge variant="outline">{t("no_w9_badge")}</Badge>
                )}
              </div>
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={(e) => { e.stopPropagation(); setSelected(sub); }}>
                  {t("view_details")}
                </Button>
                <Button size="sm" variant="ghost" onClick={(e) => { e.stopPropagation(); openEdit(sub); }} data-testid={`sub-edit-${sub.id}`}>
                  <Pencil className="h-3 w-3 mr-1" /> {t("edit_sub")}
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      <SubFormDialog
        open={formOpen}
        initial={editing}
        onClose={() => setFormOpen(false)}
        onSaved={load}
        t={t}
      />

      {selected && (
        <DetailDrawer
          sub={selected}
          onClose={() => setSelected(null)}
          onChanged={load}
          canManage={canManage}
          t={t}
        />
      )}
    </div>
  );
}
