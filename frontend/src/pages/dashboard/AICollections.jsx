import { useEffect, useState } from "react";
import { api, errMessage } from "@/lib/api";
import { useTranslation } from "react-i18next";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import {
  HandCoins, Loader2, HeartHandshake, Megaphone, CalendarClock,
  CheckCircle2, Plus, PhoneOff, MessageSquareHeart,
} from "lucide-react";

const BASE = "/ai-collections";

const TONE_BADGE = {
  friendly: "bg-emerald-100 text-emerald-800",
  firm: "bg-amber-100 text-amber-800",
  "plan-offer": "bg-sky-100 text-sky-800",
  none: "bg-gray-100 text-gray-600",
  pause: "bg-purple-100 text-purple-800",
};

function money(n) {
  return `$${Number(n || 0).toFixed(2)}`;
}

function KpiCard({ label, value, icon: Icon, tint, testid }) {
  return (
    <div className="glass-crystal rounded-2xl p-5" data-testid={testid}>
      <div className="flex items-center gap-3">
        <span className={`p-2 rounded-xl ${tint}`}>
          <Icon className="h-5 w-5" />
        </span>
        <div>
          <div className="text-2xl font-bold">{value}</div>
          <div className="text-sm text-muted-foreground">{label}</div>
        </div>
      </div>
    </div>
  );
}

function OverdueRow({ item, onEnroll, onTouch, t }) {
  return (
    <div
      className="border rounded-xl p-4 flex flex-col md:flex-row md:items-center gap-3"
      data-testid={`aicoll-overdue-${item.invoice_id}`}
    >
      <div className="flex-1 min-w-0">
        <div className="font-medium truncate">
          {item.customer_name || t("aiColl.unknownCustomer")}
        </div>
        <div className="text-sm text-muted-foreground">
          #{item.invoice_number} · {money(item.amount_due)} · {item.days_overdue}{" "}
          {t("aiColl.daysOverdue")} · {item.prior_touches} {t("aiColl.touches")}
        </div>
        {item.dnc && (
          <Badge variant="destructive" className="mt-1">
            <PhoneOff className="h-3 w-3 mr-1" />
            {t("aiColl.dnc")}
          </Badge>
        )}
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <Badge className={TONE_BADGE[item.tone] || TONE_BADGE.none}>
          <MessageSquareHeart className="h-3 w-3 mr-1" />
          {t(`aiColl.tone_${item.tone.replace("-", "_")}`)}
        </Badge>
        <Badge variant="outline">{item.next_action}</Badge>
        <Button size="sm" variant="outline" onClick={() => onEnroll(item)}>
          {t("aiColl.enroll")}
        </Button>
        <Button size="sm" onClick={() => onTouch(item)} disabled={item.tone === "none"}>
          {t("aiColl.newTouch")}
        </Button>
      </div>
    </div>
  );
}

