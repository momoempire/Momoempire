import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { fmtUSD } from "@/lib/money";
import { Badge } from "@/components/ui/badge";

// "Financing options" section for the public quote/invoice pages.
// Backend: GET /api/public/financing/offers?token=... (token-authenticated,
// demo math by default, clearly labeled). Renders nothing when financing is
// disabled or unavailable for the token.
export default function FinancingOptions({ token }) {
  const { t } = useTranslation();
  const [data, setData] = useState(null);
  const [term, setTerm] = useState(null);

  useEffect(() => {
    if (!token) return;
    api.get("/public/financing/offers", { params: { token } })
      .then((r) => {
        setData(r.data);
        const offers = r.data?.offers || [];
        const longest = offers.length ? Math.max(...offers.map((o) => o.term_months)) : null;
        setTerm(longest);
      })
      .catch(() => setData({ offers: [] })); // disabled / unknown token → hide
  }, [token]);

  if (!data || !data.offers || data.offers.length === 0) return null;
  const selected = data.offers.find((o) => o.term_months === term) || data.offers[0];
  const longest = Math.max(...data.offers.map((o) => o.term_months));
  const lowest = data.offers.reduce((a, b) => (a.monthly_payment_cents < b.monthly_payment_cents ? a : b));

  return (
    <section className="mt-6 rounded-xl border bg-muted/40 p-4" data-testid="financing-options" aria-label={t("financing.title")}>
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold">{t("financing.title")}</h2>
        {data.demo && <Badge variant="secondary" data-testid="financing-demo-badge">{t("financing.demo_badge")}</Badge>}
      </div>
      <p className="mt-2 text-lg font-semibold" data-testid="financing-headline">
        {t("financing.as_low_as", { amount: fmtUSD(lowest.monthly_payment_cents / 100), months: longest })}
      </p>
      <div className="mt-3 flex flex-wrap gap-2" role="group" aria-label={t("financing.term_label")}>
        {data.offers.map((o) => (
          <button
            key={o.term_months}
            type="button"
            onClick={() => setTerm(o.term_months)}
            data-testid={`financing-term-${o.term_months}`}
            className={`rounded-lg border px-3 py-1.5 text-sm ${o.term_months === selected.term_months ? "border-foreground bg-foreground text-background" : "border-border hover:bg-muted"}`}
            aria-pressed={o.term_months === selected.term_months}
          >
            {t("financing.months", { n: o.term_months })}
          </button>
        ))}
      </div>
      <p className="mt-2 text-sm text-muted-foreground" data-testid="financing-selected">
        {t("financing.per_month", { amount: fmtUSD(selected.monthly_payment_cents / 100) })}
        {" · "}
        {t("financing.apr", { rate: selected.apr_percent.toFixed(2) })}
      </p>
      {data.demo && <p className="mt-2 text-xs text-muted-foreground">{t("financing.demo_note")}</p>}
    </section>
  );
}
