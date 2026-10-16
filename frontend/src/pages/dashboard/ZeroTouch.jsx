import { useCallback, useEffect, useMemo, useState } from "react";
import { api, errMessage } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import PageHeader from "@/components/PageHeader";
import { toast } from "sonner";
import {
  ArrowRight,
  Bot,
  CheckCircle2,
  GitBranch,
  Hand,
  History,
  Play,
  Sparkles,
  Timer,
} from "lucide-react";

const STAGE_ORDER = [
  "lead",
  "booked",
  "dispatched",
  "en_route",
  "on_site",
  "complete",
  "invoiced",
  "paid",
];

const STAGE_LABELS = {
  lead: "Lead",
  booked: "Booked",
  dispatched: "Dispatched",
  en_route: "En route",
  on_site: "On site",
  complete: "Complete",
  invoiced: "Invoiced",
  paid: "Paid",
  canceled: "Canceled",
};

const STAGE_COLORS = {
  lead: "bg-slate-500",
  booked: "bg-sky-500",
  dispatched: "bg-indigo-500",
  en_route: "bg-violet-500",
  on_site: "bg-amber-500",
  complete: "bg-emerald-500",
  invoiced: "bg-teal-500",
  paid: "bg-green-600",
  canceled: "bg-red-500",
};

function StageNode({ stage, isCurrent, isPast, actions }) {
  return (
    <div
      className={`flex flex-col items-center gap-1 rounded-xl border bg-card px-3 py-3 shadow-sm min-w-[104px] ${
        isCurrent ? "border-primary ring-2 ring-primary/30" : "border-border"
      } ${isPast ? "opacity-70" : ""}`}
    >
      <div className={`h-2.5 w-2.5 rounded-full ${STAGE_COLORS[stage] || "bg-slate-400"}`} />
      <div className="text-xs font-semibold whitespace-nowrap">
        {isCurrent && "➤ "}{STAGE_LABELS[stage] || stage}
      </div>
      {actions?.length > 0 && (
        <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
          <Bot className="h-3 w-3" /> {actions.length} auto
        </div>
      )}
    </div>
  );
}

