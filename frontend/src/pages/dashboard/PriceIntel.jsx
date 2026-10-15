import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errMessage } from "@/lib/api";
import { fmtUSD } from "@/lib/money";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { LineChart, Search, ShieldCheck, TrendingUp, Users } from "lucide-react";

function PositionBadge({ position, t }) {
  if (position === "below")
    return <Badge className="bg-amber-100 text-amber-800 hover:bg-amber-100">{t("price_intel.below_market", "Below market")}</Badge>;
  if (position === "above")
    return <Badge className="bg-sky-100 text-sky-800 hover:bg-sky-100">{t("price_intel.above_market", "Above market")}</Badge>;
  return <Badge className="bg-emerald-100 text-emerald-800 hover:bg-emerald-100">{t("price_intel.at_market", "At market")}</Badge>;
}

function RangeBar({ p25, median, p75, myPrice }) {
  const lo = Math.min(p25, myPrice ?? Infinity);
  const hi = Math.max(p75, myPrice ?? -Infinity);
  const span = hi - lo || 1;
  const pct = (v) => Math.min(100, Math.max(0, ((v - lo) / span) * 100));
  return (
    <div className="mt-3">
      <div className="relative h-2 rounded-full bg-muted">
        <div
          className="absolute h-2 rounded-full bg-primary/60"
          style={{ left: `${pct(p25)}%`, width: `${pct(p75) - pct(p25)}%` }}
        />
        <div
          className="absolute top-1/2 h-4 w-4 -translate-x-1/2 -translate-y-1/2 rounded-full bg-primary ring-2 ring-background"
          style={{ left: `${pct(median)}%` }}
          title="median"
        />
        {myPrice != null && (
          <div
            className="absolute top-1/2 h-4 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded bg-foreground"
            style={{ left: `${pct(myPrice)}%` }}
            title="my price"
          />
        )}
      </div>
      <div className="mt-1 flex justify-between text-xs text-muted-foreground">
        <span>{fmtUSD(p25)}</span>
        <span>{fmtUSD(median)}</span>
        <span>{fmtUSD(p75)}</span>
      </div>
    </div>
  );
}

