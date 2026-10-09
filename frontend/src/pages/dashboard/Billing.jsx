import { useEffect, useState } from "react";
import { errMessage } from "@/lib/api";
import { api } from "@/lib/api";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";
import { useAuth } from "@/context/AuthContext";
import { startStripeCheckout } from "@/lib/checkout";

export default function Billing() {
  const [plans, setPlans] = useState([]);
  const [tenant, setTenant] = useState(null);
  const [overage, setOverage] = useState(null);
  const [loading, setLoading] = useState("");
  const { user } = useAuth();

  useEffect(() => {
    api.get("/plans").then((r) => setPlans(r.data));
    api.get("/tenants/me").then((r) => setTenant(r.data));
    api.get("/usage/overage").then((r) => setOverage(r.data)).catch(() => {});
  }, []);

  const subscribe = async (planId) => {
    setLoading(planId);
    try {
      await startStripeCheckout(planId);
    } catch (e) {
      toast.error(errMessage(e));
      setLoading("");
    }
  };

  return (
    <div data-testid="billing-page">
      <PageHeader eyebrow="Setup" title="Billing & plans" description="Launch free. Upgrade when you're ready." />
      <div className="surface p-6 mb-6 flex items-center justify-between">
        <div>
          <div className="overline">Current plan</div>
          <div className="font-display text-2xl mt-1">{tenant?.subscription_status === "active" ? "Active" : "Trial"}</div>
          <div className="text-xs text-muted-foreground mt-1">{user?.email}</div>
        </div>
        <Badge variant={tenant?.subscription_status === "active" ? "default" : "secondary"} data-testid="billing-current-badge">{tenant?.subscription_status || "trial"}</Badge>
      </div>

      {overage && overage.total_cents > 0 && (
        <div className="surface p-6 mb-6 border-amber-200 border" data-testid="overage-card">
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="overline">Overage this period · {overage.period}</div>
              <div className="font-display text-3xl mt-1">${(overage.total_cents / 100).toFixed(2)}</div>
              <div className="text-xs text-muted-foreground mt-1">You've gone over your {overage.plan} plan quota — billed on next invoice.</div>
            </div>
            <div className="flex flex-wrap gap-2 justify-end">
              {overage.items.map((it) => (
                <Badge key={it.metric} variant="secondary" className="font-mono text-[11px]">
                  {it.metric}: +{Math.round(it.overage_units)} ({(it.rate_cents).toFixed(1)}¢)
                </Badge>
              ))}
            </div>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        {plans.filter((p) => p.price_cents > 0 && p.key !== "enterprise").map((p) => (
          <div key={p.key} className="surface p-7 lift" data-testid={`plan-card-${p.key}`}>
            <div className="overline">{p.name}</div>
            <div className="font-display text-4xl mt-3 tracking-tight">${(p.price_cents / 100).toFixed(0)}<span className="text-base text-muted-foreground font-sans">/mo</span></div>
            {p.description && <div className="text-sm text-muted-foreground mt-2">{p.description}</div>}
            <ul className="mt-5 text-sm space-y-2 text-muted-foreground">
              {(p.features || []).slice(0, 6).map((f) => <li key={f}>· {f}</li>)}
              {p.limits?.ai_minutes && <li>· {p.limits.ai_minutes} AI minutes/mo</li>}
            </ul>
            <Button className="btn-tenant w-full mt-6" onClick={() => subscribe(p.key)} disabled={loading === p.key} data-testid={`plan-subscribe-${p.key}`}>
              {loading === p.key ? "Redirecting…" : "Subscribe"}
            </Button>
          </div>
        ))}
      </div>

      {plans.find((p) => p.key === "enterprise") && (
        <div className="surface p-7 mt-5" data-testid="plan-card-enterprise">
          <div className="flex items-start justify-between gap-6 flex-wrap">
            <div>
              <div className="overline">Enterprise</div>
              <div className="font-display text-3xl mt-1">Custom pricing</div>
              <div className="text-sm text-muted-foreground mt-2 max-w-xl">Custom AI minutes, locations, users, phone numbers, personas, integrations, and workflows. Fair-use policies apply.</div>
            </div>
            <Button variant="outline" asChild><a href="mailto:sales@aioffice.io">Contact sales</a></Button>
          </div>
        </div>
      )}

      <div className="mt-8 surface p-6 text-sm">
        <div className="overline mb-2">Tax</div>
        <p className="text-muted-foreground">Stripe calculates tax on checkout (+0.5% per transaction). You handle filing & remittance. You can switch to a fully-managed plan later from Settings.</p>
      </div>
    </div>
  );
}
