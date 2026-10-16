import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errMessage } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "sonner";
import {
  Activity, BellRing, CheckCircle2, Plus, RadioTower, RefreshCw,
  ShieldCheck, Wrench, XCircle, Clock, Megaphone,
} from "lucide-react";

const STATUS_STYLES = {
  operational: { label: "Operational", cls: "bg-emerald-100 text-emerald-800", dot: "bg-emerald-500" },
  degraded: { label: "Degraded", cls: "bg-amber-100 text-amber-800", dot: "bg-amber-500" },
  partial_outage: { label: "Partial outage", cls: "bg-orange-100 text-orange-800", dot: "bg-orange-500" },
  major_outage: { label: "Major outage", cls: "bg-rose-100 text-rose-800", dot: "bg-rose-500" },
};

const INCIDENT_STYLES = {
  investigating: { label: "Investigating", cls: "bg-amber-100 text-amber-800" },
  identified: { label: "Identified", cls: "bg-sky-100 text-sky-800" },
  monitoring: { label: "Monitoring", cls: "bg-violet-100 text-violet-800" },
  resolved: { label: "Resolved", cls: "bg-emerald-100 text-emerald-800" },
};

const SEVERITY_STYLES = {
  minor: "bg-slate-100 text-slate-700",
  major: "bg-orange-100 text-orange-800",
  critical: "bg-rose-100 text-rose-800",
};

function StatusBadge({ status }) {
  const s = STATUS_STYLES[status] || STATUS_STYLES.operational;
  return (
    <span className="flex items-center gap-1.5">
      <span className={`h-2.5 w-2.5 rounded-full ${s.dot}`} />
      <Badge className={s.cls}>{s.label}</Badge>
    </span>
  );
}

