import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api, errMessage } from "@/lib/api";
import { fmtUSD, lineTotal } from "@/lib/money";
import { Button } from "@/components/ui/button";
import { CheckCircle2, Loader2 } from "lucide-react";

// Public "Pay online" page for an invoice sent to a customer.
// Backend: GET /api/public/c2p/invoices/:token, POST .../pay (Stripe PaymentIntent on the
// business's connected account). Stripe.js is loaded from js.stripe.com only when paying.
function loadStripeJs() {
  if (window.Stripe) return Promise.resolve(window.Stripe);
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = "https://js.stripe.com/v3";
    s.async = true;
    s.onload = () => (window.Stripe ? resolve(window.Stripe) : reject(new Error("Stripe.js failed to load")));
    s.onerror = () => reject(new Error("Stripe.js failed to load"));
    document.head.appendChild(s);
  });
}

export default function PublicInvoice() {
  const { token } = useParams();
  const [inv, setInv] = useState(null);
  const [error, setError] = useState("");
  const [phase, setPhase] = useState("idle"); // idle | preparing | card | paying | done | unavailable
  const stripeRef = useRef(null);
  const elementsRef = useRef(null);
  const mountRef = useRef(null);

  useEffect(() => {
    api.get(`/public/c2p/invoices/${token}`)
      .then((r) => setInv(r.data))
      .catch((e) => setError(e?.response?.status === 404 ? "This invoice link is invalid or has expired." : errMessage(e)));
  }, [token]);

  const startPay = async () => {
    setError("");
    setPhase("preparing");
    try {
      const { data } = await api.post(`/public/c2p/invoices/${token}/pay`, {});
      if (!data?.client_secret || !data?.publishable_key || !data?.stripe_account) {
        setPhase("unavailable");
        return;
      }
      const StripeCtor = await loadStripeJs();
      const stripe = StripeCtor(data.publishable_key, { stripeAccount: data.stripe_account });
      const elements = stripe.elements({ clientSecret: data.client_secret });
      stripeRef.current = stripe;
      elementsRef.current = elements;
      setPhase("card");
      setTimeout(() => elements.create("payment").mount(mountRef.current), 0);
    } catch (e) {
      if (e?.response?.status === 503) setPhase("unavailable");
      else { setError(errMessage(e) || e.message); setPhase("idle"); }
    }
  };

  const confirm = async (ev) => {
    ev.preventDefault();
    setPhase("paying");
    const { error: err } = await stripeRef.current.confirmPayment({
      elements: elementsRef.current,
      confirmParams: { return_url: window.location.href.split("?")[0] + "?paid=1" },
      redirect: "if_required",
    });
    if (err) { setError(err.message || "Payment failed"); setPhase("card"); }
    else setPhase("done");
  };

  if (!inv && !error) {
    return <div className="min-h-screen grid place-items-center" role="status"><Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" /></div>;
  }
  if (!inv) {
    return <main className="min-h-screen grid place-items-center p-6"><p role="alert" className="text-sm">{error}</p></main>;
  }

  const business = inv.business?.name || "the business";
  const due = (inv.amount_due_cents ?? 0) / 100;
  const paid = inv.status === "paid" || due <= 0;
  const returnedPaid = new URLSearchParams(window.location.search).get("paid") === "1";
  const canPayOnline = !!inv.business?.accepts_online_pay;

  return (
    <main className="min-h-screen bg-muted/30 py-10 px-4" data-testid="public-invoice-page">
      <div className="mx-auto max-w-xl rounded-2xl border bg-background p-6 shadow-sm">
        <p className="text-xs uppercase tracking-wide text-muted-foreground">Invoice from {business}</p>
        <h1 className="mt-1 text-2xl font-semibold">{inv.title}</h1>

        <table className="mt-6 w-full text-sm">
          <caption className="sr-only">Invoice line items</caption>
          <thead><tr className="text-left text-muted-foreground"><th scope="col" className="py-1">Item</th><th scope="col" className="py-1 text-right">Amount</th></tr></thead>
          <tbody>
            {(inv.lines || []).map((l, i) => (
              <tr key={i} className="border-t"><td className="py-2">{l.description} {Number(l.quantity) > 1 ? `× ${l.quantity}` : ""}</td><td className="py-2 text-right">{fmtUSD(lineTotal(l))}</td></tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t"><td className="py-2">Total</td><td className="py-2 text-right">{fmtUSD((inv.total_cents || 0) / 100)}</td></tr>
            <tr className="font-semibold"><td className="py-2">Amount due</td><td className="py-2 text-right" data-testid="public-invoice-due">{fmtUSD(due)}</td></tr>
          </tfoot>
        </table>

        {error && <p role="alert" className="mt-4 text-sm text-rose-600">{error}</p>}

        <div className="mt-6" aria-live="polite">
          {paid ? (
            <p className="flex items-center gap-2 text-sm font-medium"><CheckCircle2 className="h-4 w-4 text-emerald-600" aria-hidden />This invoice is paid. Thank you!</p>
          ) : phase === "done" || returnedPaid ? (
            <p className="flex items-center gap-2 text-sm font-medium" data-testid="public-invoice-paid"><CheckCircle2 className="h-4 w-4 text-emerald-600" aria-hidden />Payment received. It can take a moment to show as paid.</p>
          ) : !canPayOnline || phase === "unavailable" ? (
            <p className="text-sm text-muted-foreground" data-testid="public-invoice-unavailable">Online payment isn’t available for this invoice yet. Please contact {business} to pay.</p>
          ) : phase === "card" || phase === "paying" ? (
            <form onSubmit={confirm} className="space-y-4">
              <div ref={mountRef} aria-label="Payment details" />
              <Button type="submit" className="w-full h-11" disabled={phase === "paying"}>{phase === "paying" ? "Paying…" : `Pay ${fmtUSD(due)}`}</Button>
            </form>
          ) : (
            <Button className="w-full h-11" onClick={startPay} disabled={phase === "preparing"} data-testid="public-invoice-pay-btn">
              {phase === "preparing" ? "Preparing…" : "Pay online"}
            </Button>
          )}
        </div>
      </div>
    </main>
  );
}
