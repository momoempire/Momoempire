import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errMessage } from "@/lib/api";
import { fmtUSD } from "@/lib/money";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Gauge, PieChart, Settings2, Search } from "lucide-react";

/**
 * Real-time per-job profitability (#11).
 *
 * EN/ES copy lives inline here so the page works before the
 * `profitability.*` keys from INTEGRATION_job_profitability.md are merged
 * into frontend/src/i18n/locales/{en,es}.json. The canonical i18n keys are
 * looked up first via t(); the inline table is the fallback.
 */
const FALLBACK = {
  en: {
    title: "Job profitability",
    subtitle: "Live margin on every job, plus period totals by tech and job type.",
    jobCard: "Per-job margin",
    jobIdLabel: "Job ID",
    jobIdPh: "paste an appointment/job id",
    load: "Load job",
    noJob: "Load a job to see its live margin breakdown.",
    period: "Period dashboard",
    from: "From",
    to: "To",
    refresh: "Refresh",
    revenue: "Revenue",
    labor: "Labor",
    parts: "Parts",
    overhead: "Overhead",
    totalCost: "Total cost",
    grossProfit: "Gross profit",
    margin: "Margin",
    hours: "Hours",
    rate: "Rate/hr",
    jobs: "jobs",
    byTech: "By technician",
    byJobType: "By job type",
    settings: "Profitability settings",
    defaultRate: "Default hourly rate",
    overheadRate: "Overhead rate",
    overheadMethod: "Overhead method",
    perHour: "Per hour ($/hr)",
    pctRevenue: "% of revenue",
    save: "Save settings",
    saved: "Settings saved",
    ownerOnly: "Only owners and admins can change settings.",
    techRates: "Per-tech / per-role rates (optional, JSON)",
    err: "Could not load profitability data",
  },
  es: {
    title: "Rentabilidad por trabajo",
    subtitle: "Margen en vivo de cada trabajo y totales del período por técnico y tipo de trabajo.",
    jobCard: "Margen por trabajo",
    jobIdLabel: "ID del trabajo",
    jobIdPh: "pega el id de la cita/trabajo",
    load: "Cargar trabajo",
    noJob: "Carga un trabajo para ver su desglose de margen en vivo.",
    period: "Panel del período",
    from: "Desde",
    to: "Hasta",
    refresh: "Actualizar",
    revenue: "Ingresos",
    labor: "Mano de obra",
    parts: "Repuestos",
    overhead: "Gastos generales",
    totalCost: "Costo total",
    grossProfit: "Utilidad bruta",
    margin: "Margen",
    hours: "Horas",
    rate: "Tarifa/h",
    jobs: "trabajos",
    byTech: "Por técnico",
    byJobType: "Por tipo de trabajo",
    settings: "Ajustes de rentabilidad",
    defaultRate: "Tarifa horaria predeterminada",
    overheadRate: "Tasa de gastos generales",
    overheadMethod: "Método de gastos generales",
    perHour: "Por hora ($/h)",
    pctRevenue: "% de los ingresos",
    save: "Guardar ajustes",
    saved: "Ajustes guardados",
    ownerOnly: "Solo propietarios y administradores pueden cambiar los ajustes.",
    techRates: "Tarifas por técnico/rol (opcional, JSON)",
    err: "No se pudieron cargar los datos de rentabilidad",
  },
};

const num = (v) => Number(v) || 0;

function Stat({ label, value, accent }) {
  return (
    <div className="rounded-xl border p-4" data-testid={`stat-${label}`}>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`mt-1 text-2xl font-semibold ${accent || ""}`}>{value}</div>
    </div>
  );
}

