import { useCallback, useEffect, useState } from "react";
import { api, errMessage } from "@/lib/api";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { ChevronLeft, ChevronRight, Truck, MapPin, CheckCircle2, Clock } from "lucide-react";

const STATUS_STYLE = {
  scheduled: "bg-blue-100 text-blue-900",
  confirmed: "bg-emerald-100 text-emerald-900",
  en_route: "bg-orange-100 text-orange-900",
  on_site: "bg-violet-100 text-violet-900",
  completed: "bg-slate-200 text-slate-800",
  canceled: "bg-rose-100 text-rose-900",
};

const STATUS_LABEL = {
  scheduled: "Scheduled",
  confirmed: "Confirmed",
  en_route: "En route",
  on_site: "On site",
  completed: "Completed",
  canceled: "Canceled",
};

// Which status buttons a card shows for its current status.
const NEXT_ACTIONS = {
  scheduled: ["en_route", "on_site"],
  confirmed: ["en_route", "on_site"],
  en_route: ["on_site"],
  on_site: ["completed"],
  completed: [],
  canceled: [],
};

const ACTION_LABEL = { en_route: "En route", on_site: "On site", completed: "Complete" };

function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function JobCard({ job, onStatus, onTimeChange, onDragStart }) {
  const [editingTime, setEditingTime] = useState(false);
  const [timeVal, setTimeVal] = useState("");
  const actions = NEXT_ACTIONS[job.status] || [];

  const beginTimeEdit = () => {
    try {
      const d = new Date(job.start_at);
      const pad = (n) => String(n).padStart(2, "0");
      setTimeVal(
        `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
      );
    } catch {
      setTimeVal("");
    }
    setEditingTime(true);
  };

  return (
    <div
      draggable
      onDragStart={(e) => onDragStart(e, job)}
      className="rounded-lg border bg-card p-3 shadow-sm cursor-grab active:cursor-grabbing"
      data-testid={`dispatch-job-${job.id}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="font-medium text-sm">{job.customer_name || "Unnamed customer"}</div>
        <Badge className={STATUS_STYLE[job.status] || ""}>{STATUS_LABEL[job.status] || job.status}</Badge>
      </div>
      <div className="text-xs text-muted-foreground mt-1">{job.service_name || "Service"}</div>
      <div className="flex items-center gap-1 text-xs mt-1.5">
        <Clock className="h-3 w-3" />
        {editingTime ? (
          <span className="flex items-center gap-1">
            <Input
              type="datetime-local"
              value={timeVal}
              onChange={(e) => setTimeVal(e.target.value)}
              className="h-7 text-xs w-40"
            />
            <Button
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={() => {
                onTimeChange(job, timeVal);
                setEditingTime(false);
              }}
            >
              Set
            </Button>
          </span>
        ) : (
          <button className="underline decoration-dotted" title="Click to reschedule" onClick={beginTimeEdit}>
            {fmtTime(job.start_at)} – {fmtTime(job.end_at)}
          </button>
        )}
      </div>
      {job.customer_phone && <div className="text-xs text-muted-foreground mt-1">{job.customer_phone}</div>}
      {actions.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-2.5">
          {actions.map((s) => (
            <Button
              key={s}
              size="sm"
              variant={s === "completed" ? "default" : "outline"}
              className="h-7 px-2 text-xs"
              onClick={() => onStatus(job, s)}
              data-testid={`dispatch-${s}-${job.id}`}
            >
              {s === "en_route" && <Truck className="h-3 w-3 mr-1" />}
              {s === "on_site" && <MapPin className="h-3 w-3 mr-1" />}
              {s === "completed" && <CheckCircle2 className="h-3 w-3 mr-1" />}
              {ACTION_LABEL[s]}
            </Button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Dispatch() {
  const todayStr = () => new Date().toISOString().slice(0, 10);
  const [date, setDate] = useState(todayStr());
  const [board, setBoard] = useState({ techs: [], unassigned: [] });
  const [loading, setLoading] = useState(true);
  const [dragJob, setDragJob] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get("/tenants/dispatch/board", { params: { date } });
      setBoard(r.data);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  }, [date]);

  useEffect(() => {
    load();
  }, [load]);

  const shiftDay = (n) => {
    const d = new Date(date + "T12:00:00");
    d.setDate(d.getDate() + n);
    setDate(d.toISOString().slice(0, 10));
  };

  // Optimistic local move; reverts on server error.
  const applyLocalMove = (jobId, techId) => {
    setBoard((b) => {
      let job = null;
      const strip = (jobs) => jobs.filter((j) => (j.id === jobId ? ((job = j), false) : true));
      const unassigned = strip(b.unassigned);
      const techs = b.techs.map((t) => ({ ...t, jobs: strip(t.jobs) }));
      if (!job) return b;
      const moved = { ...job, staff_id: techId || "" };
      if (techId) {
        return {
          ...b,
          unassigned,
          techs: techs.map((t) => (t.id === techId ? { ...t, jobs: [...t.jobs, moved] } : t)),
        };
      }
      return { ...b, unassigned: [...unassigned, moved], techs };
    });
  };

  const doMove = async (job, techId) => {
    const prev = board;
    applyLocalMove(job.id, techId);
    try {
      await api.post("/tenants/dispatch/move", { job_id: job.id, tech_id: techId || "" });
      toast.success(techId ? "Job assigned" : "Job unassigned");
    } catch (e) {
      setBoard(prev);
      toast.error(errMessage(e));
    }
  };

  const doStatus = async (job, status) => {
    try {
      await api.post("/tenants/dispatch/status", { job_id: job.id, status });
      toast.success(`Job ${ACTION_LABEL[status].toLowerCase()}`);
      load();
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const doTimeChange = async (job, timeVal) => {
    if (!timeVal) return;
    try {
      await api.post("/tenants/dispatch/move", {
        job_id: job.id,
        start_at: new Date(timeVal).toISOString(),
      });
      toast.success("Job rescheduled");
      load();
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const onDragStart = (e, job) => {
    setDragJob(job);
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", job.id);
  };

  const onDropOnTech = (e, techId) => {
    e.preventDefault();
    if (dragJob && dragJob.staff_id !== techId) doMove(dragJob, techId);
    setDragJob(null);
  };
  const onDropUnassigned = (e) => {
    e.preventDefault();
    if (dragJob && dragJob.staff_id) doMove(dragJob, "");
    setDragJob(null);
  };

  const colClass =
    "rounded-xl border bg-muted/40 p-3 min-w-[260px] w-[260px] flex flex-col gap-2 min-h-[300px]";

  return (
    <div>
      <PageHeader
        title="Dispatch board"
        description="Drag jobs onto a technician to assign them. Click a job's time to reschedule."
        testId="dispatch-header"
        actions={
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => shiftDay(-1)} aria-label="Previous day">
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} className="w-40" />
            <Button variant="outline" size="sm" onClick={() => shiftDay(1)} aria-label="Next day">
              <ChevronRight className="h-4 w-4" />
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setDate(todayStr())}>
              Today
            </Button>
          </div>
        }
      />
      {loading ? (
        <div className="text-muted-foreground">Loading board…</div>
      ) : (
        <div className="flex gap-4 overflow-x-auto pb-4 items-start" data-testid="dispatch-board">
          <div
            className={colClass}
            onDragOver={(e) => e.preventDefault()}
            onDrop={onDropUnassigned}
            data-testid="dispatch-col-unassigned"
          >
            <div className="font-semibold text-sm px-1">
              Unassigned <span className="text-muted-foreground">({board.unassigned.length})</span>
            </div>
            {board.unassigned.map((j) => (
              <JobCard key={j.id} job={j} onStatus={doStatus} onTimeChange={doTimeChange} onDragStart={onDragStart} />
            ))}
            {board.unassigned.length === 0 && (
              <div className="text-xs text-muted-foreground px-1">Nothing waiting.</div>
            )}
          </div>
          {board.techs.map((t) => (
            <div
              key={t.id}
              className={colClass}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => onDropOnTech(e, t.id)}
              data-testid={`dispatch-col-${t.id}`}
            >
              <div className="font-semibold text-sm px-1">
                {t.name} <span className="text-muted-foreground">({t.jobs.length})</span>
              </div>
              {t.jobs.map((j) => (
                <JobCard key={j.id} job={j} onStatus={doStatus} onTimeChange={doTimeChange} onDragStart={onDragStart} />
              ))}
              {t.jobs.length === 0 && (
                <div className="text-xs text-muted-foreground px-1">Drop jobs here.</div>
              )}
            </div>
          ))}
          {board.techs.length === 0 && (
            <div className="text-sm text-muted-foreground">
              No technicians yet — add staff under Scheduling to dispatch jobs.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
