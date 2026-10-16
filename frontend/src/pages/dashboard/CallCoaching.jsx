import { useEffect, useState } from "react";
import { api, errMessage } from "@/lib/api";
import { useTranslation } from "react-i18next";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { Headset, Loader2, PhoneMissed, CheckCircle2, ChevronRight, Lightbulb } from "lucide-react";

const BASE = "/call-coaching";

const RUBRIC_KEYS = ["greeting", "needs_discovery", "same_day_offer", "close", "empathy"];

function scoreTone(score) {
  if (score >= 80) return "bg-emerald-500";
  if (score >= 60) return "bg-sky-500";
  if (score >= 40) return "bg-amber-500";
  return "bg-red-500";
}

function scoreBadgeTone(score) {
  if (score >= 80) return "bg-emerald-100 text-emerald-800";
  if (score >= 60) return "bg-sky-100 text-sky-800";
  if (score >= 40) return "bg-amber-100 text-amber-800";
  return "bg-red-100 text-red-800";
}

function RubricBar({ label, score }) {
  return (
    <div className="space-y-1" data-testid={`coaching-rubric-${label}`}>
      <div className="flex justify-between text-sm">
        <span className="text-muted-foreground">{label}</span>
        <span className="font-medium">{score}/100</span>
      </div>
      <div className="h-2 rounded-full bg-muted overflow-hidden" aria-hidden="true">
        <div className={`h-full ${scoreTone(score)}`} style={{ width: `${score}%` }} />
      </div>
    </div>
  );
}

function CallRow({ call, selected, onSelect, t }) {
  return (
    <button
      type="button"
      onClick={() => onSelect(call)}
      className={`w-full text-left border rounded-lg p-4 flex items-center justify-between gap-3 hover:border-primary/50 transition-colors ${
        selected ? "border-primary bg-primary/5" : ""
      }`}
      data-testid={`coaching-call-${call.call_id}`}
    >
      <div className="min-w-0">
        <div className="font-medium truncate">
          {call.csr_name || t("call_coaching.unassigned")}
        </div>
        <div className="text-sm text-muted-foreground truncate">
          {call.transcript_summary}
        </div>
        <div className="text-xs text-muted-foreground mt-1">
          {new Date(call.occurred_at).toLocaleString()}
        </div>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {call.missed_booking && (
          <Badge variant="destructive" data-testid="coaching-missed-badge">
            <PhoneMissed className="h-3 w-3 mr-1" />
            {t("call_coaching.missed")}
          </Badge>
        )}
        {call.booked && (
          <Badge variant="secondary">
            <CheckCircle2 className="h-3 w-3 mr-1" />
            {t("call_coaching.booked")}
          </Badge>
        )}
        <span
          className={`inline-flex items-center px-2 py-0.5 rounded-full text-sm font-semibold ${scoreBadgeTone(call.overall)}`}
          data-testid="coaching-overall-score"
        >
          {call.overall}
        </span>
        <ChevronRight className="h-4 w-4 text-muted-foreground" />
      </div>
    </button>
  );
}

