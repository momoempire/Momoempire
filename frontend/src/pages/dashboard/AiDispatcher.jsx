import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errMessage } from "@/lib/api";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { Bot, Check, ChevronLeft, ChevronRight, Clock, Sparkles, User } from "lucide-react";

// Local EN/ES strings (kept in-component so no locale files are touched).
const STR = {
  en: {
    title: "AI Dispatcher",
    description: "The board assigns itself; you approve. Scores are deterministic and explainable.",
    date: "Date",
    today: "Today",
    refresh: "Refresh",
    generate: "Generate suggestions",
    dryRun: "Preview auto-assign",
    applyAll: "Apply all",
    applying: "Applying…",
    unassigned: "Unassigned jobs",
    noSuggestions: "No suggestions for this day — every job is assigned, or nothing is scheduled.",
    score: "Score",
    accept: "Accept",
    accepted: "Accepted",
    why: "Why this tech",
    alternates: "Also considered",
    planTitle: "Auto-assign plan (dry run)",
    planEmpty: "Nothing meets the confidence floor — review jobs manually.",
    noTechs: "No technicians on staff yet — add staff under Scheduling first.",
    rejectedWrite: "Only owners and admins can apply assignments.",
  },
  es: {
    title: "Despachador IA",
    description: "El tablero se asigna solo; usted aprueba. Las puntuaciones son deterministas y explicables.",
    date: "Fecha",
    today: "Hoy",
    refresh: "Actualizar",
    generate: "Generar sugerencias",
    dryRun: "Vista previa de autoasignación",
    applyAll: "Aplicar todo",
    applying: "Aplicando…",
    unassigned: "Trabajos sin asignar",
    noSuggestions: "Sin sugerencias para este día: todo está asignado o no hay trabajos programados.",
    score: "Puntuación",
    accept: "Aceptar",
    accepted: "Aceptado",
    why: "Por qué este técnico",
    alternates: "También considerados",
    planTitle: "Plan de autoasignación (simulación)",
    planEmpty: "Nada alcanza el umbral de confianza: revise los trabajos manualmente.",
    noTechs: "Aún no hay técnicos: agregue personal en Programación primero.",
    rejectedWrite: "Solo propietarios y administradores pueden aplicar asignaciones.",
  },
};

function scoreColor(score) {
  if (score >= 80) return "bg-emerald-500";
  if (score >= 60) return "bg-lime-500";
  if (score >= 40) return "bg-amber-500";
  return "bg-rose-500";
}

function fmtTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function SuggestionCard({ s, t, onAccept, accepted }) {
  return (
    <div className="rounded-xl border bg-card p-4 shadow-sm" data-testid={`ai-dispatch-sug-${s.id}`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="font-medium text-sm">{s.job?.customer_name || "Unnamed customer"}</div>
          <div className="text-xs text-muted-foreground mt-0.5">
            {s.job?.service_name || "Service"}
            {s.job?.start_at && (
              <span className="inline-flex items-center gap-1 ml-2">
                <Clock className="h-3 w-3" />
                {fmtTime(s.job.start_at)}
              </span>
            )}
          </div>
        </div>
        <Badge variant="outline" className="shrink-0">
          {t.score}: {s.score}
        </Badge>
      </div>

      <div className="mt-3 flex items-center gap-2">
        <User className="h-4 w-4 text-muted-foreground" />
        <span className="text-sm font-semibold">{s.tech_name}</span>
        <div className="flex-1 h-2 rounded-full bg-muted overflow-hidden" title={`${t.score}: ${s.score}`}>
          <div className={`h-full rounded-full ${scoreColor(s.score)}`} style={{ width: `${s.score}%` }} />
        </div>
      </div>

      <div className="mt-3 text-xs font-medium text-muted-foreground">{t.why}</div>
      <ul className="mt-1 space-y-1">
        {(s.reasons || []).map((r, i) => (
          <li key={i} className="text-xs flex gap-1.5">
            <Check className="h-3 w-3 mt-0.5 text-emerald-600 shrink-0" />
            <span>{r}</span>
          </li>
        ))}
      </ul>

      {(s.alternates || []).length > 0 && (
        <div className="mt-2 text-xs text-muted-foreground">
          {t.alternates}:{" "}
          {s.alternates.map((a) => `${a.tech_name} (${a.score})`).join(" · ")}
        </div>
      )}

      <div className="mt-3">
        <Button
          size="sm"
          onClick={() => onAccept(s)}
          disabled={accepted}
          data-testid={`ai-dispatch-accept-${s.id}`}
        >
          <Bot className="h-3.5 w-3.5 mr-1.5" />
          {accepted ? t.accepted : t.accept}
        </Button>
      </div>
    </div>
  );
}

export default function AiDispatcher() {
  const { i18n } = useTranslation();
  const t = STR[i18n.language?.startsWith("es") ? "es" : "en"];

  const todayStr = () => new Date().toISOString().slice(0, 10);
  const [date, setDate] = useState(todayStr());
  const [suggestions, setSuggestions] = useState([]);
  const [unassignedCount, setUnassignedCount] = useState(0);
  const [note, setNote] = useState("");
  const [loading, setLoading] = useState(false);
  const [acceptedIds, setAcceptedIds] = useState(new Set());
  const [plan, setPlan] = useState(null);
  const [applying, setApplying] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setPlan(null);
    try {
      const r = await api.get("/tenants/ai-dispatcher/suggestions", { params: { date } });
      setSuggestions(r.data?.suggestions || []);
      setUnassignedCount(r.data?.unassigned_count ?? 0);
      setNote(r.data?.note || "");
      setAcceptedIds(new Set());
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

  const doAccept = async (s) => {
    try {
      await api.post(`/tenants/ai-dispatcher/suggestions/${s.id}/accept`);
      setAcceptedIds((prev) => new Set(prev).add(s.id));
      toast.success(t.accepted);
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const doDryRun = async () => {
    try {
      const r = await api.post("/tenants/ai-dispatcher/auto-assign", { date, dry_run: true });
      setPlan(r.data?.plan || []);
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const doApplyAll = async () => {
    setApplying(true);
    try {
      const r = await api.post("/tenants/ai-dispatcher/auto-assign", { date, dry_run: false });
      toast.success(`${t.accepted}: ${r.data?.writes ?? 0}`);
      load();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setApplying(false);
    }
  };

  return (
    <div>
      <PageHeader
        title={t.title}
        description={t.description}
        testId="ai-dispatcher-header"
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
              {t.today}
            </Button>
            <Button variant="outline" size="sm" onClick={load} disabled={loading}>
              {t.refresh}
            </Button>
            <Button variant="outline" size="sm" onClick={doDryRun}>
              <Sparkles className="h-3.5 w-3.5 mr-1.5" />
              {t.dryRun}
            </Button>
            <Button size="sm" onClick={doApplyAll} disabled={applying || suggestions.length === 0}>
              {applying ? t.applying : t.applyAll}
            </Button>
          </div>
        }
      />

      {plan && (
        <div className="mb-4 rounded-xl border bg-muted/40 p-4" data-testid="ai-dispatcher-plan">
          <div className="font-semibold text-sm mb-2">{t.planTitle}</div>
          {plan.length === 0 ? (
            <div className="text-xs text-muted-foreground">{t.planEmpty}</div>
          ) : (
            <ul className="space-y-1.5">
              {plan.map((p) => (
                <li key={p.job_id} className="text-xs">
                  <span className="font-medium">{p.tech_name}</span> ← job {p.job_id}{" "}
                  <Badge variant="outline" className="ml-1">
                    {p.score}
                  </Badge>
                  <div className="text-muted-foreground">{(p.reasons || []).slice(0, 2).join(" · ")}</div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {loading ? (
        <div className="text-muted-foreground">…</div>
      ) : note ? (
        <div className="text-sm text-muted-foreground">{note || t.noTechs}</div>
      ) : suggestions.length === 0 ? (
        <div className="text-sm text-muted-foreground">{t.noSuggestions}</div>
      ) : (
        <>
          <div className="text-xs text-muted-foreground mb-3">
            {t.unassigned}: {unassignedCount}
          </div>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {suggestions.map((s) => (
              <SuggestionCard key={s.id} s={s} t={t} onAccept={doAccept} accepted={acceptedIds.has(s.id)} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
