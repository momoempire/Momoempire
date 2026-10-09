import { Link } from "react-router-dom";
import { Logo } from "@/components/Logo";
import { Button } from "@/components/ui/button";
import { ArrowLeft } from "lucide-react";
import { isWaitlistOnly } from "@/lib/waitlistMode";

// EMP-WL-053: the waitlist-only build has no trial, pricing, plans or billing, so every sentence
// about them is hidden there and replaced by a clearly marked slot for Brann/counsel to fill.
// The legal text itself is NOT rewritten; the full build renders exactly as before.
function TodoSlot({ id, what }) {
  return (
    <p className="text-xs border border-dashed border-amber-500 text-amber-800 bg-amber-50 rounded px-2 py-1"
       data-testid="legal-todo-slot" data-todo-slot={id}>
      [TODO(Brann/counsel): waitlist-mode wording for {what}, or remove this slot.]
    </p>
  );
}

// EMP-WL-082: "Last updated" is a fixed value set at build time, never today's date (which made
// every visit look like a fresh policy change). TODO(Brann): set REACT_APP_LEGAL_LAST_UPDATED to
// the real date the policies take effect; until then a visible TODO slot is shown.
function LastUpdated() {
  const value = (process.env.REACT_APP_LEGAL_LAST_UPDATED || "").trim();
  return (
    <p className="text-xs text-muted-foreground" data-testid="legal-last-updated">
      Last updated:{" "}
      {value || (
        <span className="border border-dashed border-amber-500 text-amber-800 bg-amber-50 rounded px-1"
              data-testid="legal-last-updated-todo">
          [TODO(Brann): real date]
        </span>
      )}
    </p>
  );
}

function Legal({ title, children }) {
  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border">
        <div className="max-w-4xl mx-auto px-6 h-14 flex items-center justify-between">
          <Logo />
          <Link to="/"><Button variant="ghost" size="sm"><ArrowLeft className="h-4 w-4 mr-1" />Home</Button></Link>
        </div>
      </header>
      <main className="max-w-3xl mx-auto px-6 py-14 prose prose-sm" data-testid={`legal-${title.toLowerCase().replace(/\s+/g, "-")}`}>
        <h1 className="font-display text-4xl tracking-tight mb-6">{title}</h1>
        <LastUpdated />
        {children}
      </main>
    </div>
  );
}

export function Privacy() {
  const wl = isWaitlistOnly();
  return (
    <Legal title="Privacy Policy">
      <p>AI Office ("we", "our", "us") provides a multi-tenant SaaS platform that gives small businesses an AI receptionist, CRM, scheduling, and growth tooling. This policy explains what we collect, why, and how to control it.</p>
      <h2>What we collect</h2>
      <ul>
        <li><strong>Account data</strong> — name, email, business name, hashed password, Google OAuth profile when used.</li>
        {wl ? <li><TodoSlot id="privacy-workspace-content" what="workspace content" /></li> : <li><strong>Workspace content</strong> — services, pricing, knowledge, team members, appointments, leads, conversations, and uploaded knowledge documents.</li>}
        <li><strong>Call & SMS content</strong> — transcripts and metadata the AI uses to serve your customers. Phone numbers and messages flow through Twilio.</li>
        {wl ? <li><TodoSlot id="privacy-payment-usage" what="payment data and usage" /></li> : <>
        <li><strong>Payment data</strong> — handled by Stripe. We only store subscription IDs and plan state, never raw card numbers.</li>
        <li><strong>Usage</strong> — minutes, calls, SMS, AI interactions, and basic logs for billing + abuse prevention.</li>
        </>}
      </ul>
      <h2>How we use it</h2>
      <ul>
        <li>To operate the AI receptionist and tools you asked us to run.</li>
        {wl ? <li><TodoSlot id="privacy-billing-use" what="payment-related use of data" /></li> : <li>To bill the right amount — overage is computed from your plan's limits.</li>}
        <li>To send transactional email (invites, review requests, follow-ups, weekly digest) via Resend.</li>
        <li>To improve safety and reliability. We do <em>not</em> sell your data.</li>
      </ul>
      <h2>Who sees it</h2>
      <p>Your workspace data is isolated per tenant. Our vendors — Twilio (voice/SMS), Stripe (payments), Resend (email), MongoDB (storage), OpenAI/Anthropic/Google (LLMs) — only receive the fields they need to perform the task, and are bound by their own privacy terms.</p>
      {/* EMP-WL-095: the waitlist page itself loads Google Fonts and is served by Cloudflare (Pages,
          and Turnstile when on), so visitors' IPs reach them. No wording is written here. */}
      {wl && <TodoSlot id="privacy-site-processors" what="Google Fonts and Cloudflare (hosting, Turnstile) as processors of visitor data" />}
      <h2>Your rights</h2>
      <p>Email <a href="mailto:privacy@aioffice.io">privacy@aioffice.io</a> to export, correct, or delete your data. We will respond within 30 days.</p>
      <h2>Retention</h2>
      {wl ? <TodoSlot id="privacy-retention" what="retention" /> : <p>We keep account data while your workspace is active, plus 90 days after cancellation for billing reconciliation. Call recordings and transcripts can be deleted on request.</p>}
      <h2>Contact</h2>
      <p>Questions? <a href="mailto:privacy@aioffice.io">privacy@aioffice.io</a>.</p>
    </Legal>
  );
}

export function Terms() {
  const wl = isWaitlistOnly();
  return (
    <Legal title="Terms of Service">
      <p>By using AI Office you agree to these terms. In short: we provide the platform honestly, you provide accurate info and don't abuse it.</p>
      <h2>Account</h2>
      <p>You must be 18+ and the authorized operator of the business you're onboarding. Keep your password safe. You're responsible for your team members' actions in your workspace.</p>
      <h2>Acceptable use</h2>
      <p>No illegal content, no unsolicited mass messaging in violation of TCPA/CAN-SPAM, no attempts to break multi-tenant isolation, no reselling the service without a written agreement.</p>
      {wl ? <TodoSlot id="terms-billing-trial" what="the two hidden sections on payments and sign-up offers" /> : <>
      <h2>Billing</h2>
      <p>Monthly plans auto-renew. You can cancel any time from your workspace — your access runs through the end of the paid period. Overage is billed on the next invoice at the rate shown on each plan.</p>
      <h2>Trial</h2>
      <p>The free trial has limits shown live on the pricing page. No credit card is required. We may update trial length with reasonable notice to new signups.</p>
      </>}
      <h2>AI content</h2>
      <p>Our AI generates responses from your knowledge, services, and public context. You remain responsible for what your AI employee says on your behalf. We offer guardrails (discount caps, objection playbook, persona) to keep it on-brand.</p>
      <h2>Service levels</h2>
      {wl ? <TodoSlot id="terms-service-levels" what="service levels" /> : <p>We target high uptime but offer no formal SLA on free/starter plans. Enterprise agreements can include one.</p>}
      <h2>Termination</h2>
      <p>Either side can terminate for breach. We can suspend accounts that threaten platform safety.</p>
      <h2>Liability</h2>
      <p>Service is provided "as is". Our liability is capped at the fees you paid us in the 12 months prior to the claim.</p>
      <h2>Changes</h2>
      <p>We'll email material changes 30 days before they take effect.</p>
      <h2>Contact</h2>
      <p><a href="mailto:legal@aioffice.io">legal@aioffice.io</a>.</p>
    </Legal>
  );
}
