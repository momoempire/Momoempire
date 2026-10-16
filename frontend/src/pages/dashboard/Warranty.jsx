import { useEffect, useState } from "react";
import { api, errMessage } from "@/lib/api";
import { useTranslation } from "react-i18next";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import {
  ShieldCheck,
  Loader2,
  Plus,
  X,
  AlertTriangle,
  CheckCircle2,
  RotateCcw,
  CalendarClock,
} from "lucide-react";

const BASE = "/warranty";

const COVERAGE_TYPES = ["equipment", "labor", "parts", "workmanship"];
const STATUSES = ["all", "active", "upcoming", "expired"];

function statusBadgeVariant(status) {
  switch (status) {
    case "active":
      return "default";
    case "upcoming":
      return "secondary";
    default:
      return "outline";
  }
}

function callbackBadgeVariant(status) {
  switch (status) {
    case "escalated":
      return "destructive";
    case "resolved":
      return "secondary";
    default:
      return "outline";
  }
}

function WarrantyForm({ onSaved, onCancel, t }) {
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    customer_name: "",
    customer_id: "",
    equipment_id: "",
    job_id: "",
    coverage_type: "equipment",
    start_date: "",
    end_date: "",
    terms: "",
  });

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const save = async () => {
    if (!form.start_date || !form.end_date) {
      toast.error(t("warranty.dates_required"));
      return;
    }
    setBusy(true);
    try {
      const payload = {
        ...form,
        equipment_id: form.equipment_id.trim() || null,
        job_id: form.job_id.trim() || null,
      };
      await api.post(`${BASE}/warranties`, payload);
      toast.success(t("warranty.saved_toast"));
      onSaved();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="surface p-4 space-y-3" data-testid="warranty-form">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <Input value={form.customer_name} onChange={set("customer_name")} placeholder={t("warranty.customer_name")} data-testid="warranty-field-customer" />
        <Input value={form.customer_id} onChange={set("customer_id")} placeholder={t("warranty.customer_id")} data-testid="warranty-field-customer-id" />
        <Input value={form.equipment_id} onChange={set("equipment_id")} placeholder={t("warranty.equipment_id")} data-testid="warranty-field-equipment" />
        <Input value={form.job_id} onChange={set("job_id")} placeholder={t("warranty.job_id")} data-testid="warranty-field-job" />
        <label className="block">
          <span className="text-xs text-muted-foreground">{t("warranty.coverage_type")}</span>
          <select
            value={form.coverage_type}
            onChange={set("coverage_type")}
            className="mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm"
            data-testid="warranty-field-coverage-type"
          >
            {COVERAGE_TYPES.map((ct) => (
              <option key={ct} value={ct}>{t(`warranty.type_${ct}`)}</option>
            ))}
          </select>
        </label>
        <Input value={form.terms} onChange={set("terms")} placeholder={t("warranty.terms")} data-testid="warranty-field-terms" />
        <label className="block">
          <span className="text-xs text-muted-foreground">{t("warranty.start_date")}</span>
          <Input type="date" value={form.start_date} onChange={set("start_date")} className="mt-1" data-testid="warranty-field-start" />
        </label>
        <label className="block">
          <span className="text-xs text-muted-foreground">{t("warranty.end_date")}</span>
          <Input type="date" value={form.end_date} onChange={set("end_date")} className="mt-1" data-testid="warranty-field-end" />
        </label>
      </div>
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={save} disabled={busy} data-testid="warranty-save">
          {busy ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <ShieldCheck className="h-4 w-4 mr-1" />}
          {t("warranty.save")}
        </Button>
        <Button variant="ghost" size="sm" onClick={onCancel}>
          <X className="h-4 w-4 mr-1" />
          {t("warranty.cancel")}
        </Button>
      </div>
    </div>
  );
}