export default function PriceIntel() {
  const { t } = useTranslation();
  const [optedIn, setOptedIn] = useState(null); // null = unknown (not yet enrolled this session)
  const [itemName, setItemName] = useState("");
  const [trade, setTrade] = useState("");
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [opps, setOpps] = useState([]);
  const [oppsLoading, setOppsLoading] = useState(true);

  const loadOpportunities = () => {
    setOppsLoading(true);
    api
      .get("/tenants/price-intel/opportunities")
      .then((r) => setOpps(r.data.opportunities || []))
      .catch((e) => toast.error(errMessage(e)))
      .finally(() => setOppsLoading(false));
  };

  useEffect(() => {
    loadOpportunities();
  }, []); // eslint-disable-line

  const setConsent = async (join) => {
    try {
      const { data } = await api.post(join ? "/tenants/price-intel/opt-in" : "/tenants/price-intel/opt-out");
      setOptedIn(data.opt_in);
      toast.success(
        data.opt_in
          ? t("price_intel.joined", "You're in the network — your anonymized prices now power benchmarks.")
          : t("price_intel.left", "You've left the network — your prices are excluded from benchmarks.")
      );
      loadOpportunities();
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const lookup = async () => {
    if (!itemName.trim()) return toast.error(t("price_intel.enter_item", "Enter a service name to look up."));
    setLoading(true);
    setResult(null);
    try {
      const { data } = await api.get("/tenants/price-intel/benchmarks", {
        params: { item_name: itemName.trim(), ...(trade.trim() ? { trade: trade.trim() } : {}) },
      });
      setResult(data);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("price_intel.title", "Price intelligence")}
        subtitle={t(
          "price_intel.subtitle",
          "Anonymized benchmarks from shops like yours — see where your prices stand and spot money left on the table."
        )}
        icon={<LineChart className="h-5 w-5" />}
      />

      {/* Consent banner */}
      <div className="flex flex-wrap items-center gap-3 rounded-lg border p-4">
        <ShieldCheck className="h-5 w-5 text-primary" />
        <div className="flex-1 min-w-[220px]">
          <div className="font-medium">{t("price_intel.network_title", "Pricing network")}</div>
          <div className="text-sm text-muted-foreground">
            {t(
              "price_intel.network_blurb",
              "Opt in to contribute your anonymized pricebook prices. Benchmarks only appear with 5+ contributors — individual prices are never exposed."
            )}
          </div>
        </div>
        {optedIn ? (
          <Button variant="outline" onClick={() => setConsent(false)}>
            {t("price_intel.opt_out", "Opt out")}
          </Button>
        ) : (
          <Button onClick={() => setConsent(true)}>
            {t("price_intel.opt_in", "Join the network")}
          </Button>
        )}
      </div>

      {/* Benchmark lookup */}
      <div className="rounded-lg border p-4 space-y-4">
        <div className="font-medium">{t("price_intel.lookup_title", "Benchmark lookup")}</div>
        <div className="flex flex-wrap gap-2">
          <Input
            placeholder={t("price_intel.item_placeholder", "Service name, e.g. Capacitor replacement")}
            value={itemName}
            onChange={(e) => setItemName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && lookup()}
            className="flex-1 min-w-[200px]"
          />
          <Input
            placeholder={t("price_intel.trade_placeholder", "Trade (optional, defaults to yours)")}
            value={trade}
            onChange={(e) => setTrade(e.target.value)}
            className="w-56"
          />
          <Button onClick={lookup} disabled={loading}>
            <Search className="h-4 w-4 mr-1" />
            {loading ? t("price_intel.looking_up", "Looking up…") : t("price_intel.lookup", "Look up")}
          </Button>
        </div>

        {result && result.insufficient_data && (
          <div className="flex items-center gap-2 rounded-md bg-muted p-3 text-sm">
            <Users className="h-4 w-4" />
            {t(
              "price_intel.insufficient",
              "Not enough anonymized data yet for “{{name}}” in {{region}} — benchmarks appear once 5+ opted-in shops contribute.",
              { name: result.item_name, region: result.region || "—" }
            )}
          </div>
        )}

        {result && !result.insufficient_data && (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-3">
              <span className="text-lg font-semibold">{result.item_name}</span>
              <PositionBadge position={result.position} t={t} />
              <span className="text-sm text-muted-foreground">
                <Users className="inline h-3.5 w-3.5 mr-1" />
                {t("price_intel.contributors", "{{n}} shops", { n: result.contributor_count })}
                {result.region ? ` · ${result.region}` : ""}
              </span>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <div className="rounded-md border p-3">
                <div className="text-xs text-muted-foreground">{t("price_intel.my_price", "My price")}</div>
                <div className="text-xl font-semibold">
                  {result.my_price != null ? fmtUSD(result.my_price) : "—"}
                </div>
              </div>
              <div className="rounded-md border p-3">
                <div className="text-xs text-muted-foreground">{t("price_intel.median", "Network median")}</div>
                <div className="text-xl font-semibold">{fmtUSD(result.median)}</div>
              </div>
              <div className="rounded-md border p-3">
                <div className="text-xs text-muted-foreground">{t("price_intel.p25", "25th pct")}</div>
                <div className="text-xl font-semibold">{fmtUSD(result.p25)}</div>
              </div>
              <div className="rounded-md border p-3">
                <div className="text-xs text-muted-foreground">{t("price_intel.p75", "75th pct")}</div>
                <div className="text-xl font-semibold">{fmtUSD(result.p75)}</div>
              </div>
            </div>
            <RangeBar p25={result.p25} median={result.median} p75={result.p75} myPrice={result.my_price} />
          </div>
        )}
      </div>

      {/* Opportunities */}
      <div className="rounded-lg border p-4 space-y-3">
        <div className="flex items-center gap-2">
          <TrendingUp className="h-5 w-5 text-primary" />
          <div className="font-medium">{t("price_intel.opps_title", "Money left on the table")}</div>
        </div>
        <p className="text-sm text-muted-foreground">
          {t(
            "price_intel.opps_blurb",
            "Your services priced more than 15% below the network median."
          )}
        </p>
        {oppsLoading ? (
          <div className="text-sm text-muted-foreground">{t("price_intel.loading", "Loading…")}</div>
        ) : opps.length === 0 ? (
          <div className="text-sm text-muted-foreground">
            {t("price_intel.no_opps", "Nothing here — your prices are at or above the network median. Nice.")}
          </div>
        ) : (
          <div className="divide-y rounded-md border">
            {opps.map((o) => (
              <div key={o.item_name} className="flex flex-wrap items-center gap-3 p-3">
                <div className="flex-1 min-w-[180px]">
                  <div className="font-medium">{o.item_name}</div>
                  <div className="text-sm text-muted-foreground">
                    {t("price_intel.you_charge", "You charge")} {fmtUSD(o.my_price)} ·{" "}
                    {t("price_intel.network_charges", "network median")} {fmtUSD(o.median)}
                  </div>
                </div>
                <Badge className="bg-amber-100 text-amber-800 hover:bg-amber-100">
                  {t("price_intel.gap_badge", "{{pct}}% below median", { pct: o.gap_pct })}
                </Badge>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    setItemName(o.item_name);
                    setTrade("");
                  }}
                >
                  {t("price_intel.view_benchmark", "View benchmark")}
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
