// One formatter for every plan price on the site. The amount always comes from the plan's
// `price_cents` (the same field checkout charges: backend/routers/payments.py `unit_amount`),
// and is shown with cents, never rounded: 1999 -> "$19.99".
export function formatPlanPrice(cents) {
  const n = Number(cents);
  if (!Number.isFinite(n)) return "";
  return `$${(n / 100).toFixed(2)}`;
}
