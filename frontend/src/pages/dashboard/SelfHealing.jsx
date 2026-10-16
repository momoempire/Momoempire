import { useCallback, useEffect, useState } from "react";
import { api, errMessage } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";
import {
  HeartPulse,
  RefreshCw,
  ScanLine,
  Undo2,
  CheckCircle2,
  Wrench,
  Bell,
  Timer,
} from "lucide-react";

const HEALER_ICONS = {
  double_booking: Wrench,
  unconfirmed_appointment: Bell,
  stale_lead: Timer,
  overdue_invoice_nudge: HeartPulse,
  no_show_risk: HeartPulse,
};

const STATUS_STYLES = {
  open: "bg-amber-100 text-amber-900",
  auto_fixed: "bg-emerald-100 text-emerald-900",
  overridden: "bg-purple-100 text-purple-900",
  resolved: "bg-slate-200 text-slate-700",
};

const STATUS_FILTERS = ["", "open", "auto_fixed", "overridden", "resolved"];

function StatCard({ label, value, icon: Icon, tone }) {
  return (
    <div className="glass-crystal rounded-2xl p-5">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-2xl font-bold">{value}</div>
          <div className="text-sm text-muted-foreground">{label}</div>
        </div>
        <Icon className={`h-8 w-8 ${tone}`} />
      </div>
    </div>
  );
}