function PipelineFlow({ pipeline }) {
  if (!pipeline) return null;
  const ordered = [...STAGE_ORDER, "canceled"].map(
    (key) => pipeline.stages.find((s) => s.stage === key) || { stage: key, actions: [] }
  );
  return (
    <div className="rounded-xl border border-border bg-card p-4 shadow-sm" data-testid="zt-pipeline">
      <div className="flex items-center gap-2 mb-3">
        <GitBranch className="h-4 w-4" />
        <h3 className="font-semibold">{pipeline.name}</h3>
        <Badge variant="outline">seeded default</Badge>
      </div>
      <div className="flex items-start gap-1 overflow-x-auto pb-2">
        {ordered.map((s, i) => (
          <div key={s.stage} className="flex items-start gap-1 shrink-0">
            <StageNode stage={s.stage} isCurrent={false} isPast={false} actions={s.actions} />
            {i < ordered.length - 1 && (
              <ArrowRight className="h-4 w-4 mt-6 text-muted-foreground shrink-0" />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function KpiCard({ icon: Icon, label, value, sub }) {
  return (
    <div className="rounded-xl border border-border bg-card p-4 shadow-sm">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Icon className="h-4 w-4" /> {label}
      </div>
      <div className="text-2xl font-bold mt-1">{value}</div>
      {sub && <div className="text-xs text-muted-foreground mt-1">{sub}</div>}
    </div>
  );
}

function TimelineFeed({ events, onOverride }) {
  const [jobId, setJobId] = useState("");
  const [stage, setStage] = useState("booked");
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);

  const override = async () => {
    if (!jobId.trim()) return toast.error("Job ID is required");
    if (!reason.trim()) return toast.error("A reason is required for overrides");
    setSaving(true);
    try {
      const r = await api.post(
        `/api/zero-touch/tenants/jobs/${jobId.trim()}/stage/${stage}`,
        { reason: reason.trim() }
      );
      toast.success(`Moved to ${STAGE_LABELS[r.to_stage] || r.to_stage}`);
      setJobId("");
      setReason("");
      onOverride();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="grid md:grid-cols-2 gap-4">
      <div className="rounded-xl border border-border bg-card p-4 shadow-sm">
        <div className="flex items-center gap-2 mb-3">
          <History className="h-4 w-4" />
          <h3 className="font-semibold">Recent transitions</h3>
        </div>
        {events.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No lifecycle activity yet. Advance a job to see its audit trail here.
          </p>
        ) : (
          <ul className="space-y-2 max-h-72 overflow-y-auto" data-testid="zt-feed">
            {events.map((e) => (
              <li key={e.id} className="flex items-start gap-2 text-sm border-b border-border pb-2">
                {e.kind === "override" ? (
                  <Hand className="h-4 w-4 mt-0.5 text-amber-500 shrink-0" />
                ) : e.kind === "action" ? (
                  <Bot className="h-4 w-4 mt-0.5 text-sky-500 shrink-0" />
                ) : (
                  <Play className="h-4 w-4 mt-0.5 text-emerald-500 shrink-0" />
                )}
                <div className="flex-1">
                  <div className="font-medium">
                    {e.kind === "override" ? "Human override" : e.kind === "action" ? e.action : "Auto transition"}
                    {e.to_stage && (
                      <>
                        {" "}
                        <Badge variant="secondary" className="ml-1">
                          {e.from_stage || "—"} → {e.to_stage}
                        </Badge>
                      </>
                    )}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    job {e.job_id} · {new Date(e.created_at).toLocaleString()}
                    {e.reason && ` · “${e.reason}”`}
                    {e.detail && ` · ${e.detail}`}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="rounded-xl border border-border bg-card p-4 shadow-sm">
        <div className="flex items-center gap-2 mb-3">
          <Hand className="h-4 w-4" />
          <h3 className="font-semibold">Human override</h3>
        </div>
        <p className="text-sm text-muted-foreground mb-3">
          Move a job to any stage with a reason. Every override is audit-logged and
          counts against the autonomy score.
        </p>
        <div className="space-y-2">
          <Input
            placeholder="Job ID"
            value={jobId}
            onChange={(e) => setJobId(e.target.value)}
            data-testid="zt-override-job"
          />
          <div className="flex gap-2">
            <select
              value={stage}
              onChange={(e) => setStage(e.target.value)}
              className="flex-1 rounded-md border border-input bg-background px-3 py-2 text-sm"
            >
              {[...STAGE_ORDER, "canceled"].map((s) => (
                <option key={s} value={s}>{STAGE_LABELS[s]}</option>
              ))}
            </select>
          </div>
          <Input
            placeholder="Reason (required)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            data-testid="zt-override-reason"
          />
          <Button onClick={override} disabled={saving} data-testid="zt-override-btn">
            <Hand className="h-4 w-4 mr-1" /> Apply override
          </Button>
        </div>
      </div>
    </div>
  );
}

export default function ZeroTouch() {
  const [pipeline, setPipeline] = useState(null);
  const [stats, setStats] = useState(null);
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [p, a, f] = await Promise.all([
        api.get("/zero-touch/tenants/pipelines"),
        api.get("/zero-touch/tenants/autonomy"),
        api.get("/zero-touch/tenants/events/recent?limit=30"),
      ]);
      setPipeline(p.pipelines?.[0] || null);
      setStats(a);
      setEvents(f.events || []);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const topStageTime = useMemo(() => {
    const m = stats?.avg_seconds_per_stage || {};
    const entries = Object.entries(m);
    if (!entries.length) return null;
    entries.sort((a, b) => b[1] - a[1]);
    return { stage: entries[0][0], secs: entries[0][1] };
  }, [stats]);

  if (loading) return <div className="p-6">Loading zero-touch lifecycle…</div>;

  return (
    <div className="p-6 space-y-4 max-w-6xl mx-auto">
      <PageHeader
        title="Zero-Touch Job Lifecycle"
        subtitle="Lead → paid with minimal human touch. Automations fire at every stage; overrides are always one click away."
        icon={<Sparkles className="h-5 w-5" />}
      />

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <KpiCard
          icon={Bot}
          label="Fully autonomous"
          value={`${stats?.fully_autonomous_pct ?? 0}%`}
          sub={`${stats?.jobs_reached_paid ?? 0} jobs reached paid`}
        />
        <KpiCard
          icon={CheckCircle2}
          label="Auto transitions"
          value={stats?.automatic_transitions ?? 0}
          sub={`${stats?.actions_fired ?? 0} actions fired`}
        />
        <KpiCard
          icon={Hand}
          label="Human overrides"
          value={stats?.human_overrides ?? 0}
          sub="each one is audit-logged"
        />
        <KpiCard
          icon={Timer}
          label="Slowest stage"
          value={topStageTime ? STAGE_LABELS[topStageTime.stage] || topStageTime.stage : "—"}
          sub={topStageTime ? `avg ${Math.round(topStageTime.secs / 60)} min` : "no data yet"}
        />
      </div>

      <PipelineFlow pipeline={pipeline} />

      <TimelineFeed events={events} onOverride={load} />
    </div>
  );
}
