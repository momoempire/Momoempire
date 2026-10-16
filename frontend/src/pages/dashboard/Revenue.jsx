import { useEffect, useState } from "react";
import { api, errMessage } from "@/lib/api";
import { fmtUSD } from "@/lib/money";
import { useTranslation } from "react-i18next";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { TrendingUp, Loader2, FilePlus, X, CheckCircle2, Wrench } from "lucide-react";

const BASE = "/tenants/revenue";

function scoreTone(score) {
  if (score >= 75) return "bg-red-500";
  if (score >= 50) return "bg-amber-500";
  return "bg-sky-500";
}

function OpportunityRow({ opp, onDrafted, onDismissed, t }) {
  const [busy, setBusy] = useState(false);
  const [dismissing, setDismissing] = useState(false);
  const [reason, setReason] = useState("");

  const draft = async () => {
    setBusy(true);
    try {
      const res = await api.post(`${BASE}/opportunities/${opp.equipment_id}/draft-quote`);
      const q = res.data.quote;
      toast.success(t("revenue.draft_created_toast", { total: fmtUSD(q.total) }));
      onDrafted(opp.equipment_id);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const dismiss = async () => {
    if (!reason.trim()) {
      toast.error(t("revenue.dismiss_reason_required"));
      return;
    }
    setBusy(true);
    try {
      await api.post(`${BASE}/opportunities/${opp.equipment_id}/dismiss`, { reason: reason.trim() });
      toast.success(t("revenue.dismissed_toast"));
      onDismissed(opp.equipment_id);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const label = `${(opp.equipment_type || "equipment").replace(/_/g, " ")}${opp.make ? ` — ${opp.make}${opp.model ? ` ${opp.model}` : ""}` : ""}`;

  return (
    <div className="border rounded-lg p-4 space-y-3" data-testid={`revenue-opportunity-${opp.equipment_id}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-medium truncate">{label}</div>
          <div className="text-sm text-muted-foreground">
            {opp.customer?.name}
            {opp.location_label ? ` · ${opp.location_label}` : ""}
            {` · ${t("revenue.age_years", { n: opp.age_years })} / ${t("revenue.lifespan_years", { n: opp.expected_lifespan_years })}`}
          </div>
        </div>
        <Badge data-testid="revenue-score">{opp.replacement_score}/100</Badge>
      </div>

      <div className="space-y-1">
        <div className="h-2 rounded-full bg-muted overflow-hidden" aria-hidden="true">
          <div className={`h-full ${scoreTone(opp.replacement_score)}`} style={{ width: `${opp.replacement_score}%` }} />
        </div>
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <span className="inline-flex items-center gap-1">
            <Wrench className="h-3 w-3" />
            {t("revenue.repairs_12mo", { n: opp.recent_repairs_12mo })}
          </span>
          <span>{t("revenue.season_factor", { pct: Math.round((opp.season_factor || 0) * 100) })}</span>
          <span className="font-medium text-foreground">{t("revenue.est_value", { v: fmtUSD(opp.estimated_value) })}</span>
        </div>
      </div>

      {opp.suggested_items?.length > 0 && (
        <ul className="text-sm space-y-1">
          {opp.suggested_items.map((it) => (
            <li key={it.id} className="flex justify-between gap-2">
              <span className="truncate">{it.name}</span>
              <span className="text-muted-foreground shrink-0">{fmtUSD(it.price)}</span>
            </li>
          ))}
        </ul>
      )}

      <div className="flex items-center gap-2">
        {opp.quote_drafted ? (
          <Badge variant="secondary">
            <CheckCircle2 className="h-3 w-3 mr-1" />
            {t("revenue.quote_drafted")}
          </Badge>
        ) : (
          <Button size="sm" onClick={draft} disabled={busy} data-testid={`revenue-draft-${opp.equipment_id}`}>
            {busy ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <FilePlus className="h-4 w-4 mr-1" />}
            {t("revenue.draft_quote")}
          </Button>
        )}
        {!dismissing ? (
          <Button variant="ghost" size="sm" onClick={() => setDismissing(true)} data-testid={`revenue-dismiss-${opp.equipment_id}`}>
            <X className="h-4 w-4 mr-1" />
            {t("revenue.dismiss")}
          </Button>
        ) : (
          <div className="flex items-center gap-2 flex-1">
            <Input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder={t("revenue.dismiss_reason_placeholder")}
              className="flex-1"
              data-testid={`revenue-dismiss-reason-${opp.equipment_id}`}
            />
            <Button size="sm" onClick={dismiss} disabled={busy}>{t("revenue.confirm_dismiss")}</Button>
            <Button variant="ghost" size="sm" onClick={() => setDismissing(false)}>{t("revenue.cancel")}</Button>
          </div>
        )}
      </div>
    </div>
  );
}

export default function Revenue() {
  const { t } = useTranslation();
  const [opps, setOpps] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try {
      const res = await api.get(`${BASE}/opportunities`);
      setOpps(res.data.opportunities || []);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const markDrafted = (equipment_id) =>
    setOpps((list) => list.map((o) => (o.equipment_id === equipment_id ? { ...o, quote_drafted: true } : o)));
  const removeOpp = (equipment_id) =>
    setOpps((list) => list.filter((o) => o.equipment_id !== equipment_id));

  const pipelineValue = opps.reduce((s, o) => s + (o.estimated_value || 0), 0);

  return (
    <div className="space-y-6" data-testid="revenue-page">
      <PageHeader
        eyebrow={t("revenue.eyebrow")}
        title={t("revenue.title")}
        description={t("revenue.sub")}
      />

      {!loading && opps.length > 0 && (
        <div className="surface p-4 flex items-center gap-2">
          <TrendingUp className="h-4 w-4 text-muted-foreground" />
          <span className="text-sm">
            {t("revenue.pipeline", { n: opps.length, v: fmtUSD(pipelineValue) })}
          </span>
        </div>
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> {t("revenue.loading")}
        </div>
      ) : opps.length === 0 ? (
        <div className="surface p-8 text-center text-muted-foreground">
          {t("revenue.empty")}
        </div>
      ) : (
        <div className="space-y-4">
          {opps.map((o) => (
            <OpportunityRow key={o.equipment_id} opp={o} onDrafted={markDrafted} onDismissed={removeOpp} t={t} />
          ))}
        </div>
      )}
    </div>
  );
}