export default function AICollections() {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(true);
  const [overdue, setOverdue] = useState([]);
  const [sequences, setSequences] = useState([]);
  const [kpis, setKpis] = useState(null);
  const [touches, setTouches] = useState([]);
  const [seqName, setSeqName] = useState("");
  const [tab, setTab] = useState("queue");

  const load = async () => {
    setLoading(true);
    try {
      const [o, s, d, tt] = await Promise.all([
        api.get(`${BASE}/tenants/overdue`),
        api.get(`${BASE}/tenants/sequences`),
        api.get(`${BASE}/tenants/dashboard`),
        api.get(`${BASE}/tenants/touches`),
      ]);
      setOverdue(o.data.items);
      setSequences(s.data.items);
      setKpis(d.data);
      setTouches(tt.data.items);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const createSequence = async () => {
    if (!seqName.trim()) return toast.error(t("aiColl.nameRequired"));
    try {
      await api.post(`${BASE}/tenants/sequences`, {
        name: seqName,
        description: t("aiColl.defaultSeqDesc"),
        steps: [
          { wait_days: 1, channel: "sms", tone: "friendly" },
          { wait_days: 7, channel: "sms", tone: "firm" },
          { wait_days: 14, channel: "email", tone: "plan-offer" },
        ],
        active: true,
      });
      setSeqName("");
      toast.success(t("aiColl.sequenceCreated"));
      load();
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const enroll = async (item) => {
    const seq = sequences[0];
    if (!seq) return toast.error(t("aiColl.createSequenceFirst"));
    try {
      await api.post(`${BASE}/tenants/sequences/${seq.id}/enroll`, {
        sequence_id: seq.id,
        invoice: { invoice_id: item.invoice_id },
      });
      toast.success(t("aiColl.enrolled"));
      load();
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const newTouch = async (item) => {
    try {
      const r = await api.post(`${BASE}/tenants/touches`, {
        invoice_id: item.invoice_id,
        channel: item.channel === "voice" && item.voice_consent ? "voice" : "sms",
      });
      toast.success(t("aiColl.touchPreviewed"));
      setTouches([r.data, ...touches]);
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const logOutcome = async (touchId, outcome) => {
    try {
      await api.post(`${BASE}/tenants/touches/${touchId}/log`, { outcome });
      toast.success(t("aiColl.logged"));
      load();
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  return (
    <div className="space-y-6" data-testid="aicoll-page">
      <PageHeader
        icon={HandCoins}
        title={t("aiColl.title")}
        subtitle={t("aiColl.subtitle")}
      />

      {loading && (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> {t("aiColl.loading")}
        </div>
      )}

      {kpis && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <KpiCard label={t("aiColl.kpiCollected")} value={money(kpis.collected)}
            icon={CheckCircle2} tint="bg-emerald-500/15" testid="aicoll-kpi-collected" />
          <KpiCard label={t("aiColl.kpiPromised")} value={kpis.promised_count}
            icon={CalendarClock} tint="bg-sky-500/15" testid="aicoll-kpi-promised" />
          <KpiCard label={t("aiColl.kpiAtRisk")} value={money(kpis.at_risk)}
            icon={Megaphone} tint="bg-amber-500/15" testid="aicoll-kpi-atrisk" />
          <KpiCard label={t("aiColl.kpiAvgDays")} value={kpis.avg_days_to_pay ?? "—"}
            icon={HeartHandshake} tint="bg-purple-500/15" testid="aicoll-kpi-avgdays" />
        </div>
      )}

      <div className="flex gap-2">
        {["queue", "sequences", "timeline"].map((k) => (
          <Button
            key={k}
            variant={tab === k ? "default" : "outline"}
            size="sm"
            onClick={() => setTab(k)}
            data-testid={`aicoll-tab-${k}`}
          >
            {t(`aiColl.tab_${k}`)}
          </Button>
        ))}
      </div>

      {tab === "queue" && (
        <div className="space-y-3" data-testid="aicoll-queue">
          {overdue.length === 0 && !loading && (
            <p className="text-muted-foreground">{t("aiColl.noOverdue")}</p>
          )}
          {overdue.map((item) => (
            <OverdueRow key={item.invoice_id} item={item} onEnroll={enroll} onTouch={newTouch} t={t} />
          ))}
        </div>
      )}

      {tab === "sequences" && (
        <div className="space-y-4" data-testid="aicoll-sequences">
          <div className="glass-crystal rounded-2xl p-5 flex gap-2 items-center">
            <Input
              value={seqName}
              onChange={(e) => setSeqName(e.target.value)}
              placeholder={t("aiColl.sequenceNamePlaceholder")}
              data-testid="aicoll-seq-name"
            />
            <Button onClick={createSequence} data-testid="aicoll-seq-create">
              <Plus className="h-4 w-4 mr-1" /> {t("aiColl.createSequence")}
            </Button>
          </div>
          {sequences.map((s) => (
            <div key={s.id} className="border rounded-xl p-4" data-testid={`aicoll-seq-${s.id}`}>
              <div className="font-medium">{s.name}</div>
              <div className="text-sm text-muted-foreground mt-1">
                {s.steps.map((st, i) => (
                  <span key={i} className="mr-3">
                    {t("aiColl.dayN", { n: st.wait_days })} · {st.channel} ·{" "}
                    {t(`aiColl.tone_${st.tone.replace("-", "_")}`)}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {tab === "timeline" && (
        <div className="space-y-3" data-testid="aicoll-timeline">
          {touches.length === 0 && <p className="text-muted-foreground">{t("aiColl.noTouches")}</p>}
          {touches.map((tc) => (
            <div key={tc.id} className="border rounded-xl p-4" data-testid={`aicoll-touch-${tc.id}`}>
              <div className="flex items-center gap-2 flex-wrap">
                <Badge className={TONE_BADGE[tc.tone] || TONE_BADGE.none}>{tc.tone}</Badge>
                <Badge variant="outline">{tc.channel}</Badge>
                {tc.outcome && <Badge variant="secondary">{tc.outcome}</Badge>}
                <span className="text-xs text-muted-foreground ml-auto">
                  {new Date(tc.created_at).toLocaleString()}
                </span>
              </div>
              <p className="text-sm mt-2 whitespace-pre-wrap text-muted-foreground">{tc.template}</p>
              {!tc.outcome && (
                <div className="flex gap-2 mt-3 flex-wrap">
                  {["paid", "promised-to-pay", "dispute", "dnc"].map((o) => (
                    <Button
                      key={o}
                      size="sm"
                      variant="outline"
                      onClick={() => logOutcome(tc.id, o)}
                      data-testid={`aicoll-log-${tc.id}-${o}`}
                    >
                      {t(`aiColl.outcome_${o.replace(/-/g, "_")}`)}
                    </Button>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
