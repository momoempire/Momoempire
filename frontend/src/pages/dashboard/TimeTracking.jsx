import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errMessage } from "@/lib/api";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import {
  Clock, Clock3, Loader2, CheckCircle2, Download, Copy,
  ChevronLeft, ChevronRight, Timer, Settings2, Save,
} from "lucide-react";
const BASE = "/time-tracking";

function todayISO() {
  return new Date().toISOString().slice(0, 10);
}

function fmtTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function shiftWeek(weekStartISO, dir) {
  const d = new Date(`${weekStartISO}T12:00:00`);
  d.setDate(d.getDate() + 7 * dir);
  return d.toISOString().slice(0, 10);
}

// ---------- clock card ----------

function ClockCard({ techs, onChanged, t }) {
  const [techId, setTechId] = useState("");
  const [jobId, setJobId] = useState("");
  const [openEntry, setOpenEntry] = useState(null);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(Date.now());

  const refreshOpen = useCallback(async (tid) => {
    if (!tid) { setOpenEntry(null); return; }
    try {
      const res = await api.get(`${BASE}/entries`, { params: { tech_id: tid, open_only: true } });
      setOpenEntry(res.data.entries[0] || null);
    } catch (e) {
      toast.error(errMessage(e));
    }
  }, []);

  useEffect(() => { refreshOpen(techId); }, [techId, refreshOpen]);

  useEffect(() => {
    if (!openEntry) return;
    const id = setInterval(() => setNow(Date.now()), 30000);
    return () => clearInterval(id);
  }, [openEntry]);

  const elapsed = useMemo(() => {
    if (!openEntry?.clock_in) return null;
    const mins = Math.max(0, Math.round((now - new Date(openEntry.clock_in).getTime()) / 60000));
    const h = Math.floor(mins / 60);
    const m = mins % 60;
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
  }, [openEntry, now]);

  const clockIn = async () => {
    if (!techId) return;
    setBusy(true);
    try {
      await api.post(`${BASE}/clock-in`, { tech_id: techId, job_id: jobId.trim() || null });
      const tech = techs.find((x) => x.id === techId);
      toast.success(t("time_tracking.clock_in_toast", { name: tech?.name || techId }));
      setJobId("");
      await refreshOpen(techId);
      onChanged();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const clockOut = async () => {
    if (!techId) return;
    setBusy(true);
    try {
      const res = await api.post(`${BASE}/clock-out`, { tech_id: techId });
      const tech = techs.find((x) => x.id === techId);
      toast.success(t("time_tracking.clock_out_toast", {
        name: tech?.name || techId, h: res.data.entry.hours ?? 0,
      }));
      await refreshOpen(techId);
      onChanged();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="surface p-5 md:p-6 space-y-4" data-testid="time-tracking-clock-card">
      <h2 className="font-medium flex items-center gap-2">
        <Clock className="h-5 w-5" /> {t("time_tracking.clock_card_title")}
      </h2>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <label className="space-y-1">
          <span className="text-sm text-muted-foreground">{t("time_tracking.tech_label")}</span>
          <select
            className="w-full border rounded-md p-3 text-base bg-background"
            value={techId}
            onChange={(e) => setTechId(e.target.value)}
            data-testid="time-tracking-tech-select"
          >
            <option value="">{t("time_tracking.tech_placeholder")}</option>
            {techs.map((x) => (
              <option key={x.id} value={x.id}>{x.name}</option>
            ))}
          </select>
        </label>
        <label className="space-y-1">
          <span className="text-sm text-muted-foreground">{t("time_tracking.job_label")}</span>
          <Input
            value={jobId}
            onChange={(e) => setJobId(e.target.value)}
            placeholder={t("time_tracking.job_placeholder")}
            data-testid="time-tracking-job-input"
          />
        </label>
      </div>

      {techId && openEntry ? (
        <div className="flex items-center gap-3 flex-wrap" data-testid="time-tracking-open-entry">
          <Badge variant="secondary" className="text-base px-3 py-1">
            <Timer className="h-4 w-4 mr-1" />
            {t("time_tracking.clocked_in")} · {t("time_tracking.clocked_in_at", { t: fmtTime(openEntry.clock_in) })}
            {elapsed && ` (${elapsed})`}
          </Badge>
        </div>
      ) : techId ? (
        <div className="text-sm text-muted-foreground">{t("time_tracking.not_clocked_in")}</div>
      ) : null}

      {openEntry ? (
        <Button
          size="lg" className="w-full text-lg py-6" variant="destructive"
          onClick={clockOut} disabled={busy || !techId}
          data-testid="time-tracking-clock-out"
        >
          {busy ? <Loader2 className="h-5 w-5 mr-2 animate-spin" /> : <Clock3 className="h-5 w-5 mr-2" />}
          {t("time_tracking.clock_out")}
        </Button>
      ) : (
        <Button
          size="lg" className="w-full text-lg py-6" onClick={clockIn}
          disabled={busy || !techId} data-testid="time-tracking-clock-in"
        >
          {busy ? <Loader2 className="h-5 w-5 mr-2 animate-spin" /> : <Clock className="h-5 w-5 mr-2" />}
          {t("time_tracking.clock_in")}
        </Button>
      )}
    </div>
  );
}

// ---------- timesheet ----------

function Timesheet({ techs, weekStart, setWeekStart, onChanged, t }) {
  const [techId, setTechId] = useState("");
  const [sheet, setSheet] = useState(null);
  const [loading, setLoading] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [otHours, setOtHours] = useState(40);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    if (!techId) { setSheet(null); return; }
    setLoading(true);
    try {
      const res = await api.get(`${BASE}/timesheet`, { params: { tech_id: techId, week: weekStart } });
      setSheet(res.data);
      setOtHours(res.data.ot_weekly_hours);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  }, [techId, weekStart]);

  useEffect(() => { load(); }, [load]);

  const saveSettings = async () => {
    setSaving(true);
    try {
      await api.put(`${BASE}/settings`, { ot_weekly_hours: Number(otHours) });
      toast.success(t("time_tracking.settings_saved_toast"));
      load();
      onChanged();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="surface p-5 md:p-6 space-y-4" data-testid="time-tracking-timesheet">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h2 className="font-medium">{t("time_tracking.timesheet_title")}</h2>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="icon" onClick={() => setWeekStart(shiftWeek(weekStart, -1))} data-testid="time-tracking-prev-week">
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <span className="text-sm font-medium" data-testid="time-tracking-week-label">
            {t("time_tracking.week_of", { d: weekStart })}
          </span>
          <Button variant="outline" size="icon" onClick={() => setWeekStart(shiftWeek(weekStart, 1))} data-testid="time-tracking-next-week">
            <ChevronRight className="h-4 w-4" />
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setShowSettings((s) => !s)} data-testid="time-tracking-settings-toggle">
            <Settings2 className="h-4 w-4 mr-1" /> {t("time_tracking.settings_title")}
          </Button>
        </div>
      </div>

      {showSettings && (
        <div className="border rounded-lg p-4 flex items-end gap-3 flex-wrap" data-testid="time-tracking-settings">
          <label className="space-y-1">
            <span className="text-sm text-muted-foreground">{t("time_tracking.ot_threshold_label")}</span>
            <Input
              type="number" min="1" max="168" step="1" value={otHours}
              onChange={(e) => setOtHours(e.target.value)}
              className="w-32" data-testid="time-tracking-ot-input"
            />
          </label>
          <Button size="sm" onClick={saveSettings} disabled={saving} data-testid="time-tracking-ot-save">
            {saving ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Save className="h-4 w-4 mr-1" />}
            {t("time_tracking.settings_saved_toast")}
          </Button>
        </div>
      )}

      <label className="space-y-1 block max-w-sm">
        <span className="text-sm text-muted-foreground">{t("time_tracking.tech_label")}</span>
        <select
          className="w-full border rounded-md p-2.5 bg-background"
          value={techId} onChange={(e) => setTechId(e.target.value)}
          data-testid="time-tracking-timesheet-tech"
        >
          <option value="">{t("time_tracking.tech_placeholder")}</option>
          {techs.map((x) => (
            <option key={x.id} value={x.id}>{x.name}</option>
          ))}
        </select>
      </label>

      {loading ? (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> {t("time_tracking.loading")}
        </div>
      ) : sheet ? (
        <div className="space-y-4">
          <div className="grid grid-cols-2 sm:grid-cols-7 gap-2" data-testid="time-tracking-days">
            {sheet.days.map((d, i) => (
              <div key={d.date} className="border rounded-lg p-3 text-center">
                <div className="text-xs text-muted-foreground">
                  {t(`time_tracking.day_${["mon", "tue", "wed", "thu", "fri", "sat", "sun"][i]}`)}
                </div>
                <div className="text-lg font-semibold">{d.hours}h</div>
                <div className="text-xs text-muted-foreground">{d.date.slice(5)}</div>
              </div>
            ))}
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <Badge variant="secondary" data-testid="time-tracking-regular">
              {t("time_tracking.regular_hours")}: {sheet.regular_hours}h
            </Badge>
            <Badge variant={sheet.overtime_hours > 0 ? "default" : "secondary"} data-testid="time-tracking-overtime">
              {t("time_tracking.overtime_hours")}: {sheet.overtime_hours}h
            </Badge>
            <Badge variant="outline" data-testid="time-tracking-total">
              {t("time_tracking.total_hours")}: {sheet.total_hours}h
            </Badge>
            <span className="text-xs text-muted-foreground">
              {t("time_tracking.ot_threshold", { n: sheet.ot_weekly_hours })}
            </span>
            {sheet.approved && (
              <Badge variant="secondary"><CheckCircle2 className="h-3 w-3 mr-1" />{t("time_tracking.approved_badge")}</Badge>
            )}
          </div>
          {sheet.entries.length === 0 && (
            <div className="text-sm text-muted-foreground">{t("time_tracking.no_hours")}</div>
          )}
        </div>
      ) : (
        <div className="text-sm text-muted-foreground">{t("time_tracking.timesheet_sub")}</div>
      )}
    </div>
  );
}

// ---------- approval queue ----------

function ApprovalQueue({ weekStart, onChanged, t }) {
  const [entries, setEntries] = useState([]);
  const [selected, setSelected] = useState(new Set());
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const weekEnd = shiftWeek(weekStart, 1);
      const res = await api.get(`${BASE}/entries`, {
        params: { from: weekStart, to: weekEnd },
      });
      setEntries((res.data.entries || []).filter((e) => !e.approved && e.clock_out));
      setSelected(new Set());
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  }, [weekStart]);

  useEffect(() => { load(); }, [load]);

  const toggle = (id) => setSelected((s) => {
    const next = new Set(s);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const approve = async (ids) => {
    if (ids.length === 0) return;
    setBusy(true);
    try {
      const res = await api.post(`${BASE}/timesheet/approve`, { entry_ids: ids });
      toast.success(t("time_tracking.approved_toast", { n: res.data.count }));
      await load();
      onChanged();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="surface p-5 md:p-6 space-y-4" data-testid="time-tracking-approvals">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h2 className="font-medium">{t("time_tracking.approval_title")}</h2>
        <div className="flex items-center gap-2">
          {selected.size > 0 && (
            <Button size="sm" onClick={() => approve([...selected])} disabled={busy} data-testid="time-tracking-approve-selected">
              {busy ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <CheckCircle2 className="h-4 w-4 mr-1" />}
              {t("time_tracking.approve_selected", { n: selected.size })}
            </Button>
          )}
          <Button size="sm" variant="outline" onClick={() => approve(entries.map((e) => e.id))} disabled={busy || entries.length === 0} data-testid="time-tracking-approve-all">
            {t("time_tracking.approve_all")}
          </Button>
        </div>
      </div>
      <p className="text-sm text-muted-foreground">{t("time_tracking.approval_sub")}</p>
      {loading ? (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> {t("time_tracking.loading")}
        </div>
      ) : entries.length === 0 ? (
        <div className="border rounded-lg p-8 text-center text-muted-foreground text-sm" data-testid="time-tracking-approvals-empty">
          {t("time_tracking.no_hours")}
        </div>
      ) : (
        <div className="space-y-2">
          <label className="flex items-center gap-2 text-sm text-muted-foreground">
            <input
              type="checkbox"
              checked={selected.size === entries.length}
              onChange={() => setSelected(selected.size === entries.length ? new Set() : new Set(entries.map((e) => e.id)))}
            />
            {t("time_tracking.select_all")}
          </label>
          {entries.map((e) => (
            <div key={e.id} className="border rounded-lg p-3 flex items-center gap-3" data-testid={`time-tracking-entry-${e.id}`}>
              <input
                type="checkbox" checked={selected.has(e.id)} onChange={() => toggle(e.id)}
                data-testid={`time-tracking-select-${e.id}`}
              />
              <div className="flex-1 min-w-0">
                <div className="font-medium truncate">{e.tech_name || e.tech_id}</div>
                <div className="text-sm text-muted-foreground">
                  {fmtTime(e.clock_in)} → {fmtTime(e.clock_out)}
                  {e.job_id ? ` · ${e.job_id}` : ""}
                </div>
              </div>
              <Badge variant="secondary">{e.hours ?? 0}h</Badge>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------- payroll export ----------

function PayrollExport({ t }) {
  const [period, setPeriod] = useState(todayISO().slice(0, 7));
  const [rows, setRows] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get(`${BASE}/payroll-export`, { params: { period } });
      setRows(res.data);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  }, [period]);

  useEffect(() => { load(); }, [load]);

  const csv = useMemo(() => {
    if (!rows) return "";
    const lines = ["tech_id,tech_name,regular_hours,overtime_hours,total_hours,entry_count,approved_count"];
    for (const r of rows.techs) {
      lines.push([r.tech_id, `"${(r.tech_name || "").replace(/"/g, '""')}"`,
        r.regular_hours, r.overtime_hours, r.total_hours, r.entry_count, r.approved_count].join(","));
    }
    return lines.join("\n");
  }, [rows]);

  const downloadCSV = () => {
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `payroll-${period}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const copyJSON = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(rows, null, 2));
      toast.success(t("time_tracking.copied_toast"));
    } catch {
      toast.error("clipboard");
    }
  };

  return (
    <div className="surface p-5 md:p-6 space-y-4" data-testid="time-tracking-export">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h2 className="font-medium">{t("time_tracking.export_title")}</h2>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={downloadCSV} disabled={!rows} data-testid="time-tracking-export-csv">
            <Download className="h-4 w-4 mr-1" /> {t("time_tracking.export_csv")}
          </Button>
          <Button variant="outline" size="sm" onClick={copyJSON} disabled={!rows} data-testid="time-tracking-export-json">
            <Copy className="h-4 w-4 mr-1" /> {t("time_tracking.export_json")}
          </Button>
        </div>
      </div>
      <p className="text-sm text-muted-foreground">{t("time_tracking.export_sub")}</p>
      <label className="space-y-1 block max-w-xs">
        <span className="text-sm text-muted-foreground">{t("time_tracking.period_label")}</span>
        <Input
          type="month" value={period} onChange={(e) => setPeriod(e.target.value)}
          data-testid="time-tracking-period"
        />
      </label>
      {loading ? (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> {t("time_tracking.loading")}
        </div>
      ) : rows && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="time-tracking-export-table">
            <thead>
              <tr className="text-left text-muted-foreground border-b">
                <th className="py-2 pr-3">{t("time_tracking.tech_label")}</th>
                <th className="py-2 pr-3">{t("time_tracking.regular_hours")}</th>
                <th className="py-2 pr-3">{t("time_tracking.overtime_hours")}</th>
                <th className="py-2 pr-3">{t("time_tracking.total_hours")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.techs.map((r) => (
                <tr key={r.tech_id} className="border-b">
                  <td className="py-2 pr-3 font-medium">{r.tech_name || r.tech_id}</td>
                  <td className="py-2 pr-3">{r.regular_hours}h</td>
                  <td className="py-2 pr-3">{r.overtime_hours}h</td>
                  <td className="py-2 pr-3 font-medium">{r.total_hours}h</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="flex items-center gap-2 pt-3">
            <Badge variant="secondary">
              {t("time_tracking.regular_hours")}: {rows.totals.regular_hours}h
            </Badge>
            <Badge variant={rows.totals.overtime_hours > 0 ? "default" : "secondary"}>
              {t("time_tracking.overtime_hours")}: {rows.totals.overtime_hours}h
            </Badge>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------- page ----------

const TABS = ["timesheet", "entries", "approvals", "export"];

export default function TimeTracking() {
  const { t } = useTranslation("time_tracking");
  const [techs, setTechs] = useState([]);
  const [tab, setTab] = useState("timesheet");
  const [weekStart, setWeekStart] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
    return d.toISOString().slice(0, 10);
  });
  const [entries, setEntries] = useState([]);
  const [loadingEntries, setLoadingEntries] = useState(false);

  useEffect(() => {
    api.get("/tenants/dispatch/board", { params: { date: todayISO() } })
      .then((res) => setTechs(res.data.techs || []))
      .catch(() => {});
  }, []);

  const loadEntries = useCallback(async () => {
    setLoadingEntries(true);
    try {
      const res = await api.get(`${BASE}/entries`, { params: { from: weekStart, to: shiftWeek(weekStart, 1) } });
      setEntries(res.data.entries || []);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoadingEntries(false);
    }
  }, [weekStart]);

  useEffect(() => { if (tab === "entries") loadEntries(); }, [tab, loadEntries]);

  const onChanged = useCallback(() => { loadEntries(); }, [loadEntries]);

  return (
    <div className="space-y-6" data-testid="time-tracking-page">
      <PageHeader
        eyebrow={t("time_tracking.eyebrow")}
        title={t("time_tracking.title")}
        description={t("time_tracking.sub")}
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-1">
          <ClockCard techs={techs} onChanged={onChanged} t={t} />
        </div>
        <div className="lg:col-span-2 space-y-6">
          <div className="flex items-center gap-2 flex-wrap" role="tablist" data-testid="time-tracking-tabs">
            {TABS.map((name) => (
              <Button
                key={name} size="sm" variant={tab === name ? "default" : "outline"}
                onClick={() => setTab(name)} role="tab" aria-selected={tab === name}
                data-testid={`time-tracking-tab-${name}`}
              >
                {t(`time_tracking.${name === "timesheet" ? "timesheet_tab" : name === "entries" ? "entries_tab" : name === "approvals" ? "approve_tab" : "export_tab"}`)}
              </Button>
            ))}
          </div>

          {tab === "timesheet" && (
            <Timesheet techs={techs} weekStart={weekStart} setWeekStart={setWeekStart} onChanged={onChanged} t={t} />
          )}

          {tab === "entries" && (
            <div className="surface p-5 md:p-6 space-y-4" data-testid="time-tracking-entries">
              <h2 className="font-medium">{t("time_tracking.entries_tab")}</h2>
              {loadingEntries ? (
                <div className="flex items-center gap-2 text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" /> {t("time_tracking.loading")}
                </div>
              ) : entries.length === 0 ? (
                <div className="text-sm text-muted-foreground">{t("time_tracking.no_hours")}</div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-muted-foreground border-b">
                        <th className="py-2 pr-3">{t("time_tracking.tech_label")}</th>
                        <th className="py-2 pr-3">{t("time_tracking.clock_in_col")}</th>
                        <th className="py-2 pr-3">{t("time_tracking.clock_out_col")}</th>
                        <th className="py-2 pr-3">{t("time_tracking.hours_col")}</th>
                        <th className="py-2 pr-3">{t("time_tracking.job_col")}</th>
                        <th className="py-2 pr-3">{t("time_tracking.note_col")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {entries.map((e) => (
                        <tr key={e.id} className="border-b">
                          <td className="py-2 pr-3 font-medium">{e.tech_name || e.tech_id}</td>
                          <td className="py-2 pr-3">{fmtTime(e.clock_in)}</td>
                          <td className="py-2 pr-3">{e.clock_out ? fmtTime(e.clock_out) : <Badge variant="outline">{t("time_tracking.open_badge")}</Badge>}</td>
                          <td className="py-2 pr-3">{e.hours ?? "—"}</td>
                          <td className="py-2 pr-3">{e.job_id || "—"}</td>
                          <td className="py-2 pr-3">
                            {e.note || "—"}
                            {e.approved && <Badge variant="secondary" className="ml-2"><CheckCircle2 className="h-3 w-3 mr-1" />{t("time_tracking.approved_badge")}</Badge>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {tab === "approvals" && (
            <ApprovalQueue weekStart={weekStart} onChanged={onChanged} t={t} />
          )}

          {tab === "export" && <PayrollExport t={t} />}
        </div>
      </div>
    </div>
  );
}
