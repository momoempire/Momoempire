import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api, errMessage } from "@/lib/api";
import { fmtUSD, lineTotal } from "@/lib/money";
import { Button } from "@/components/ui/button";
import { CheckCircle2, Loader2 } from "lucide-react";

// Public "Review & accept" page for a quote/estimate sent to a customer.
// Backend: GET/POST /api/public/c2p/quotes/:token[/accept]
export default function PublicQuote() {
  const { token } = useParams();
  const [quote, setQuote] = useState(null);
  const [error, setError] = useState("");
  const [accepting, setAccepting] = useState(false);
  const [accepted, setAccepted] = useState(null); // { invoice }

  useEffect(() => {
    api.get(`/public/c2p/quotes/${token}`)
      .then((r) => setQuote(r.data))
      .catch((e) => setError(e?.response?.status === 404 ? "This quote link is invalid or has expired." : errMessage(e)));
  }, [token]);

  const accept = async () => {
    setAccepting(true);
    setError("");
    try {
      const { data } = await api.post(`/public/c2p/quotes/${token}/accept`, {});
      setAccepted(data);
    } catch (e) {
      setError(errMessage(e));
    } finally {
      setAccepting(false);
    }
  };

  if (!quote && !error) {
    return <div className="min-h-screen grid place-items-center text-sm text-muted-foreground" role="status"><Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" /></div>;
  }
  if (!quote) {
    return <main className="min-h-screen grid place-items-center p-6"><p role="alert" className="text-sm">{error}</p></main>;
  }

  const business = quote.business?.name || "the business";
  const alreadyDone = ["approved", "accepted"].includes(quote.status);
  const declined = quote.status === "declined";
  // EMP-W-CF-022: expired quotes and quotes the owner hasn't approved (e.g. dashboard estimates)
  // can't be accepted, so don't offer a button that always fails.
  // TODO(Brann): confirm the wording of these two messages.
  const expired = Boolean(quote.expired);
  const notApproved = !quote.owner_approved;
  const invoiceToken = accepted?.invoice?.public_token;

  return (
    <main className="min-h-screen bg-muted/30 py-10 px-4" data-testid="public-quote-page">
      <div className="mx-auto max-w-xl rounded-2xl border bg-background p-6 shadow-sm">
        <p className="text-xs uppercase tracking-wide text-muted-foreground">Quote from {business}</p>
        <h1 className="mt-1 text-2xl font-semibold">{quote.title}</h1>
        {quote.customer_name && <p className="text-sm text-muted-foreground mt-1">Prepared for {quote.customer_name}</p>}

        <table className="mt-6 w-full text-sm">
          <caption className="sr-only">Quote line items</caption>
          <thead><tr className="text-left text-muted-foreground"><th scope="col" className="py-1">Item</th><th scope="col" className="py-1 text-right">Qty</th><th scope="col" className="py-1 text-right">Amount</th></tr></thead>
          <tbody>
            {(quote.lines || []).map((l, i) => (
              <tr key={i} className="border-t"><td className="py-2">{l.description}</td><td className="py-2 text-right">{l.quantity}</td><td className="py-2 text-right">{fmtUSD(lineTotal(l))}</td></tr>
            ))}
          </tbody>
          <tfoot><tr className="border-t font-semibold"><td className="py-2" colSpan={2}>Total</td><td className="py-2 text-right" data-testid="public-quote-total">{fmtUSD(quote.total)}</td></tr></tfoot>
        </table>

        {error && <p role="alert" className="mt-4 text-sm text-rose-600">{error}</p>}

        <div className="mt-6" aria-live="polite">
          {accepted ? (
            <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm" data-testid="public-quote-accepted">
              <p className="flex items-center gap-2 font-medium"><CheckCircle2 className="h-4 w-4 text-emerald-600" aria-hidden />Thanks — you accepted this quote.</p>
              <p className="mt-1 text-muted-foreground">{business} will follow up to confirm scheduling.</p>
              {invoiceToken && <Link className="mt-3 inline-block underline" to={`/i/${invoiceToken}`}>View your invoice</Link>}
            </div>
          ) : alreadyDone ? (
            <p className="text-sm" data-testid="public-quote-already">This quote has already been accepted.</p>
          ) : declined ? (
            <p className="text-sm">This quote is no longer available. Please contact {business}.</p>
          ) : expired ? (
            <p className="text-sm" data-testid="public-quote-expired">This quote has expired. Please contact {business} for an updated quote.</p>
          ) : notApproved ? (
            <p className="text-sm" data-testid="public-quote-not-approved">This quote can't be accepted online yet. Please contact {business}.</p>
          ) : (
            <Button className="w-full h-11" onClick={accept} disabled={accepting} data-testid="public-quote-accept-btn">
              {accepting ? "Accepting…" : "Accept quote"}
            </Button>
          )}
        </div>
      </div>
    </main>
  );
}