function GroupTable({ rows, caption }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="text-left text-xs text-muted-foreground border-b">
            <th className="py-2 pr-4">{caption}</th>
            <th className="py-2 pr-4 text-right">Revenue</th>
            <th className="py-2 pr-4 text-right">Cost</th>
            <th className="py-2 pr-4 text-right">Profit</th>
            <th className="py-2 text-right">Margin</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([name, g]) => (
            <tr key={name} className="border-b last:border-0">
              <td className="py-2 pr-4 font-medium">{name} <span className="text-xs text-muted-foreground">({g.jobs})</span></td>
              <td className="py-2 pr-4 text-right font-mono">{fmtUSD(g.revenue)}</td>
              <td className="py-2 pr-4 text-right font-mono">{fmtUSD(g.cost)}</td>
              <td className="py-2 pr-4 text-right font-mono">{fmtUSD(g.profit)}</td>
              <td className="py-2 text-right font-mono">{num(g.margin_pct).toFixed(2)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Profitability() {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language || "en").startsWith("es") ? "es" : "en";
  const f = FALLBACK[lang];
  const L = (key) => t(`profitability.${key}`, f[key]);

  const today = () => new Date().toISOString().slice(0, 10);
  const monthAgo = () => {
    const d = new Date();
    d.setDate(d.getDate() - 30);
    return d.toISOString().slice(0, 10);
  };

  const [jobId, setJobId] = useState("");
  const [job, setJob] = useState(null);
  const [from, setFrom] = useState(monthAgo());
  const [to, setTo] = useState(today());
  const [dash, setDash] = useState(null);
  const [settings, setSettings] = useState(null);
  const [settingsForm, setSettingsForm] = useState({});
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const loadDashboard = () => {
    setError("");
    api.get("/tenants/profitability/dashboard", { params: { from, to } })
      .then((r) => setDash(r.data))
      .catch((e) => setError(errMessage(e) || L("err")));
  };

  const loadJob = () => {
    if (!jobId.trim()) return;
    setError("");
    api.get(`/tenants/profitability/jobs/${encodeURIComponent(jobId.trim())}`)
      .then((r) => setJob(r.data))
      .catch((e) => setError(errMessage(e) || L("err")));
  };

  const loadSettings = () => {
    api.get("/tenants/profitability/settings")
      .then((r) => {
        setSettings(r.data);
        setSettingsForm({
          default_hourly_rate: r.data?.default_hourly_rate ?? "",
          overhead_rate: r.data?.overhead_rate ?? "",
          overhead_method: r.data?.overhead_method ?? "per-hour",
          hourly_rates: JSON.stringify(r.data?.hourly_rates ?? {}, null, 2),
        });
      })
      .catch(() => {});
  };

  const saveSettings = async () => {
    setError("");
    setNotice("");
    let rates = {};
    try {
      rates = settingsForm.hourly_rates?.trim() ? JSON.parse(settingsForm.hourly_rates) : {};
    } catch {
      setError(L("techRates") + " — invalid JSON");
      return;
    }
    const numOrOmit = (v) => (v === "" || v == null || Number.isNaN(Number(v)) ? undefined : Number(v));
    try {
      const r = await api.put("/tenants/profitability/settings", {
        default_hourly_rate: numOrOmit(settingsForm.default_hourly_rate),
        overhead_rate: numOrOmit(settingsForm.overhead_rate),
        overhead_method: settingsForm.overhead_method,
        hourly_rates: rates,
      });
      setSettings(r.data);
      setNotice(L("saved"));
      loadDashboard();
    } catch (e) {
      setError(errMessage(e) || L("err"));
    }
  };

  useEffect(() => {
    loadDashboard();
    loadSettings();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);


  return (
    <div data-testid="profitability-page" className="space-y-6">
      <PageHeader eyebrow={L("subtitle")} title={L("title")} icon={<Gauge className="h-5 w-5" />} />
      {error && <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900">{error}</div>}
      {notice && <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">{notice}</div>}

      {/* per-job margin card */}
      <Card className="p-6" data-testid="job-margin-card">
        <h2 className="text-lg font-semibold mb-4">{L("jobCard")}</h2>
        <div className="flex flex-wrap items-end gap-3 mb-4">
          <div className="flex-1 min-w-56">
            <Label htmlFor="profit-job-id">{L("jobIdLabel")}</Label>
            <Input id="profit-job-id" value={jobId} onChange={(e) => setJobId(e.target.value)}
              placeholder={L("jobIdPh")} onKeyDown={(e) => e.key === "Enter" && loadJob()} />
          </div>
          <Button onClick={loadJob}><Search className="h-4 w-4 mr-2" />{L("load")}</Button>
        </div>
        {job ? (
          <div>
            <div className="mb-4 text-sm">
              <span className="font-semibold">{job.customer_name}</span>
              <span className="text-muted-foreground"> · {job.service_name} · {job.tech?.name || "—"} · {num(job.hours).toFixed(2)} {L("hours")} @ {fmtUSD(job.hourly_rate)}/{L("rate")}</span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
              <Stat label={L("revenue")} value={fmtUSD(job.revenue)} />
              <Stat label={L("labor")} value={fmtUSD(job.labor_cost)} />
              <Stat label={L("parts")} value={fmtUSD(job.parts_cost)} />
              <Stat label={L("overhead")} value={fmtUSD(job.overhead_allocated)} />
              <Stat label={L("grossProfit")} value={fmtUSD(job.gross_profit)}
                accent={num(job.gross_profit) >= 0 ? "text-emerald-600" : "text-rose-600"} />
              <Stat label={L("margin")} value={`${num(job.margin_pct).toFixed(2)}%`}
                accent={num(job.margin_pct) >= 0 ? "text-emerald-600" : "text-rose-600"} />
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">{L("noJob")}</p>
        )}
      </Card>

      {/* period dashboard */}
      <Card className="p-6" data-testid="period-dashboard">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
          <h2 className="text-lg font-semibold flex items-center gap-2"><PieChart className="h-5 w-5" />{L("period")}</h2>
          <div className="flex items-end gap-2">
            <div><Label htmlFor="profit-from">{L("from")}</Label><Input id="profit-from" type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></div>
            <div><Label htmlFor="profit-to">{L("to")}</Label><Input id="profit-to" type="date" value={to} onChange={(e) => setTo(e.target.value)} /></div>
            <Button variant="outline" onClick={loadDashboard}>{L("refresh")}</Button>
          </div>
        </div>
        {dash && (
          <div className="space-y-6">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <Stat label={L("revenue")} value={fmtUSD(dash.revenue)} />
              <Stat label={L("totalCost")} value={fmtUSD(dash.cost)} />
              <Stat label={L("grossProfit")} value={fmtUSD(dash.profit)}
                accent={num(dash.profit) >= 0 ? "text-emerald-600" : "text-rose-600"} />
              <Stat label={L("margin")} value={`${num(dash.margin_pct).toFixed(2)}%`} />
            </div>
            <div className="grid md:grid-cols-2 gap-6">
              <div>
                <h3 className="font-medium mb-2">{L("byTech")} ({dash.jobs} {L("jobs")})</h3>
                <GroupTable rows={Object.entries(dash.by_tech || {})} caption={L("byTech")} />
              </div>
              <div>
                <h3 className="font-medium mb-2">{L("byJobType")}</h3>
                <GroupTable rows={Object.entries(dash.by_job_type || {})} caption={L("byJobType")} />
              </div>
            </div>
          </div>
        )}
      </Card>

      {/* settings (owner/admin only; server enforces) */}
      <Card className="p-6" data-testid="profitability-settings">
        <h2 className="text-lg font-semibold mb-1 flex items-center gap-2"><Settings2 className="h-5 w-5" />{L("settings")}</h2>
        <p className="text-xs text-muted-foreground mb-4">{L("ownerOnly")}</p>
        <div className="grid md:grid-cols-3 gap-4">
          <div>
            <Label htmlFor="profit-default-rate">{L("defaultRate")}</Label>
            <Input id="profit-default-rate" type="number" min="0" step="0.01"
              value={settingsForm.default_hourly_rate ?? ""}
              onChange={(e) => setSettingsForm({ ...settingsForm, default_hourly_rate: e.target.value })} />
          </div>
          <div>
            <Label htmlFor="profit-overhead-rate">{L("overheadRate")}</Label>
            <Input id="profit-overhead-rate" type="number" min="0" step="0.01"
              value={settingsForm.overhead_rate ?? ""}
              onChange={(e) => setSettingsForm({ ...settingsForm, overhead_rate: e.target.value })} />
          </div>
          <div>
            <Label htmlFor="profit-overhead-method">{L("overheadMethod")}</Label>
            <select id="profit-overhead-method" className="w-full rounded-md border px-3 py-2 text-sm bg-background"
              value={settingsForm.overhead_method ?? "per-hour"}
              onChange={(e) => setSettingsForm({ ...settingsForm, overhead_method: e.target.value })}>
              <option value="per-hour">{L("perHour")}</option>
              <option value="pct-of-revenue">{L("pctRevenue")}</option>
            </select>
          </div>
        </div>
        <div className="mt-4">
          <Label htmlFor="profit-tech-rates">{L("techRates")}</Label>
          <textarea id="profit-tech-rates" rows={4}
            className="w-full rounded-md border px-3 py-2 text-sm font-mono bg-background"
            value={settingsForm.hourly_rates ?? ""}
            onChange={(e) => setSettingsForm({ ...settingsForm, hourly_rates: e.target.value })} />
        </div>
        <Button className="mt-4" onClick={saveSettings}>{L("save")}</Button>
      </Card>
    </div>
  );
}