function CallDetail({ call, t }) {
  if (!call) {
    return (
      <div className="surface p-8 text-center text-muted-foreground">
        {t("call_coaching.select_hint")}
      </div>
    );
  }
  return (
    <div className="surface p-5 space-y-5" data-testid={`coaching-detail-${call.call_id}`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="font-semibold text-lg">
            {t("call_coaching.detail_title", { csr: call.csr_name || t("call_coaching.unassigned") })}
          </div>
          <div className="text-sm text-muted-foreground">
            {new Date(call.occurred_at).toLocaleString()}
          </div>
        </div>
        <span className={`inline-flex items-center px-3 py-1 rounded-full text-base font-bold ${scoreBadgeTone(call.overall)}`}>
          {call.overall}/100
        </span>
      </div>

      {call.missed_booking && (
        <div className="rounded-lg bg-red-50 border border-red-200 p-3 text-sm text-red-800">
          <span className="font-semibold">{t("call_coaching.missed_booking")}:</span>{" "}
          {call.missed_booking_reason}
        </div>
      )}

      <div className="space-y-3">
        {RUBRIC_KEYS.map((k) => (
          <RubricBar key={k} label={t(`call_coaching.rubric_${k}`)} score={call.rubric[k]} />
        ))}
      </div>

      <div>
        <div className="text-sm font-medium mb-1">{t("call_coaching.summary")}</div>
        <p className="text-sm text-muted-foreground">{call.transcript_summary}</p>
      </div>

      {call.transcript && (
        <details className="text-sm">
          <summary className="cursor-pointer font-medium">{t("call_coaching.transcript")}</summary>
          <p className="mt-2 whitespace-pre-wrap text-muted-foreground">{call.transcript}</p>
        </details>
      )}
    </div>
  );
}

function InsightsPanel({ insights, t }) {
  if (!insights) return null;
  return (
    <div className="surface p-5 space-y-4" data-testid="coaching-insights">
      <div className="flex items-start gap-2">
        <Lightbulb className="h-4 w-4 mt-0.5 text-amber-500 shrink-0" />
        <p className="font-medium">{insights.headline}</p>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-center">
        {[
          [t("call_coaching.calls_scored"), insights.total_calls],
          [t("call_coaching.avg_score"), insights.avg_overall],
          [t("call_coaching.bookings_lost"), insights.missed_bookings],
          [t("call_coaching.booked"), insights.booked],
        ].map(([label, value]) => (
          <div key={label} className="border rounded-lg p-3">
            <div className="text-2xl font-bold">{value}</div>
            <div className="text-xs text-muted-foreground">{label}</div>
          </div>
        ))}
      </div>

      {insights.opportunities?.length > 0 && (
        <div className="space-y-2">
          <div className="text-sm font-semibold">{t("call_coaching.coaching_opportunities")}</div>
          {insights.opportunities.map((o) => (
            <div key={o.csr_name} className="border rounded-lg p-3 text-sm" data-testid={`coaching-opp-${o.csr_name}`}>
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium">{o.csr_name}</span>
                <span className="text-muted-foreground">
                  {t("call_coaching.opp_stats", { n: o.calls, score: o.avg_score, lost: o.missed_bookings })}
                </span>
              </div>
              <div className="mt-1 text-muted-foreground">
                {t("call_coaching.weakest", { rubric: t(`call_coaching.rubric_${o.weakest_rubric}`), score: o.weakest_rubric_avg })}
              </div>
              <div className="mt-1">{o.coaching_tip}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function CallCoaching() {
  const { t } = useTranslation();
  const [calls, setCalls] = useState([]);
  const [insights, setInsights] = useState(null);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [csr, setCsr] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      const params = {};
      if (dateFrom) params.date_from = `${dateFrom}T00:00:00Z`;
      if (dateTo) params.date_to = `${dateTo}T23:59:59Z`;
      if (csr.trim()) params.csr = csr.trim();
      const [callsRes, insightsRes] = await Promise.all([
        api.get(`${BASE}/calls`, { params }),
        api.get(`${BASE}/insights/weekly`),
      ]);
      setCalls(callsRes.data.calls || []);
      setInsights(insightsRes.data);
      setSelectedId(null);
      setDetail(null);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const selectCall = async (call) => {
    setSelectedId(call.call_id);
    setDetailLoading(true);
    try {
      const res = await api.get(`${BASE}/calls/${call.call_id}`);
      setDetail(res.data);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setDetailLoading(false);
    }
  };

  const selected = calls.find((c) => c.call_id === selectedId) || null;

  return (
    <div className="space-y-6" data-testid="call-coaching-page">
      <PageHeader
        eyebrow={t("call_coaching.eyebrow")}
        title={t("call_coaching.title")}
        description={t("call_coaching.sub")}
      />

      <InsightsPanel insights={insights} t={t} />

      <div className="flex flex-wrap items-end gap-3" data-testid="coaching-filters">
        <label className="text-sm space-y-1">
          <span className="block text-muted-foreground">{t("call_coaching.from")}</span>
          <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
        </label>
        <label className="text-sm space-y-1">
          <span className="block text-muted-foreground">{t("call_coaching.to")}</span>
          <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
        </label>
        <label className="text-sm space-y-1">
          <span className="block text-muted-foreground">{t("call_coaching.csr")}</span>
          <Input
            value={csr}
            onChange={(e) => setCsr(e.target.value)}
            placeholder={t("call_coaching.csr_placeholder")}
            className="w-40"
          />
        </label>
        <Button size="sm" onClick={load} disabled={loading}>
          {loading ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Headset className="h-4 w-4 mr-1" />}
          {t("call_coaching.apply")}
        </Button>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> {t("call_coaching.loading")}
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="space-y-3">
            {calls.length === 0 ? (
              <div className="surface p-8 text-center text-muted-foreground">
                {t("call_coaching.empty")}
              </div>
            ) : (
              calls.map((c) => (
                <CallRow key={c.call_id} call={c} selected={c.call_id === selectedId} onSelect={selectCall} t={t} />
              ))
            )}
          </div>
          <div>
            {detailLoading ? (
              <div className="flex items-center gap-2 text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" /> {t("call_coaching.loading")}
              </div>
            ) : (
              <CallDetail call={detail || selected} t={t} />
            )}
          </div>
        </div>
      )}
    </div>
  );
}