export default function SelfHealing() {
  const { user } = useAuth();
  const canManage = user?.role === "owner" || user?.role === "admin" || user?.role === "platform_admin";
  const [healers, setHealers] = useState([]);
  const [issues, setIssues] = useState([]);
  const [counts, setCounts] = useState({});
  const [statusFilter, setStatusFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [overrideFor, setOverrideFor] = useState(null);
  const [overrideReason, setOverrideReason] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [healersRes, issuesRes] = await Promise.all([
        api.get("/tenants/self-healing/healers"),
        api.get("/tenants/self-healing/issues", {
          params: statusFilter ? { status: statusFilter } : undefined,
        }),
      ]);
      setHealers(healersRes.data.healers || []);
      setIssues(issuesRes.data.issues || []);
      setCounts(issuesRes.data.counts || {});
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    load();
  }, [load]);

  const toggleHealer = async (healer) => {
    try {
      await api.post(`/tenants/self-healing/healers/${healer.id}/toggle`, {
        enabled: !healer.enabled,
      });
      toast.success(`${healer.name} ${healer.enabled ? "disabled" : "enabled"}`);
      load();
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const runScan = async () => {
    setScanning(true);
    try {
      const r = await api.post("/tenants/self-healing/scan");
      toast.success(
        `Scan complete: ${r.data.detected} issue${r.data.detected === 1 ? "" : "s"} found, ${r.data.fixes_applied} auto-fixed`
      );
      load();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setScanning(false);
    }
  };

  const resolveIssue = async (issue) => {
    try {
      await api.post(`/tenants/self-healing/issues/${issue.id}/resolve`);
      toast.success("Issue resolved");
      load();
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const submitOverride = async () => {
    if (!overrideReason.trim()) {
      toast.error("A reason is required for human override");
      return;
    }
    try {
      const r = await api.post(`/tenants/self-healing/issues/${overrideFor.id}/override`, {
        reason: overrideReason.trim(),
      });
      toast.success(
        `Overridden${r.data.reverted_fields?.length ? " — auto-fix reverted" : ""}`
      );
      setOverrideFor(null);
      setOverrideReason("");
      load();
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const autoFixed = healers.reduce((n, h) => n + (h.fixes_applied || 0), 0);

  return (
    <div>
      <PageHeader
        title="Self-Healing Operations"
        description="Watchers detect common operational problems and fix them automatically — every action is logged, and a human can override any fix (reverting it) at any time."
        testId="selfhealing-header"
        actions={
          <Button size="sm" onClick={runScan} disabled={scanning} data-testid="selfhealing-scan">
            <ScanLine className="h-4 w-4 mr-1" />
            {scanning ? "Scanning…" : "Run scan now"}
          </Button>
        }
      />

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <StatCard label="Healers active" value={healers.filter((h) => h.enabled).length} icon={HeartPulse} tone="text-emerald-600" />
        <StatCard label="Auto-fixes applied" value={autoFixed} icon={CheckCircle2} tone="text-blue-600" />
        <StatCard label="Open issues" value={counts.open || 0} icon={Bell} tone="text-amber-600" />
        <StatCard label="Overridden by humans" value={counts.overridden || 0} icon={Undo2} tone="text-purple-600" />
      </div>

      <h2 className="text-lg font-semibold mb-3">Healers</h2>
      {loading ? (
        <div className="text-muted-foreground mb-6">Loading healers…</div>
      ) : (
        <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4 mb-8">
          {healers.map((h) => {
            const Icon = HEALER_ICONS[h.id] || Wrench;
            return (
              <div key={h.id} className="glass-crystal rounded-2xl p-5" data-testid={`healer-${h.id}`}>
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <Icon className="h-5 w-5 text-muted-foreground" />
                    <div className="font-semibold">{h.name}</div>
                  </div>
                  <Badge className={h.enabled ? "bg-emerald-100 text-emerald-900" : "bg-slate-200 text-slate-700"}>
                    {h.enabled ? "On" : "Off"}
                  </Badge>
                </div>
                <p className="text-sm text-muted-foreground mt-2">{h.description}</p>
                <div className="flex items-center justify-between mt-3 text-xs text-muted-foreground">
                  <span>
                    {h.last_run
                      ? `Last run ${new Date(h.last_run).toLocaleString()} · ${h.fixes_applied || 0} fixes`
                      : "Never run"}
                  </span>
                  {canManage && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => toggleHealer(h)}
                      data-testid={`healer-toggle-${h.id}`}
                    >
                      {h.enabled ? "Disable" : "Enable"}
                    </Button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">Issues inbox</h2>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={load}>
            <RefreshCw className="h-4 w-4 mr-1" /> Refresh
          </Button>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="rounded-md border bg-card px-3 py-2 text-sm"
            data-testid="selfhealing-status-filter"
          >
            {STATUS_FILTERS.map((s) => (
              <option key={s} value={s}>
                {s ? s.replace("_", " ") : "All statuses"}
              </option>
            ))}
          </select>
        </div>
      </div>

      {loading ? (
        <div className="text-muted-foreground">Loading issues…</div>
      ) : issues.length === 0 ? (
        <div className="glass-crystal rounded-2xl p-8 text-center text-muted-foreground">
          <CheckCircle2 className="h-6 w-6 mx-auto mb-2" />
          No issues{statusFilter ? ` with status "${statusFilter}"` : ""}. Run a scan to check for problems.
        </div>
      ) : (
        <div className="rounded-xl border overflow-hidden">
          <table className="w-full text-sm" data-testid="selfhealing-table">
            <thead className="bg-muted/60 text-left">
              <tr>
                <th className="px-4 py-2.5 font-medium">Issue</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
                <th className="px-4 py-2.5 font-medium">Auto-fix</th>
                <th className="px-4 py-2.5 font-medium">Detected</th>
                <th className="px-4 py-2.5 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {issues.map((it) => (
                <tr key={it.id} className="border-t hover:bg-muted/30" data-testid={`issue-row-${it.id}`}>
                  <td className="px-4 py-2.5">
                    <div className="font-medium">{it.title}</div>
                    <div className="text-xs text-muted-foreground">{it.detail}</div>
                    {it.override_reason && (
                      <div className="text-xs text-purple-700 mt-1">
                        Override reason: {it.override_reason}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-2.5">
                    <Badge className={STATUS_STYLES[it.status] || STATUS_STYLES.open}>
                      {it.status === "auto_fixed" ? "Auto-fixed" : it.status}
                    </Badge>
                  </td>
                  <td className="px-4 py-2.5 text-xs text-muted-foreground">
                    {it.fix_detail || "—"}
                  </td>
                  <td className="px-4 py-2.5 text-xs text-muted-foreground whitespace-nowrap">
                    {it.created_at ? new Date(it.created_at).toLocaleString() : ""}
                  </td>
                  <td className="px-4 py-2.5">
                    <div className="flex justify-end gap-1">
                      {it.status === "auto_fixed" && (
                        <Button
                          variant="ghost"
                          size="sm"
                          title="Override (reverts auto-fix)"
                          onClick={() => setOverrideFor(it)}
                        >
                          <Undo2 className="h-4 w-4 mr-1" /> Override
                        </Button>
                      )}
                      {it.status !== "resolved" && (
                        <Button variant="ghost" size="sm" title="Mark resolved" onClick={() => resolveIssue(it)}>
                          <CheckCircle2 className="h-4 w-4 mr-1" /> Resolve
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {overrideFor && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md rounded-xl bg-card p-6 shadow-xl">
            <h2 className="text-lg font-semibold mb-1">Override auto-fix</h2>
            <p className="text-sm text-muted-foreground mb-4">
              {overrideFor.title} — the auto-fix will be reverted and the reason logged.
            </p>
            <textarea
              className="w-full rounded-md border bg-background p-2 text-sm min-h-[80px]"
              placeholder="Why is the auto-fix wrong? (required)"
              value={overrideReason}
              onChange={(e) => setOverrideReason(e.target.value)}
              data-testid="selfhealing-override-reason"
            />
            <div className="flex justify-end gap-2 mt-4">
              <Button variant="ghost" onClick={() => { setOverrideFor(null); setOverrideReason(""); }}>
                Cancel
              </Button>
              <Button onClick={submitOverride}>Override &amp; revert</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
