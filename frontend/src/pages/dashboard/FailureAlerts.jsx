import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errMessage } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";
import {
  AlertTriangle, Activity, CheckCircle, XCircle, RefreshCw,
  Wrench, CalendarClock, Lightbulb, ChevronDown, BellOff,
} from "lucide-react";

const SEVERITY_STYLES = {
  critical: { cls: "bg-rose-100 text-rose-800", icon: <AlertTriangle className="h-3.5 w-3.5" /> },
  warning: { cls: "bg-amber-100 text-amber-800", icon: <AlertTriangle className="h-3.5 w-3.5" /> },
  info: { cls: "bg-sky-100 text-sky-800", icon: <Activity className="h-3.5 w-3.5" /> },
};

const STATUS_STYLES = {
  new: "bg-orange-100 text-orange-800",
  acknowledged: "bg-violet-100 text-violet-800",
  dismissed: "bg-zinc-200 text-zinc-700",
};

function RiskPanel({ equipmentId, onClose }) {
  const { t } = useTranslation();
  const [risk, setRisk] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await api.get(`/failure-alerts/equipment/${encodeURIComponent(equipmentId)}/risk`);
        if (!cancelled) setRisk(r.data);
      } catch (e) {
        if (!cancelled) toast.error(errMessage(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [equipmentId]);

  if (loading) return <div className="text-sm text-muted-foreground mt-3">{t("failureAlerts.loading_risk", "Computing risk…")}</div>;
  if (!risk) return null;

  const sev = SEVERITY_STYLES[risk.severity] || SEVERITY_STYLES.info;
  return (
    <div className="mt-3 rounded-lg bg-muted/50 p-4" data-testid={`fal-risk-${equipmentId}`}>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <span className="text-sm font-semibold">{t("failureAlerts.risk_breakdown", "Risk breakdown")} — {risk.equipment_label}</span>
        <Badge className={sev.cls}>{sev.icon}{risk.severity ? risk.severity : t("failureAlerts.healthy", "healthy")}</Badge>
        <span className="text-sm font-bold">{risk.score}/100</span>
        {risk.predicted_window && (
          <span className="text-xs text-muted-foreground flex items-center gap-1">
            <CalendarClock className="h-3.5 w-3.5" />
            {t("failureAlerts.failure_window", "Failure window")}: {risk.predicted_window}
          </span>
        )}
        <button onClick={onClose} className="ml-auto text-xs text-muted-foreground underline">{t("failureAlerts.hide", "Hide")}</button>
      </div>
      <div className="space-y-2">
        {(risk.components || []).map((c) => (
          <div key={c.factor}>
            <div className="flex justify-between text-xs mb-1">
              <span className="font-medium">{c.label}</span>
              <span className="text-muted-foreground">{c.points.toFixed(1)} {t("failureAlerts.pts", "pts")} · {t("failureAlerts.weight", "weight")} {c.weight}</span>
            </div>
            <div className="h-2 rounded-full bg-muted overflow-hidden">
              <div
                className={`h-full rounded-full ${c.score >= 0.8 ? "bg-rose-500" : c.score >= 0.5 ? "bg-amber-500" : c.score >= 0.3 ? "bg-sky-500" : "bg-emerald-500"}`}
                style={{ width: `${Math.min(100, c.score * 100)}%` }}
              />
            </div>
            <div className="text-xs text-muted-foreground mt-0.5">{c.detail}</div>
          </div>
        ))}
      </div>
      {risk.reasons?.length > 0 && (
        <ul className="text-xs text-muted-foreground mt-3 space-y-1 list-disc list-inside">
          {risk.reasons.map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      )}
    </div>
  );
}

export default function FailureAlerts() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [overview, setOverview] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [severityFilter, setSeverityFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("new");
  const [openRisk, setOpenRisk] = useState(null);
  const [busy, setBusy] = useState(null);
  const canScan = user && ["owner", "admin"].includes(user.role);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [ov, al] = await Promise.all([
        api.get("/failure-alerts/overview"),
        api.get("/failure-alerts/alerts"),
      ]);
      setOverview(ov.data);
      setAlerts(al.data.alerts || []);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = useMemo(() => alerts.filter((a) => {
    if (severityFilter && a.severity !== severityFilter) return false;
    if (statusFilter && a.status !== statusFilter) return false;
    return true;
  }), [alerts, severityFilter, statusFilter]);

  const runScan = async () => {
    setScanning(true);
    try {
      const r = await api.post("/failure-alerts/scan");
      toast.success(
        t("failureAlerts.scan_done", "Scan complete: {{created}} new, {{updated}} refreshed.", { created: r.data.alerts_created, updated: r.data.alerts_updated })
      );
      load();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setScanning(false);
    }
  };

  const act = async (id, verb) => {
    setBusy(id);
    try {
      await api.post(`/failure-alerts/alerts/${encodeURIComponent(id)}/${verb}`);
      toast.success(verb === "acknowledge"
        ? t("failureAlerts.acknowledged", "Alert acknowledged — the model learned from this.")
        : t("failureAlerts.dismissed", "Alert dismissed — the model dampened this factor."));
      load();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div>
      <PageHeader
        title={t("failureAlerts.title", "Failure Alerts")}
        description={t("failureAlerts.description", "Predicted equipment failures — age, service gaps, repair history and trouble signs, scored before anything breaks.")}
        testId="fal-header"
        actions={
          canScan && (
            <Button size="sm" onClick={runScan} disabled={scanning} data-testid="fal-scan">
              <RefreshCw className={`h-4 w-4 mr-1 ${scanning ? "animate-spin" : ""}`} />
              {scanning ? t("failureAlerts.scanning", "Scanning…") : t("failureAlerts.run_scan", "Run scan")}
            </Button>
          )
        }
      />

      {loading ? (
        <div className="text-muted-foreground">{t("failureAlerts.loading", "Loading failure alerts…")}</div>
      ) : (
        <>
          {/* KPI header */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5" data-testid="fal-kpis">
            {[
              { label: t("failureAlerts.critical", "Critical"), value: overview?.critical ?? 0, icon: <AlertTriangle className="h-4 w-4 text-rose-500" /> },
              { label: t("failureAlerts.warning", "Warning"), value: overview?.warning ?? 0, icon: <AlertTriangle className="h-4 w-4 text-amber-500" /> },
              { label: t("failureAlerts.info", "Info"), value: overview?.info ?? 0, icon: <Activity className="h-4 w-4 text-sky-500" /> },
              { label: t("failureAlerts.equipment_at_risk", "Units at risk"), value: `${overview?.equipment_at_risk ?? 0} / ${overview?.total_equipment ?? 0}`, icon: <Wrench className="h-4 w-4 text-violet-500" /> },
            ].map((k) => (
              <div key={k.label} className="rounded-xl border bg-card p-4">
                <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">{k.icon}{k.label}</div>
                <div className="text-2xl font-bold">{k.value}</div>
              </div>
            ))}
          </div>

          {/* Filters */}
          <div className="flex flex-wrap items-center gap-2 mb-3">
            <h3 className="font-semibold text-sm mr-2">{t("failureAlerts.alerts", "Alerts")}</h3>
            <select value={severityFilter} onChange={(e) => setSeverityFilter(e.target.value)} className="rounded-md border bg-card px-3 py-2 text-sm">
              <option value="">{t("failureAlerts.all_severities", "All severities")}</option>
              <option value="critical">{t("failureAlerts.critical", "Critical")}</option>
              <option value="warning">{t("failureAlerts.warning", "Warning")}</option>
              <option value="info">{t("failureAlerts.info", "Info")}</option>
            </select>
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="rounded-md border bg-card px-3 py-2 text-sm">
              <option value="">{t("failureAlerts.all_statuses", "All statuses")}</option>
              <option value="new">{t("failureAlerts.status_new", "New")}</option>
              <option value="acknowledged">{t("failureAlerts.status_acknowledged", "Acknowledged")}</option>
              <option value="dismissed">{t("failureAlerts.status_dismissed", "Dismissed")}</option>
            </select>
            {overview?.last_scan_at && (
              <span className="text-xs text-muted-foreground ml-auto">
                {t("failureAlerts.last_scan", "Last scan")}: {new Date(overview.last_scan_at).toLocaleString()}
              </span>
            )}
          </div>

          {filtered.length === 0 ? (
            <div className="rounded-xl border bg-muted/40 p-8 text-center text-muted-foreground">
              <BellOff className="h-6 w-6 mx-auto mb-2" />
              {t("failureAlerts.empty", "No alerts. Run a scan to predict failures before they happen.")}
            </div>
          ) : (
            <div className="space-y-3">
              {filtered.map((a) => {
                const sev = SEVERITY_STYLES[a.severity] || SEVERITY_STYLES.info;
                const isNew = a.status === "new";
                return (
                  <div key={a.id} className="rounded-xl border bg-card p-4" data-testid={`fal-alert-${a.id}`}>
                    <div className="flex flex-wrap items-center gap-2 mb-2">
                      <Badge className={sev.cls}>{sev.icon}{a.severity}</Badge>
                      <Badge className={STATUS_STYLES[a.status] || STATUS_STYLES.new}>
                        {a.status === "new" ? t("failureAlerts.status_new", "New")
                          : a.status === "acknowledged" ? t("failureAlerts.status_acknowledged", "Acknowledged")
                          : t("failureAlerts.status_dismissed", "Dismissed")}
                      </Badge>
                      <span className="font-medium text-sm">{a.equipment_label}</span>
                      <span className="text-sm font-bold ml-auto">{a.risk_score}/100</span>
                    </div>
                    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground mb-2">
                      <span className="flex items-center gap-1">
                        <CalendarClock className="h-3.5 w-3.5" />
                        {t("failureAlerts.failure_window", "Failure window")}: {a.predicted_window}
                      </span>
                      {a.reasons?.slice(0, 2).map((r, i) => (
                        <span key={i} className="flex items-center gap-1">
                          <AlertTriangle className="h-3.5 w-3.5 text-amber-500" /> {r}
                        </span>
                      ))}
                    </div>
                    <div className="rounded-lg bg-muted/50 p-3 text-sm mb-3 flex gap-2">
                      <Lightbulb className="h-4 w-4 shrink-0 text-amber-500 mt-0.5" />
                      <span>{a.suggested_action}</span>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {isNew && (
                        <>
                          <Button size="sm" variant="outline" onClick={() => act(a.id, "acknowledge")} disabled={busy === a.id} data-testid={`fal-ack-${a.id}`}>
                            <CheckCircle className="h-3.5 w-3.5 mr-1" /> {t("failureAlerts.acknowledge", "Acknowledge")}
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => act(a.id, "dismiss")} disabled={busy === a.id} data-testid={`fal-dismiss-${a.id}`}>
                            <XCircle className="h-3.5 w-3.5 mr-1" /> {t("failureAlerts.dismiss", "Dismiss")}
                          </Button>
                        </>
                      )}
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setOpenRisk(openRisk === a.equipment_id ? null : a.equipment_id)}
                        data-testid={`fal-risk-toggle-${a.id}`}
                      >
                        <Activity className="h-3.5 w-3.5 mr-1" />
                        {openRisk === a.equipment_id ? t("failureAlerts.hide", "Hide") : t("failureAlerts.view_risk", "View risk")}
                        <ChevronDown className={`h-3.5 w-3.5 ml-1 transition-transform ${openRisk === a.equipment_id ? "rotate-180" : ""}`} />
                      </Button>
                    </div>
                    {openRisk === a.equipment_id && (
                      <RiskPanel equipmentId={a.equipment_id} onClose={() => setOpenRisk(null)} />
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}
    </div>
  );
}