function OpenIncidentModal({ onClose, onOpened }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({ title: "", severity: "minor" });
  const [saving, setSaving] = useState(false);
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  const open = async () => {
    if (!form.title.trim()) return toast.error(t("platformStatus.title_required", "Incident title is required"));
    setSaving(true);
    try {
      await api.post("/platform-status/incidents", { title: form.title.trim(), severity: form.severity });
      toast.success(t("platformStatus.incident_opened", "Incident opened."));
      onOpened();
      onClose();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-xl bg-card p-6 shadow-xl" data-testid="pstat-incident-modal">
        <h3 className="text-lg font-semibold mb-4">{t("platformStatus.open_incident", "Open an incident")}</h3>
        <div className="space-y-3">
          <Input placeholder={t("platformStatus.incident_title", "What's happening?")} value={form.title} onChange={(e) => set({ title: e.target.value })} />
          <select value={form.severity} onChange={(e) => set({ severity: e.target.value })} className="w-full rounded-md border bg-card px-3 py-2 text-sm">
            <option value="minor">{t("platformStatus.sev_minor", "Minor")}</option>
            <option value="major">{t("platformStatus.sev_major", "Major")}</option>
            <option value="critical">{t("platformStatus.sev_critical", "Critical")}</option>
          </select>
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <Button variant="outline" onClick={onClose}>{t("platformStatus.cancel", "Cancel")}</Button>
          <Button onClick={open} disabled={saving} data-testid="pstat-incident-open">
            <Megaphone className="h-4 w-4 mr-1" /> {t("platformStatus.open", "Open")}
          </Button>
        </div>
      </div>
    </div>
  );
}

function AddMonitorModal({ components, onClose, onAdded }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({ name: "", component_id: components[0]?.id || "", check_type: "http", target: "" });
  const [saving, setSaving] = useState(false);
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  const save = async () => {
    if (!form.name.trim() || !form.component_id) return toast.error(t("platformStatus.monitor_required", "Monitor name and component are required"));
    setSaving(true);
    try {
      await api.post("/platform-status/monitors", {
        name: form.name.trim(), component_id: form.component_id,
        check_type: form.check_type, target: form.target.trim(),
      });
      toast.success(t("platformStatus.monitor_added", "Monitor added."));
      onAdded();
      onClose();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-xl bg-card p-6 shadow-xl" data-testid="pstat-monitor-modal">
        <h3 className="text-lg font-semibold mb-4">{t("platformStatus.add_monitor", "Add a synthetic monitor")}</h3>
        <div className="space-y-3">
          <Input placeholder={t("platformStatus.monitor_name", "Monitor name")} value={form.name} onChange={(e) => set({ name: e.target.value })} />
          <select value={form.component_id} onChange={(e) => set({ component_id: e.target.value })} className="w-full rounded-md border bg-card px-3 py-2 text-sm">
            {components.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <div className="flex gap-2">
            <select value={form.check_type} onChange={(e) => set({ check_type: e.target.value })} className="rounded-md border bg-card px-3 py-2 text-sm">
              <option value="http">HTTP</option>
              <option value="ping">Ping</option>
              <option value="synthetic">Synthetic</option>
            </select>
            <Input placeholder={t("platformStatus.monitor_target", "Target (URL or host)")} value={form.target} onChange={(e) => set({ target: e.target.value })} />
          </div>
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <Button variant="outline" onClick={onClose}>{t("platformStatus.cancel", "Cancel")}</Button>
          <Button onClick={save} disabled={saving} data-testid="pstat-monitor-save">
            <RadioTower className="h-4 w-4 mr-1" /> {t("platformStatus.save", "Save")}
          </Button>
        </div>
      </div>
    </div>
  );
}

function IncidentCard({ incident, canManage, onChanged }) {
  const { t } = useTranslation();
  const [message, setMessage] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const style = INCIDENT_STYLES[incident.status] || INCIDENT_STYLES.investigating;

  const postUpdate = async () => {
    if (!message.trim()) return toast.error(t("platformStatus.update_required", "Write an update first"));
    setBusy(true);
    try {
      await api.post(`/platform-status/incidents/${encodeURIComponent(incident.id)}/updates`, {
        message: message.trim(), status: status || undefined,
      });
      toast.success(t("platformStatus.update_posted", "Public update posted."));
      setMessage("");
      setStatus("");
      onChanged();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const resolve = async () => {
    setBusy(true);
    try {
      await api.post(`/platform-status/incidents/${encodeURIComponent(incident.id)}/resolve`);
      toast.success(t("platformStatus.resolved", "Incident resolved — components restored."));
      onChanged();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-xl border bg-card p-4" data-testid={`pstat-incident-${incident.id}`}>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <Badge className={SEVERITY_STYLES[incident.severity] || SEVERITY_STYLES.minor}>{incident.severity}</Badge>
        <Badge className={style.cls}>{style.label}</Badge>
        {incident.auto_monitor_id && (
          <Badge variant="outline" className="flex items-center gap-1">
            <BellRing className="h-3 w-3" /> {t("platformStatus.auto_detected", "Auto-detected")}
          </Badge>
        )}
        <span className="font-medium text-sm">{incident.title}</span>
        <span className="ml-auto text-xs text-muted-foreground">{(incident.created_at || "").slice(0, 16).replace("T", " ")}</span>
      </div>

      {/* Timeline */}
      <div className="relative pl-5 mb-3 space-y-3 before:absolute before:left-1 before:top-1 before:bottom-1 before:w-px before:bg-border">
        {(incident.updates || []).map((u) => (
          <div key={u.id} className="relative text-sm">
            <span className="absolute -left-5 top-1 h-2.5 w-2.5 rounded-full bg-primary" />
            <div className="flex items-center gap-2 text-xs text-muted-foreground mb-0.5">
              <span className="capitalize font-medium text-foreground">{(u.status || "").replace("_", " ")}</span>
              <span className="flex items-center gap-1"><Clock className="h-3 w-3" />{(u.created_at || "").slice(0, 16).replace("T", " ")}</span>
            </div>
            <p>{u.message}</p>
          </div>
        ))}
      </div>

      {canManage && incident.status !== "resolved" && (
        <div className="rounded-lg bg-muted/50 p-3 space-y-2">
          <Textarea rows={2} placeholder={t("platformStatus.update_placeholder", "Post a public update…")} value={message} onChange={(e) => setMessage(e.target.value)} />
          <div className="flex flex-wrap gap-2">
            <select value={status} onChange={(e) => setStatus(e.target.value)} className="rounded-md border bg-card px-3 py-2 text-sm">
              <option value="">{t("platformStatus.keep_status", "Keep current status")}</option>
              {Object.entries(INCIDENT_STYLES).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
            </select>
            <Button size="sm" variant="outline" onClick={postUpdate} disabled={busy} data-testid={`pstat-update-${incident.id}`}>
              {t("platformStatus.post_update", "Post update")}
            </Button>
            <Button size="sm" onClick={resolve} disabled={busy} data-testid={`pstat-resolve-${incident.id}`}>
              <CheckCircle2 className="h-3.5 w-3.5 mr-1" /> {t("platformStatus.resolve", "Resolve")}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

function MonitorCard({ monitor, components, canManage, onChanged }) {
  const { t } = useTranslation();
  const [latency, setLatency] = useState("");
  const [detail, setDetail] = useState("");
  const [busy, setBusy] = useState(false);
  const comp = components.find((c) => c.id === monitor.component_id);
  const fails = monitor.consecutive_failures || 0;

  const check = async (success) => {
    setBusy(true);
    try {
      const r = await api.post(`/platform-status/monitors/${encodeURIComponent(monitor.id)}/check`, {
        success, latency_ms: latency ? Number(latency) : null, detail,
      });
      if (r.data.incident) toast.warning(t("platformStatus.auto_incident", "3 consecutive failures — incident auto-opened."));
      else toast.success(success ? t("platformStatus.check_ok", "Check recorded: success.") : t("platformStatus.check_fail", "Check recorded: failure."));
      setDetail("");
      onChanged();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-xl border bg-card p-4" data-testid={`pstat-monitor-${monitor.id}`}>
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <RadioTower className="h-4 w-4 text-muted-foreground" />
        <span className="font-medium text-sm">{monitor.name}</span>
        <Badge variant="outline">{comp?.name || monitor.component_id}</Badge>
        <Badge variant="secondary">{monitor.check_type}</Badge>
        {fails > 0 ? (
          <Badge className="bg-rose-100 text-rose-800">{t("platformStatus.failing_streak", { count: fails, defaultValue: `${fails} consecutive failures` })}</Badge>
        ) : (
          <Badge className="bg-emerald-100 text-emerald-800">{t("platformStatus.healthy", "Healthy")}</Badge>
        )}
      </div>
      <div className="flex flex-wrap gap-2 text-xs text-muted-foreground mb-3">
        {(monitor.recent_results || []).slice(0, 8).map((r) => (
          <span key={r.id} className="flex items-center gap-1" title={`${r.latency_ms ?? "—"} ms · ${r.detail || ""}`}>
            {r.success ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" /> : <XCircle className="h-3.5 w-3.5 text-rose-500" />}
            {r.latency_ms != null && <span>{Math.round(r.latency_ms)}ms</span>}
          </span>
        ))}
        {(monitor.recent_results || []).length === 0 && (
          <span>{t("platformStatus.no_checks", "No checks recorded yet.")}</span>
        )}
      </div>
      {canManage && (
        <div className="flex flex-wrap gap-2 items-center rounded-lg bg-muted/50 p-2">
          <Input className="w-28 h-8 text-sm" placeholder={t("platformStatus.latency_ms", "Latency ms")} value={latency} onChange={(e) => setLatency(e.target.value)} />
          <Input className="flex-1 min-w-[120px] h-8 text-sm" placeholder={t("platformStatus.detail", "Detail (optional)")} value={detail} onChange={(e) => setDetail(e.target.value)} />
          <Button size="sm" variant="outline" onClick={() => check(true)} disabled={busy} data-testid={`pstat-check-ok-${monitor.id}`}>
            <CheckCircle2 className="h-3.5 w-3.5 mr-1" /> {t("platformStatus.log_ok", "Log OK")}
          </Button>
          <Button size="sm" variant="outline" onClick={() => check(false)} disabled={busy} data-testid={`pstat-check-fail-${monitor.id}`}>
            <XCircle className="h-3.5 w-3.5 mr-1" /> {t("platformStatus.log_fail", "Log fail")}
          </Button>
        </div>
      )}
    </div>
  );
}

export default function PlatformStatus() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [components, setComponents] = useState([]);
  const [incidents, setIncidents] = useState([]);
  const [monitors, setMonitors] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showIncident, setShowIncident] = useState(false);
  const [showMonitor, setShowMonitor] = useState(false);
  const [busyStatus, setBusyStatus] = useState(null);
  const canManage = user && ["owner", "admin"].includes(user.role);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [c, i, m] = await Promise.all([
        api.get("/platform-status/components"),
        api.get("/platform-status/incidents"),
        api.get("/platform-status/monitors"),
      ]);
      setComponents(c.data.components || []);
      setIncidents(i.data.incidents || []);
      setMonitors(m.data.monitors || []);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const setComponentStatus = async (id, status) => {
    setBusyStatus(id);
    try {
      await api.patch(`/platform-status/components/${encodeURIComponent(id)}`, { status });
      toast.success(t("platformStatus.status_updated", "Component status updated."));
      load();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusyStatus(null);
    }
  };

  const worst = useMemo(() => {
    const rank = { operational: 0, degraded: 1, partial_outage: 2, major_outage: 3 };
    return components.reduce((w, c) => (rank[c.status] > rank[w] ? c.status : w), "operational");
  }, [components]);
  const openIncidents = incidents.filter((i) => i.status !== "resolved");

  return (
    <div>
      <PageHeader
        title={t("platformStatus.title", "Platform status & monitoring")}
        description={t("platformStatus.description", "Live component health, incident timeline, and proactive synthetic monitoring — the public status page your shops see.")}
        testId="pstat-header"
        actions={
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={load} data-testid="pstat-refresh">
              <RefreshCw className="h-4 w-4 mr-1" /> {t("platformStatus.refresh", "Refresh")}
            </Button>
            {canManage && (
              <>
                <Button size="sm" variant="outline" onClick={() => setShowMonitor(true)} data-testid="pstat-monitor-open">
                  <RadioTower className="h-4 w-4 mr-1" /> {t("platformStatus.add_monitor", "Add a synthetic monitor")}
                </Button>
                <Button size="sm" onClick={() => setShowIncident(true)} data-testid="pstat-incident-btn">
                  <Plus className="h-4 w-4 mr-1" /> {t("platformStatus.open_incident", "Open an incident")}
                </Button>
              </>
            )}
          </div>
        }
      />

      {loading ? (
        <div className="text-muted-foreground">{t("platformStatus.loading", "Loading platform status…")}</div>
      ) : (
        <>
          {/* Overall banner */}
          <div className={`rounded-xl border p-4 mb-5 flex items-center gap-3 ${worst === "operational" ? "bg-emerald-50/60 border-emerald-200" : "bg-amber-50/60 border-amber-200"}`} data-testid="pstat-overall">
            {worst === "operational" ? <ShieldCheck className="h-5 w-5 text-emerald-600" /> : <Wrench className="h-5 w-5 text-amber-600" />}
            <div>
              <div className="font-semibold text-sm">{t("platformStatus.overall", "Overall platform status")}: {(STATUS_STYLES[worst] || {}).label}</div>
              {openIncidents.length > 0 && (
                <div className="text-xs text-muted-foreground">
                  {t("platformStatus.open_incidents", {
                    count: openIncidents.length,
                    defaultValue: openIncidents.length === 1 ? "1 open incident" : `${openIncidents.length} open incidents`,
                  })}
                </div>
              )}
            </div>
          </div>

          {/* Component board */}
          <h3 className="font-semibold text-sm mb-3 flex items-center gap-2">
            <Activity className="h-4 w-4 text-muted-foreground" /> {t("platformStatus.components", "Components")}
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 mb-6">
            {components.map((c) => (
              <div key={c.id} className="rounded-xl border bg-card p-4" data-testid={`pstat-comp-${c.id}`}>
                <div className="flex items-center justify-between mb-1">
                  <span className="font-medium text-sm">{c.name}</span>
                  <StatusBadge status={c.status} />
                </div>
                <p className="text-xs text-muted-foreground mb-2">{c.description}</p>
                {canManage && (
                  <select
                    value={c.status}
                    onChange={(e) => setComponentStatus(c.id, e.target.value)}
                    disabled={busyStatus === c.id}
                    className="w-full rounded-md border bg-card px-2 py-1.5 text-sm"
                    data-testid={`pstat-comp-status-${c.id}`}
                  >
                    {Object.entries(STATUS_STYLES).map(([k, v]) => (
                      <option key={k} value={k}>{v.label}</option>
                    ))}
                  </select>
                )}
              </div>
            ))}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Incidents */}
            <div>
              <h3 className="font-semibold text-sm mb-3 flex items-center gap-2">
                <Megaphone className="h-4 w-4 text-muted-foreground" />
                {t("platformStatus.incidents", "Incidents")}
                {openIncidents.length > 0 && <Badge className="bg-amber-100 text-amber-800">{openIncidents.length}</Badge>}
              </h3>
              {incidents.length === 0 ? (
                <div className="rounded-xl border bg-muted/40 p-8 text-center text-muted-foreground">
                  <ShieldCheck className="h-6 w-6 mx-auto mb-2" />
                  {t("platformStatus.no_incidents", "No incidents. The timeline stays empty until something needs communicating.")}
                </div>
              ) : (
                <div className="space-y-3">
                  {incidents.map((i) => (
                    <IncidentCard key={i.id} incident={i} canManage={canManage} onChanged={load} />
                  ))}
                </div>
              )}
            </div>

            {/* Monitors */}
            <div>
              <h3 className="font-semibold text-sm mb-3 flex items-center gap-2">
                <RadioTower className="h-4 w-4 text-muted-foreground" /> {t("platformStatus.monitors", "Synthetic monitors")}
              </h3>
              {monitors.length === 0 ? (
                <div className="rounded-xl border bg-muted/40 p-8 text-center text-muted-foreground">
                  <RadioTower className="h-6 w-6 mx-auto mb-2" />
                  {t("platformStatus.no_monitors", "No monitors yet. Add one per component — 3 consecutive failures auto-opens an incident and pages the on-call.")}
                </div>
              ) : (
                <div className="space-y-3">
                  {monitors.map((m) => (
                    <MonitorCard key={m.id} monitor={m} components={components} canManage={canManage} onChanged={load} />
                  ))}
                </div>
              )}
            </div>
          </div>
        </>
      )}

      {showIncident && <OpenIncidentModal onClose={() => setShowIncident(false)} onOpened={load} />}
      {showMonitor && <AddMonitorModal components={components} onClose={() => setShowMonitor(false)} onAdded={load} />}
    </div>
  );
}
