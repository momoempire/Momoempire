import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errMessage } from "@/lib/api";
import { fmtUSD } from "@/lib/money";
import { useAuth } from "@/context/AuthContext";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import {
  AlertTriangle, Coins, Info, Lightbulb, PiggyBank, RefreshCw, TrendingDown, Wallet,
} from "lucide-react";

const BASE = "/tenants/cash-flow";

const card = "glass-crystal rounded-2xl p-5";

function CashChart({ weeks }) {
  // Simple SVG: per-week net bars + running-balance line. No new deps.
  const W = 760, H = 260, PAD = 36;
  const balances = weeks.map((w) => w.running_balance);
  const nets = weeks.map((w) => w.net);
  const maxAbs = Math.max(1, ...nets.map(Math.abs), ...balances.map(Math.abs));
  const x = (i) => PAD + (i * (W - PAD * 2)) / Math.max(1, weeks.length - 1);
  const y = (v) => H / 2 - (v / maxAbs) * (H / 2 - PAD / 2);
  const barW = Math.max(8, ((W - PAD * 2) / weeks.length) * 0.45);
  const line = balances.map((b, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(b).toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" role="img" aria-label="12-week cash forecast">
      <line x1={PAD} x2={W - PAD} y1={H / 2} y2={H / 2} stroke="currentColor" strokeOpacity={0.25} />
      {weeks.map((w, i) => {
        const top = w.net >= 0 ? y(w.net) : H / 2;
        const h = Math.abs(y(w.net) - H / 2);
        return (
          <g key={w.week}>
            <rect
              x={x(i) - barW / 2} y={top} width={barW} height={Math.max(2, h)} rx={3}
              fill={w.net >= 0 ? "#34d399" : "#f87171"} fillOpacity={0.75}
            >
              <title>{`W${w.week} ${w.week_start}: ${fmtUSD(w.net)} net, ${fmtUSD(w.running_balance)} balance`}</title>
            </rect>
            {i % 3 === 0 && (
              <text x={x(i)} y={H - 8} textAnchor="middle" fontSize={10} fill="currentColor" opacity={0.6}>
                {w.week_start.slice(5)}
              </text>
            )}
          </g>
        );
      })}
      <path d={line} fill="none" stroke="#60a5fa" strokeWidth={2.5} strokeLinejoin="round" />
      {balances.map((b, i) => (
        <circle key={i} cx={x(i)} cy={y(b)} r={3} fill="#60a5fa">
          <title>{`W${i + 1} balance ${fmtUSD(b)}`}</title>
        </circle>
      ))}
    </svg>
  );
}

export default function CashFlow() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [forecast, setForecast] = useState(null);
  const [warnings, setWarnings] = useState([]);
  const [levers, setLevers] = useState([]);
  const [assumptions, setAssumptions] = useState(null);
  const [loading, setLoading] = useState(true);
  const [balanceInput, setBalanceInput] = useState("");
  const [safetyInput, setSafetyInput] = useState("");
  const [saving, setSaving] = useState(false);

  const isOwner = user?.role === "owner" || user?.role === "admin";

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [f, w, l, a] = await Promise.all([
        api.get(`${BASE}/forecast`),
        api.get(`${BASE}/warnings`),
        api.get(`${BASE}/levers`),
        api.get(`${BASE}/assumptions`),
      ]);
      setForecast(f.data);
      setWarnings(w.data.warnings || []);
      setLevers(l.data.levers || []);
      setAssumptions(a.data.assumptions || null);
      setBalanceInput(String(f.data.starting_balance ?? ""));
      setSafetyInput(String(f.data.safety_threshold ?? ""));
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const totals = useMemo(() => {
    if (!forecast) return null;
    const weeks = forecast.weeks || [];
    const inT = weeks.reduce((s, w) => s + w.expected_in, 0);
    const outT = weeks.reduce((s, w) => s + w.expected_out, 0);
    const minBal = weeks.length ? Math.min(...weeks.map((w) => w.running_balance)) : 0;
    return { inT, outT, minBal };
  }, [forecast]);

  const saveBalance = async () => {
    const balance = Number(balanceInput);
    if (!(balance >= 0)) return toast.error(t("cashFlow.invalid_balance"));
    const safety = safetyInput === "" ? undefined : Number(safetyInput);
    if (safety !== undefined && !(safety >= 0)) return toast.error(t("cashFlow.invalid_threshold"));
    setSaving(true);
    try {
      await api.post(`${BASE}/starting-balance`, {
        balance,
        ...(safety !== undefined ? { safety_threshold: safety } : {}),
      });
      toast.success(t("cashFlow.balance_saved"));
      await load();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="p-6 text-sm opacity-70">{t("cashFlow.loading")}</div>;
  }

  return (
    <div className="space-y-5 p-1">
      <PageHeader
        title={t("cashFlow.title")}
        subtitle={t("cashFlow.subtitle")}
        actions={
          <Button variant="outline" size="sm" onClick={load}>
            <RefreshCw className="h-3.5 w-3.5 mr-1" /> {t("cashFlow.refresh")}
          </Button>
        }
      />

      {forecast?.demo && (
        <div className={`${card} flex items-start gap-3 border-amber-400/40`}>
          <Info className="h-5 w-5 text-amber-400 shrink-0 mt-0.5" />
          <p className="text-sm">{t("cashFlow.demo_notice")}</p>
        </div>
      )}

      {warnings.length > 0 && (
        <div className={`${card} border-red-400/50`} role="alert">
          <div className="flex items-center gap-2 mb-2">
            <AlertTriangle className="h-5 w-5 text-red-400" />
            <h3 className="font-semibold">{t("cashFlow.warnings_title", { n: warnings.length })}</h3>
          </div>
          <div className="flex flex-wrap gap-2">
            {warnings.map((w) => (
              <Badge key={w.week} variant="destructive">
                {t("cashFlow.warning_week", { week: w.week, date: w.week_start })} ·{" "}
                {fmtUSD(w.projected_balance)} ({t("cashFlow.shortfall")} {fmtUSD(w.shortfall)})
              </Badge>
            ))}
          </div>
        </div>
      )}

      {totals && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {[
            { icon: Wallet, label: t("cashFlow.starting_balance"), value: fmtUSD(forecast.starting_balance) },
            { icon: TrendingDown, label: t("cashFlow.expected_in"), value: fmtUSD(totals.inT) },
            { icon: Coins, label: t("cashFlow.expected_out"), value: fmtUSD(totals.outT) },
            { icon: PiggyBank, label: t("cashFlow.lowest_balance"), value: fmtUSD(totals.minBal) },
          ].map(({ icon: Icon, label, value }) => (
            <div key={label} className={`${card} flex items-center gap-3`}>
              <Icon className="h-6 w-6 opacity-70 shrink-0" />
              <div>
                <div className="text-xs opacity-70">{label}</div>
                <div className="text-xl font-bold">{value}</div>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className={card}>
        <h3 className="font-semibold mb-3">{t("cashFlow.chart_title")}</h3>
        {forecast?.weeks?.length ? (
          <CashChart weeks={forecast.weeks} />
        ) : (
          <p className="text-sm opacity-70">{t("cashFlow.no_data")}</p>
        )}
        <div className="flex gap-5 mt-2 text-xs opacity-70">
          <span className="flex items-center gap-1.5">
            <span className="inline-block w-3 h-3 rounded bg-emerald-400/80" /> {t("cashFlow.legend_net")}
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block w-3 h-3 rounded-full bg-blue-400" /> {t("cashFlow.legend_balance")}
          </span>
        </div>
      </div>

      <div className="grid md:grid-cols-2 gap-4">
        <div className={card}>
          <div className="flex items-center gap-2 mb-3">
            <Lightbulb className="h-5 w-5 text-amber-300" />
            <h3 className="font-semibold">{t("cashFlow.levers_title")}</h3>
          </div>
          {levers.length === 0 && <p className="text-sm opacity-70">{t("cashFlow.no_levers")}</p>}
          <ul className="space-y-3">
            {levers.map((l) => (
              <li key={l.id} className="rounded-xl border border-white/10 p-3">
                <div className="flex items-start justify-between gap-2">
                  <p className="font-medium text-sm">{l.title}</p>
                  <Badge variant="secondary" className="shrink-0">+{fmtUSD(l.projected_impact)}</Badge>
                </div>
                <p className="text-xs opacity-70 mt-1">{l.detail}</p>
                <p className="text-xs opacity-60 mt-1">{l.timeframe}</p>
              </li>
            ))}
          </ul>
        </div>

        <div className="space-y-4">
          {isOwner && (
            <div className={card}>
              <h3 className="font-semibold mb-3">{t("cashFlow.settings_title")}</h3>
              <div className="grid grid-cols-2 gap-3">
                <label className="text-sm space-y-1">
                  <span className="opacity-70">{t("cashFlow.starting_balance")}</span>
                  <Input type="number" min="0" step="0.01" value={balanceInput}
                    onChange={(e) => setBalanceInput(e.target.value)} />
                </label>
                <label className="text-sm space-y-1">
                  <span className="opacity-70">{t("cashFlow.safety_threshold")}</span>
                  <Input type="number" min="0" step="0.01" value={safetyInput}
                    onChange={(e) => setSafetyInput(e.target.value)} />
                </label>
              </div>
              <Button size="sm" className="mt-3" onClick={saveBalance} disabled={saving}>
                {t("cashFlow.save_settings")}
              </Button>
            </div>
          )}
          {assumptions && (
            <div className={card}>
              <h3 className="font-semibold mb-3">{t("cashFlow.assumptions_title")}</h3>
              <dl className="text-sm space-y-1.5">
                <div className="flex justify-between">
                  <dt className="opacity-70">{t("cashFlow.assumptions_days_to_pay")}</dt>
                  <dd className="font-medium">{assumptions.avg_days_to_pay} {t("cashFlow.days")}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="opacity-70">{t("cashFlow.assumptions_samples")}</dt>
                  <dd className="font-medium">{assumptions.days_to_pay_samples}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="opacity-70">{t("cashFlow.assumptions_terms")}</dt>
                  <dd className="font-medium">{assumptions.invoice_terms_days} {t("cashFlow.days")}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="opacity-70">{t("cashFlow.assumptions_supplier")}</dt>
                  <dd className="font-medium">{assumptions.supplier_payment_days} {t("cashFlow.days")}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="opacity-70">{t("cashFlow.assumptions_payroll")}</dt>
                  <dd className="font-medium">{fmtUSD(assumptions.monthly_payroll)}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="opacity-70">{t("cashFlow.assumptions_completion")}</dt>
                  <dd className="font-medium">
                    {Math.round((assumptions.job_completion_probability?.scheduled ?? 0) * 100)}–
                    {Math.round((assumptions.job_completion_probability?.on_site ?? 0) * 100)}%
                  </dd>
                </div>
              </dl>
              {assumptions.per_customer?.length > 0 && (
                <div className="mt-3">
                  <p className="text-xs opacity-70 mb-1.5">{t("cashFlow.per_customer")}</p>
                  <ul className="text-xs space-y-1">
                    {assumptions.per_customer.slice(0, 5).map((c) => (
                      <li key={c.customer_id} className="flex justify-between">
                        <span className="truncate mr-2">{c.customer_name || c.customer_id}</span>
                        <span className="opacity-70">{c.avg_days_to_pay} {t("cashFlow.days")} ({c.samples})</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
