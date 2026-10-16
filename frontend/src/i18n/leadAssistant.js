// Copy for the landing page's AI assistant chat (components/LeadAssistant.jsx).
// Kept as a plain strings module in the spirit of i18n/estimator.js and lib/waitlistErrors.js
// (feature copy lives with the feature, not in the shared locale files) — but deliberately NOT
// registered as an i18next resource bundle: the early-access page's render guard
// (waitlist/WaitlistI18n.test.js) whitelists exactly the namespaces that page may load
// ("landing", "waitlistErrors"), and a new top-level namespace would fail it. English only for
// now, matching the landing page's own English-pinned labels (EMP-WL-016); {{var}} placeholders
// are filled by the component, and an "es" table can sit beside this one when translated.
export const LEAD_ASSISTANT_COPY = {
  open_label: "Chat with Mae, the AI assistant",
  dismiss_label: "Dismiss Mae",
  close_label: "Close chat",
  panel_title: "Mae · AI front desk",
  typing: "Mae is typing",
  greet_bubble: "Hi! I'm Mae, the AI front desk here — want the 10-second tour?",
  nudge_bubble: "Still looking around? I can get you on the early-access list in about 20 seconds.",
  start_cta: "Let's do it",
  browse_pricing: "Just browsing — show me pricing",
  browse_generic: "Just browsing",
  hello: "Hi! I'm Mae. I answer the phones, book the jobs, and chase the invoices around here. Let me show you how I'd work for your shop — a few quick questions, about 20 seconds.",
  hello_industry: "Hi! I'm Mae, the AI front desk here. Running a {{industry}} shop? Let me show you how I'd work for you — a few quick questions, about 20 seconds.",
  q_trade: "First — what kind of work do you do?",
  trade_other_ph: "Type your trade…",
  ack_trade: "{{trade}} — good, that's home turf for me.",
  q_size: "How many trucks or techs do you have out working?",
  size_1: "Just me",
  size_2_5: "2–5",
  size_6_15: "6–15",
  size_16: "16+",
  ack_size_solo: "One-person show — then every missed call is money walking. Noted.",
  ack_size_team: "Got it — a crew of {{size}}.",
  q_headache: "What eats most of your week?",
  headache_missed: "Missed calls",
  headache_sched: "Scheduling chaos",
  headache_invoice: "Chasing invoices",
  headache_all: "All of it",
  fix_missed: "That's my favorite problem: I answer every call, 24/7, and book the job before a competitor's phone even rings.",
  fix_sched: "I can help there — I book, confirm, and remind, so the board stays full and no-shows drop off.",
  fix_invoice: "Easy: I send the invoice the moment the job's done and politely chase it until it's paid. No more Sunday-night billing.",
  fix_all: "Then you want the whole front office — calls, booking, invoicing, follow-ups. That's literally my job description.",
  q_name: "Who am I talking to?",
  name_ph: "Your name",
  ack_name: "Nice to meet you, {{name}}.",
  q_business: "And what's the shop called? Optional — tap Skip if you'd rather not say.",
  business_ph: "Business name (optional)",
  skip: "Skip",
  q_contact: "Last step: where should we send your early-access invite? Email works best. A mobile number works too — I'll attach it to your signup.",
  contact_ph: "Email or mobile number",
  contact_need_email: "Got the number — I'll attach it to your signup. The early-access list itself runs on email, so drop me one too and you're in.",
  contact_retry: "Hmm, I couldn't spot an email or a mobile number in that. One more try?",
  sending: "Saving your spot…",
  success_next: "Here's what happens next: when seats open for {{trade}} shops, we'll email {{email}} first. No spam, ever — that's a promise I can actually keep.",
  error_title: "That didn't go through.",
  error_retry: "Try again",
  error_form: "Use the regular form instead",
  send: "Send",
  input_ph: "Type your answer…",
  input_label: "Your answer",
};

export default LEAD_ASSISTANT_COPY;
