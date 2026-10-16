import { useCallback, useEffect, useMemo, useState } from "react";
import { api, errMessage } from "@/lib/api";
import { fmtUSD } from "@/lib/money";
import { useTranslation } from "react-i18next";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import {
  Plus, RefreshCw, CalendarPlus, Wallet, FileText, BellRing, Loader2, X,
} from "lucide-react";

const BASE = "/tenants/maintenance";

const STATUS_TONE = {
  active: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  expiring: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  expired: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-300",
};

const EMPTY_PLAN = { name: "", visits_per_year: 2, price: "", billing_interval: "annual", trade: "" };
const EMPTY_ENROLL = { plan_id: "", customer_name: "", customer_phone: "", customer_email: "", start_date: "" };

function StatCard({ label, value, icon: Icon, hint }) {
  return (
    <div className="rounded-xl border bg-card p-5 shadow-sm">
      <div className="flex items-center justify-between">
        <div className="text-sm text-muted-foreground">{label}</div>
        {Icon && <Icon className="h-4 w-4 text-muted-foreground" />}
      </div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
      {hint && <div className="mt-1 text-xs text-muted-foreground">{hint}</div>}
    </div>
  );
}

function PlanModal({ onClose, onSaved, t }) {
  const [form, setForm] = useState(EMPTY_PLAN);
  const [busy, setBusy] = useState(false);
  const save = async () => {
    if (!form.name.trim()) return toast.error(t("maintAgr.plan_name_required"));
    setBusy(true);
    try {
      await api.post(`${BASE}/plans`, {
        ...form,
        name: form.name.trim(),
        price: Number(form.price) || 0,
        visits_per_year: Number(form.visits_per_year) || 1,
      });
      toast.success(t("maintAgr.plan_created_toast"));
      onSaved();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-xl bg-card p-6 shadow-xl max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold">{t("maintAgr.new_plan")}</h3>
          <Button variant="ghost" size="sm" onClick={onClose}><X className="h-4 w-4" /></Button>
        </div>
        <div className="space-y-3">
          <Input placeholder={t("maintAgr.plan_name_ph")} value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <div className="grid grid-cols-2 gap-3">
            <Input type="number" min="1" max="12" placeholder={t("maintAgr.visits_per_year_ph")}
              value={form.visits_per_year}
              onChange={(e) => setForm({ ...form, visits_per_year: e.target.value })} />
            <Input type="number" min="0" step="0.01" placeholder={t("maintAgr.price_ph")}
              value={form.price}
              onChange={(e) => setForm({ ...form, price: e.target.value })} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <select className="rounded-md border bg-card px-3 py-2 text-sm"
              value={form.billing_interval}
              onChange={(e) => setForm({ ...form, billing_interval: e.target.value })}>
              <option value="monthly">{t("maintAgr.billing_monthly")}</option>
              <option value="quarterly">{t("maintAgr.billing_quarterly")}</option>
              <option value="annual">{t("maintAgr.billing_annual")}</option>
            </select>
            <Input placeholder={t("maintAgr.trade_ph")} value={form.trade}
              onChange={(e) => setForm({ ...form, trade: e.target.value })} />
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={onClose}>{t("common.cancel")}</Button>
            <Button onClick={save} disabled={busy}>
              {busy && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              {t("common.save")}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

function EnrollModal({ plans, onClose, onSaved, t }) {
  const [form, setForm] = useState({ ...EMPTY_ENROLL, plan_id: plans[0]?.id || "" });
  const [busy, setBusy] = useState(false);
  const save = async () => {
    if (!form.plan_id || !form.customer_name.trim() || !form.start_date) {
      return toast.error(t("maintAgr.enroll_required_fields"));
    }
    setBusy(true);
    try {
      await api.post(`${BASE}/agreements`, {
        plan_id: form.plan_id,
        customer: {
          name: form.customer_name.trim(),
          phone: form.customer_phone.trim(),
          email: form.customer_email.trim(),
        },
        start_date: form.start_date,
      });
      toast.success(t("maintAgr.enrolled_toast"));
      onSaved();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-xl bg-card p-6 shadow-xl max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold">{t("maintAgr.enroll")}</h3>
          <Button variant="ghost" size="sm" onClick={onClose}><X className="h-4 w-4" /></Button>
        </div>
        <div className="space-y-3">
          <select className="w-full rounded-md border bg-card px-3 py-2 text-sm"
            value={form.plan_id}
            onChange={(e) => setForm({ ...form, plan_id: e.target.value })}>
            {plans.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} — {fmtUSD(p.price)}/{p.billing_interval} · {p.visits_per_year}/yr
              </option>
            ))}
          </select>
          <Input placeholder={t("maintAgr.customer_name_ph")} value={form.customer_name}
            onChange={(e) => setForm({ ...form, customer_name: e.target.value })} />
          <div className="grid grid-cols-2 gap-3">
            <Input placeholder={t("maintAgr.phone_ph")} value={form.customer_phone}
              onChange={(e) => setForm({ ...form, customer_phone: e.target.value })} />
            <Input placeholder={t("maintAgr.email_ph")} value={form.customer_email}
              onChange={(e) => setForm({ ...form, customer_email: e.target.value })} />
          </div>
          <Input type="date" value={form.start_date}
            onChange={(e) => setForm({ ...form, start_date: e.target.value })} />
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={onClose}>{t("common.cancel")}</Button>
            <Button onClick={save} disabled={busy}>
              {busy && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              {t("maintAgr.enroll")}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

function AgreementRow({ agr, onAction, busyId, t }) {
  const busy = busyId === agr.id;
  return (
    <div className="border rounded-lg p-4 space-y-3" data-testid={`maint-agr-${agr.id}`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="font-medium">{agr.customer?.name || t("maintAgr.unknown_customer")}</div>
          <div className="text-sm text-muted-foreground">
            {agr.plan_name} · {fmtUSD(agr.price)}/{agr.billing_interval} · {agr.visits_per_year}/yr
          </div>
          <div className="text-xs text-muted-foreground mt-1">
            {agr.start_date?.slice(0, 10)} → {agr.end_date?.slice(0, 10)}
            {agr.renewal_count > 0 && ` · ${t("maintAgr.renewed_n", { n: agr.renewal_count })}`}
          </div>
        </div>
        <Badge className={STATUS_TONE[agr.status] || ""}>{t(`maintAgr.status_${agr.status}`)}</Badge>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" size="sm" disabled={busy || agr.status === "expired"}
          onClick={() => onAction("visit", agr)}>
          <CalendarPlus className="h-3.5 w-3.5 mr-1" />
          {t("maintAgr.schedule_visit")}
        </Button>
        <Button variant="outline" size="sm" disabled={busy}
          onClick={() => onAction("renew", agr)}>
          {busy ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5 mr-1" />}
          {t("maintAgr.renew")}
        </Button>
      </div>
    </div>
  );
}

export default function MaintenanceAutopilot() {
  const { t } = useTranslation();
  const [plans, setPlans] = useState([]);
  const [agreements, setAgreements] = useState([]);
  const [revenue, setRevenue] = useState(null);
  const [tab, setTab] = useState("all"); // all | active | expiring | expired
  const [planModal, setPlanModal] = useState(false);
  const [enrollModal, setEnrollModal] = useState(false);
  const [busyId, setBusyId] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [p, a, r] = await Promise.all([
        api.get(`${BASE}/plans`),
        api.get(`${BASE}/agreements`),
        api.get(`${BASE}/revenue`),
      ]);
      setPlans(p.data.plans || []);
      setAgreements(a.data.agreements || []);
      setRevenue(r.data);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = useMemo(
    () => (tab === "all" ? agreements : agreements.filter((a) => a.status === tab)),
    [agreements, tab]
  );

  const counts = useMemo(() => {
    const c = { all: agreements.length, active: 0, expiring: 0, expired: 0 };
    for (const a of agreements) if (c[a.status] !== undefined) c[a.status] += 1;
    return c;
  }, [agreements]);

  const action = async (kind, agr) => {
    setBusyId(agr.id);
    try {
      if (kind === "renew") {
        const res = await api.post(`${BASE}/agreements/${agr.id}/renew`);
        toast.success(t("maintAgr.renewed_toast", { date: res.data.agreement.end_date?.slice(0, 10) }));
      } else {
        const res = await api.post(`${BASE}/agreements/${agr.id}/schedule-visit`);
        const v = res.data.visit;
        toast.success(
          res.data.created
            ? t("maintAgr.visit_scheduled_toast", { n: v.visit_number, date: v.due_date })
            : t("maintAgr.visit_already_scheduled_toast", { date: v.due_date })
        );
      }
      await load();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="space-y-6" data-testid="maint-autopilot-page">
      <PageHeader
        title={t("maintAgr.title")}
        subtitle={t("maintAgr.subtitle")}
        actions={
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => setPlanModal(true)}>
              <FileText className="h-4 w-4 mr-2" /> {t("maintAgr.new_plan")}
            </Button>
            <Button onClick={() => setEnrollModal(true)} disabled={plans.length === 0}>
              <Plus className="h-4 w-4 mr-2" /> {t("maintAgr.enroll")}
            </Button>
          </div>
        }
      />

      {/* revenue summary */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label={t("maintAgr.revenue_mrr")} value={revenue ? fmtUSD(revenue.monthly_recurring_value) : "—"}
          icon={Wallet} hint={revenue ? `${revenue.active_agreements} ${t("maintAgr.active_agreements")}` : undefined} />
        <StatCard label={t("maintAgr.revenue_arr")} value={revenue ? fmtUSD(revenue.annual_recurring_value) : "—"}
          icon={Wallet} />
        <StatCard label={t("maintAgr.active_agreements")} value={revenue?.active_agreements ?? "—"}
          icon={FileText} />
        <StatCard label={t("maintAgr.renewal_queue")}
          value={revenue ? fmtUSD(revenue.renewal_pipeline_monthly_value) : "—"}
          icon={BellRing}
          hint={revenue ? `${revenue.expiring_agreements} ${t("maintAgr.expiring_soon")}` : undefined} />
      </div>

      {/* renewal queue */}
      {revenue && revenue.renewal_pipeline?.length > 0 && (
        <div className="rounded-xl border bg-card p-5 shadow-sm">
          <h3 className="font-semibold mb-3 flex items-center gap-2">
            <BellRing className="h-4 w-4" /> {t("maintAgr.renewal_queue")}
          </h3>
          <div className="space-y-2">
            {revenue.renewal_pipeline.map((r) => (
              <div key={r.agreement_id} className="flex items-center justify-between gap-3 text-sm border-b last:border-0 py-2">
                <div>
                  <span className="font-medium">{r.customer_name}</span>
                  <span className="text-muted-foreground"> · {t("maintAgr.ends_date", { date: r.end_date?.slice(0, 10) })}</span>
                </div>
                <div className="font-medium">{fmtUSD(r.monthly_value)}<span className="text-muted-foreground font-normal">/mo</span></div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* agreements pipeline */}
      <div className="rounded-xl border bg-card p-5 shadow-sm">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-semibold">{t("maintAgr.agreements")}</h3>
          <div className="flex gap-1">
            {["all", "active", "expiring", "expired"].map((k) => (
              <Button key={k} variant={tab === k ? "default" : "ghost"} size="sm"
                onClick={() => setTab(k)}>
                {t(`maintAgr.tab_${k}`)} ({counts[k]})
              </Button>
            ))}
          </div>
        </div>
        {loading ? (
          <div className="flex justify-center py-8"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>
        ) : filtered.length === 0 ? (
          <div className="rounded-xl border bg-muted/40 p-8 text-center text-muted-foreground">
            {t("maintAgr.no_agreements")}
          </div>
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {filtered.map((a) => (
              <AgreementRow key={a.id} agr={a} onAction={action} busyId={busyId} t={t} />
            ))}
          </div>
        )}
      </div>

      {/* plan templates */}
      <div className="rounded-xl border bg-card p-5 shadow-sm">
        <h3 className="font-semibold mb-3">{t("maintAgr.plans")}</h3>
        {plans.length === 0 ? (
          <div className="rounded-xl border bg-muted/40 p-8 text-center text-muted-foreground">
            {t("maintAgr.no_plans")}
          </div>
        ) : (
          <div className="grid gap-3 md:grid-cols-3">
            {plans.map((p) => (
              <div key={p.id} className="border rounded-lg p-4">
                <div className="font-medium">{p.name}</div>
                <div className="text-sm text-muted-foreground mt-1">
                  {fmtUSD(p.price)}/{p.billing_interval} · {p.visits_per_year}/yr{p.trade ? ` · ${p.trade}` : ""}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {planModal && <PlanModal onClose={() => setPlanModal(false)} onSaved={() => { setPlanModal(false); load(); }} t={t} />}
      {enrollModal && <EnrollModal plans={plans} onClose={() => setEnrollModal(false)} onSaved={() => { setEnrollModal(false); load(); }} t={t} />}
    </div>
  );
}
