import { useEffect, useMemo, useState } from "react";
import { api, errMessage } from "@/lib/api";
import { fmtUSD } from "@/lib/money";
import { useAuth } from "@/context/AuthContext";
import { useTranslation } from "react-i18next";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { Search, X, LayoutGrid } from "lucide-react";

const K = "trade_packs.";

function Section({ title, children }) {
  return (
    <div className="mt-5">
      <div className="text-sm font-semibold mb-2">{title}</div>
      {children}
    </div>
  );
}

function DetailModal({ pack, onClose, onApply, applying, canApply }) {
  const { t } = useTranslation();
  const [scriptOpen, setScriptOpen] = useState(false);
  if (!pack) return null;
  const script = pack.receptionist_script || "";
  const scriptPreview = scriptOpen || script.length < 400 ? script : script.slice(0, 400) + "…";
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" data-testid="tradepack-detail">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="relative bg-background rounded-lg shadow-xl max-w-3xl w-full max-h-[85vh] overflow-y-auto p-6">
        <button className="absolute top-4 right-4 text-muted-foreground hover:text-foreground" onClick={onClose} aria-label={t(K + "close")}>
          <X className="h-5 w-5" />
        </button>
        <div className="flex items-center gap-3 pr-8">
          <span className="h-10 w-10 rounded-md shrink-0" style={{ backgroundColor: pack.colors?.accent || "#4F46E5" }} />
          <div>
            <h2 className="text-xl font-bold">{pack.trade}</h2>
            <p className="text-sm text-muted-foreground">{pack.tagline}</p>
          </div>
        </div>

        <Section title={t(K + "pricebook_section", { n: pack.pricebook?.length || 0 })}>
          <div className="border rounded-md divide-y max-h-64 overflow-y-auto">
            {(pack.pricebook || []).map((item, i) => (
              <div key={i} className="flex items-center justify-between px-3 py-2 gap-3">
                <div className="min-w-0">
                  <div className="text-sm font-medium truncate">{item.name}</div>
                  <div className="text-xs text-muted-foreground truncate">{item.description}</div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <Badge variant="outline">{item.category}</Badge>
                  <span className="text-sm font-semibold">{fmtUSD(item.price)}</span>
                </div>
              </div>
            ))}
          </div>
        </Section>

        <Section title={t(K + "appointment_types_section")}>
          <div className="flex flex-wrap gap-2">
            {(pack.appointment_types || []).map((a, i) => (
              <Badge key={i} variant="secondary" title={a.description}>
                {a.name} · {a.duration_min} {t(K + "min")}
              </Badge>
            ))}
          </div>
        </Section>

        <Section title={t(K + "script_section")}>
          <p className="text-sm whitespace-pre-wrap text-muted-foreground">{scriptPreview}</p>
          {script.length >= 400 && (
            <Button variant="link" size="sm" className="px-0" onClick={() => setScriptOpen((v) => !v)}>
              {scriptOpen ? t(K + "show_less") : t(K + "show_more")}
            </Button>
          )}
        </Section>

        <div className="mt-5 text-sm text-muted-foreground">
          {t(K + "counts", {
            triage: pack.triage?.length || 0,
            auto: pack.automation_templates?.length || 0,
            review: pack.review_templates?.length || 0,
            equipment: pack.equipment_types?.length || 0,
          })}
        </div>

        <div className="mt-6 flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>{t(K + "close")}</Button>
          {canApply && (
            <Button onClick={() => onApply(pack)} disabled={applying} data-testid="tradepack-apply">
              {applying ? t(K + "applying") : t(K + "apply")}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

export default function TradePacks() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [packs, setPacks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [applying, setApplying] = useState(false);

  const canApply = ["owner", "admin"].includes(user?.role);

  useEffect(() => {
    let alive = true;
    api
      .get("/tenants/trade-packs")
      .then((r) => alive && setPacks(r.data?.packs || []))
      .catch((e) => toast.error(errMessage(e)))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return packs;
    return packs.filter(
      (p) => p.trade?.toLowerCase().includes(q) || p.tagline?.toLowerCase().includes(q)
    );
  }, [packs, query]);

  const openDetail = async (summary) => {
    setDetailLoading(true);
    try {
      const r = await api.get(`/tenants/trade-packs/${summary.id}`);
      setDetail(r.data);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setDetailLoading(false);
    }
  };

  const applyPack = async (pack) => {
    setApplying(true);
    try {
      const r = await api.post(`/tenants/trade-packs/${pack.id}/apply`);
      const { applied, skipped } = r.data || {};
      toast.success(t(K + "applied_success", { applied: applied ?? 0, skipped: skipped ?? 0 }));
      setDetail(null);
    } catch (e) {
      toast.error(t(K + "apply_error") + ": " + errMessage(e));
    } finally {
      setApplying(false);
    }
  };

  return (
    <div className="p-6 max-w-7xl mx-auto" data-testid="tradepacks-page">
      <PageHeader title={t(K + "title")} subtitle={t(K + "subtitle")} icon={<LayoutGrid className="h-5 w-5" />} />
      <div className="relative max-w-sm mt-4">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <Input
          className="pl-9"
          placeholder={t(K + "search_placeholder")}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          data-testid="tradepacks-search"
        />
      </div>

      {loading ? (
        <div className="mt-8 text-muted-foreground">{t("common.loading")}</div>
      ) : filtered.length === 0 ? (
        <div className="mt-8 text-muted-foreground">{t(K + "no_results")}</div>
      ) : (
        <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3" data-testid="tradepacks-grid">
          {filtered.map((p) => (
            <button
              key={p.id}
              onClick={() => openDetail(p)}
              className="text-left rounded-lg border bg-card p-5 hover:shadow-md transition-shadow focus:outline-none focus:ring-2 focus:ring-ring"
              data-testid={`tradepack-card-${p.id}`}
            >
              <div className="h-2 rounded-full mb-4" style={{ backgroundColor: p.accent || "#4F46E5" }} />
              <div className="font-semibold text-lg">{p.trade}</div>
              <p className="text-sm text-muted-foreground mt-1 line-clamp-2">{p.tagline}</p>
              <div className="mt-3">
                {p.valid === false ? (
                  <Badge variant="destructive" onClick={(e) => e.stopPropagation()}>{t(K + "invalid")}</Badge>
                ) : (
                  <span className="text-sm text-primary font-medium">{t(K + "view")}</span>
                )}
              </div>
            </button>
          ))}
        </div>
      )}

      {(detail || detailLoading) && (
        <DetailModal
          pack={detail}
          onClose={() => setDetail(null)}
          onApply={applyPack}
          applying={applying}
          canApply={canApply}
        />
      )}
    </div>
  );
}
