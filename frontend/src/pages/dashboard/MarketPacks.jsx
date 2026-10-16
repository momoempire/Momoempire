import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errMessage } from "@/lib/api";
import { toast } from "sonner";
import { Globe2, Languages, Palette } from "lucide-react";

export default function MarketPacks() {
  const { t } = useTranslation();
  const [packs, setPacks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [trade, setTrade] = useState("hvac");
  const [market, setMarket] = useState("de");
  const [face, setFace] = useState(null);
  const [resolving, setResolving] = useState(false);

  useEffect(() => {
    api.get("/tenants/market-packs")
      .then((r) => setPacks(r.data?.packs || []))
      .catch((e) => toast.error(errMessage(e)))
      .finally(() => setLoading(false));
  }, []);

  const resolve = async () => {
    setResolving(true);
    try {
      const r = await api.get("/tenants/market-packs/resolve", { params: { trade, market } });
      setFace(r.data);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setResolving(false);
    }
  };

  return (
    <div className="p-6 max-w-7xl mx-auto" data-testid="market-packs-page">
      <h1 className="text-2xl font-bold flex items-center gap-2">
        <Globe2 className="h-6 w-6" /> {t("marketPacks.title", "Market packs")}
      </h1>
      <p className="text-muted-foreground mt-1">
        {t("marketPacks.description", "46 Stripe-supported markets. Pick a trade + a market to preview the combined face.")}
      </p>

      <div className="mt-6 grid grid-cols-1 md:grid-cols-3 gap-4">
        <div>
          <label className="text-sm font-medium">{t("marketPacks.trade", "Trade")}</label>
          <input
            className="mt-1 w-full border rounded px-3 py-2 bg-background"
            value={trade} onChange={(e) => setTrade(e.target.value)}
            placeholder="hvac" data-testid="market-packs-trade"
          />
        </div>
        <div>
          <label className="text-sm font-medium">{t("marketPacks.market", "Market")}</label>
          <input
            className="mt-1 w-full border rounded px-3 py-2 bg-background"
            value={market} onChange={(e) => setMarket(e.target.value)}
            placeholder="de" data-testid="market-packs-market"
          />
        </div>
        <div className="flex items-end">
          <button
            onClick={resolve} disabled={resolving}
            className="px-4 py-2 rounded bg-primary text-primary-foreground disabled:opacity-50"
            data-testid="market-packs-resolve"
          >
            {resolving ? t("marketPacks.resolving", "Resolving…") : t("marketPacks.resolve", "Preview face")}
          </button>
        </div>
      </div>

      {face && (
        <div className="mt-6 border rounded-lg p-5" style={{ borderTop: `4px solid ${face.colors?.accent || "#4F46E5"}` }} data-testid="market-packs-face">
          <div className="text-3xl">{face.flag}</div>
          <div className="mt-2 font-semibold">{face.trade} · {face.market?.name}</div>
          <div className="mt-2 flex flex-wrap gap-3 text-sm text-muted-foreground">
            <span className="flex items-center gap-1"><Palette className="h-4 w-4" /> {face.colors?.accent}</span>
            <span className="flex items-center gap-1"><Languages className="h-4 w-4" /> {(face.languages || []).join(", ")}</span>
          </div>
          <div className="mt-3 text-sm">{face.tagline}</div>
          <div className="mt-2 text-sm text-muted-foreground">
            {t("marketPacks.pricebook_items", "{{n}} pricebook items", { n: (face.pricebook || []).length })}
          </div>
        </div>
      )}

      <h2 className="mt-8 text-lg font-semibold">
        {t("marketPacks.all_markets", "All markets ({{n}})", { n: packs.length })}
      </h2>
      {loading ? (
        <p className="mt-3 text-muted-foreground">{t("marketPacks.loading", "Loading markets…")}</p>
      ) : (
        <div className="mt-3 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          {packs.map((p) => (
            <div key={p.id} className="border rounded-lg p-3" style={{ borderTop: `3px solid ${p.accent}` }} data-testid={`market-pack-${p.id}`}>
              <div className="text-2xl">{p.flag}</div>
              <div className="mt-1 text-sm font-medium truncate">{p.name}</div>
              <div className="text-xs text-muted-foreground">{(p.languages || []).join(" · ")}</div>
              {p.extended_network && (
                <div className="text-[10px] mt-1 text-amber-600">{t("marketPacks.extended", "Extended network")}</div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