function WarrantyRow({ w, t }) {
  return (
    <div className="border rounded-lg p-4 space-y-2" data-testid={`warranty-row-${w.id}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-medium truncate">
            {w.customer_name || w.customer_id || t("warranty.unknown_customer")}
          </div>
          <div className="text-sm text-muted-foreground truncate">
            {t(`warranty.type_${w.coverage_type}`)}
            {w.equipment_id ? ` · ${t("warranty.equipment_id")}: ${w.equipment_id}` : ""}
            {w.job_id ? ` · ${t("warranty.job_id")}: ${w.job_id}` : ""}
          </div>
        </div>
        <Badge variant={statusBadgeVariant(w.status)} data-testid="warranty-status">
          {w.status === "active" ? (
            <CheckCircle2 className="h-3 w-3 mr-1" />
          ) : w.status === "upcoming" ? (
            <CalendarClock className="h-3 w-3 mr-1" />
          ) : null}
          {t(`warranty.status_${w.status}`)}
        </Badge>
      </div>
      <div className="text-xs text-muted-foreground">
        {w.start_date} → {w.end_date}
        {w.status === "active" && w.days_remaining != null && (
          <span className="ml-2 font-medium text-foreground">
            {t("warranty.expires_in_days", { n: w.days_remaining })}
          </span>
        )}
      </div>
      {w.terms && <div className="text-sm text-muted-foreground">{w.terms}</div>}
    </div>
  );
}

function CallbackRow({ cb, onResolved, t }) {
  const [busy, setBusy] = useState(false);
  const [resolving, setResolving] = useState(false);
  const [note, setNote] = useState("");

  const resolve = async () => {
    if (!note.trim()) {
      toast.error(t("warranty.resolve_note_required"));
      return;
    }
    setBusy(true);
    try {
      await api.post(`${BASE}/callbacks/${encodeURIComponent(cb.id)}/resolve`, {
        note: note.trim(),
      });
      toast.success(t("warranty.resolved_toast"));
      onResolved(cb.id);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="border rounded-lg p-4 space-y-3" data-testid={`callback-row-${cb.id}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-medium truncate">
            {cb.customer_name || t("warranty.unknown_customer")}
            {cb.equipment_label ? ` · ${cb.equipment_label}` : ""}
          </div>
          <div className="text-sm text-muted-foreground truncate">{cb.description}</div>
        </div>
        <Badge variant={callbackBadgeVariant(cb.status)} data-testid="callback-status">
          {cb.status === "escalated" && <AlertTriangle className="h-3 w-3 mr-1" />}
          {t(`warranty.callback_${cb.status}`)}
        </Badge>
      </div>
      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1">
          <RotateCcw className="h-3 w-3" />
          {t("warranty.visits_in_window", { n: cb.visits_in_window })}
        </span>
        {cb.tech && <span>{cb.tech}</span>}
        {cb.previous_visit_date && <span>{t("warranty.previous_visit", { d: cb.previous_visit_date.slice(0, 10) })}</span>}
      </div>
      {cb.status === "resolved" ? (
        <div className="text-sm text-muted-foreground">
          <CheckCircle2 className="h-3 w-3 inline mr-1" />
          {cb.resolution_note}
          {cb.resolved_by ? ` — ${cb.resolved_by}` : ""}
        </div>
      ) : !resolving ? (
        <Button variant="outline" size="sm" onClick={() => setResolving(true)} data-testid={`callback-resolve-${cb.id}`}>
          {t("warranty.resolve")}
        </Button>
      ) : (
        <div className="flex items-center gap-2">
          <Input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder={t("warranty.resolve_note_placeholder")}
            className="flex-1"
            data-testid={`callback-note-${cb.id}`}
          />
          <Button size="sm" onClick={resolve} disabled={busy}>{t("warranty.save")}</Button>
          <Button variant="ghost" size="sm" onClick={() => setResolving(false)}>{t("warranty.cancel")}</Button>
        </div>
      )}
    </div>
  );
}

export default function Warranty() {
  const { t } = useTranslation();
  const [tab, setTab] = useState("warranties");
  const [warranties, setWarranties] = useState([]);
  const [callbacks, setCallbacks] = useState([]);
  const [counts, setCounts] = useState({ open: 0, escalated: 0 });
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [fCustomer, setFCustomer] = useState("");
  const [fEquipment, setFEquipment] = useState("");
  const [fStatus, setFStatus] = useState("all");

  const load = async () => {
    setLoading(true);
    try {
      const [wRes, cRes, sRes] = await Promise.all([
        api.get(`${BASE}/warranties`, {
          params: {
            customer: fCustomer.trim(),
            equipment: fEquipment.trim(),
            status: fStatus,
          },
        }),
        api.get(`${BASE}/callbacks`),
        api.get(`${BASE}/stats`),
      ]);
      setWarranties(wRes.data.warranties || []);
      setCallbacks(cRes.data.callbacks || []);
      setCounts({ open: cRes.data.open || 0, escalated: cRes.data.escalated || 0 });
      setStats(sRes.data);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const applyFilters = () => load();

  const markResolved = (id) =>
    setCallbacks((list) =>
      list.map((c) => (c.id === id ? { ...c, status: "resolved" } : c))
    );

  return (
    <div className="space-y-6" data-testid="warranty-page">
      <PageHeader
        eyebrow={t("warranty.eyebrow")}
        title={t("warranty.title")}
        description={t("warranty.sub")}
      />

      {stats && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <div className="surface p-4" data-testid="warranty-stat-rate">
            <div className="text-xs text-muted-foreground">{t("warranty.stat_callback_rate")}</div>
            <div className="text-2xl font-semibold">{stats.callback_rate_pct}%</div>
          </div>
          <div className="surface p-4" data-testid="warranty-stat-active">
            <div className="text-xs text-muted-foreground">{t("warranty.stat_active_warranties")}</div>
            <div className="text-2xl font-semibold">{stats.warranties?.active || 0}</div>
          </div>
          <div className="surface p-4" data-testid="warranty-stat-escalated">
            <div className="text-xs text-muted-foreground">{t("warranty.stat_escalated")}</div>
            <div className="text-2xl font-semibold text-destructive">{stats.escalated_open || 0}</div>
          </div>
          <div className="surface p-4" data-testid="warranty-stat-expiring">
            <div className="text-xs text-muted-foreground">{t("warranty.stat_expiring")}</div>
            <div className="text-2xl font-semibold">{stats.warranties?.expiring_30d || 0}</div>
          </div>
        </div>
      )}

      <div className="flex items-center gap-2">
        <Button
          variant={tab === "warranties" ? "default" : "outline"}
          size="sm"
          onClick={() => setTab("warranties")}
          data-testid="warranty-tab-warranties"
        >
          <ShieldCheck className="h-4 w-4 mr-1" />
          {t("warranty.tab_warranties")}
        </Button>
        <Button
          variant={tab === "callbacks" ? "default" : "outline"}
          size="sm"
          onClick={() => setTab("callbacks")}
          data-testid="warranty-tab-callbacks"
        >
          <RotateCcw className="h-4 w-4 mr-1" />
          {t("warranty.tab_callbacks")}
          {(counts.open + counts.escalated) > 0 && (
            <Badge variant="secondary" className="ml-2">{counts.open + counts.escalated}</Badge>
          )}
        </Button>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> {t("warranty.loading")}
        </div>
      ) : tab === "warranties" ? (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            {!showForm ? (
              <Button size="sm" onClick={() => setShowForm(true)} data-testid="warranty-add">
                <Plus className="h-4 w-4 mr-1" />
                {t("warranty.add_warranty")}
              </Button>
            ) : null}
            <Input
              value={fCustomer}
              onChange={(e) => setFCustomer(e.target.value)}
              placeholder={t("warranty.filter_customer")}
              className="w-44"
              data-testid="warranty-filter-customer"
            />
            <Input
              value={fEquipment}
              onChange={(e) => setFEquipment(e.target.value)}
              placeholder={t("warranty.filter_equipment")}
              className="w-44"
              data-testid="warranty-filter-equipment"
            />
            <select
              value={fStatus}
              onChange={(e) => setFStatus(e.target.value)}
              className="rounded-md border bg-background px-3 py-2 text-sm"
              data-testid="warranty-filter-status"
            >
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s === "all" ? t("warranty.filter_status") : t(`warranty.status_${s}`)}
                </option>
              ))}
            </select>
            <Button variant="outline" size="sm" onClick={applyFilters} data-testid="warranty-apply-filters">
              {t("warranty.apply")}
            </Button>
          </div>

          {showForm && (
            <WarrantyForm
              t={t}
              onSaved={() => { setShowForm(false); load(); }}
              onCancel={() => setShowForm(false)}
            />
          )}

          {warranties.length === 0 ? (
            <div className="surface p-8 text-center text-muted-foreground">
              {t("warranty.empty_warranties")}
            </div>
          ) : (
            <div className="space-y-3">
              {warranties.map((w) => (
                <WarrantyRow key={w.id} w={w} t={t} />
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-4">
          {(counts.open > 0 || counts.escalated > 0) && (
            <div className="surface p-4 flex items-center gap-2 text-sm">
              <AlertTriangle className="h-4 w-4 text-amber-500" />
              {t("warranty.open_count", { n: counts.open })}
              {counts.escalated > 0 && (
                <span className="font-medium text-destructive">
                  {t("warranty.escalated_count", { n: counts.escalated })}
                </span>
              )}
            </div>
          )}
          {callbacks.length === 0 ? (
            <div className="surface p-8 text-center text-muted-foreground">
              {t("warranty.empty_callbacks")}
            </div>
          ) : (
            <div className="space-y-3">
              {callbacks.map((cb) => (
                <CallbackRow key={cb.id} cb={cb} onResolved={markResolved} t={t} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
