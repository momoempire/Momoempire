import { useId, useState } from "react";
import { COPY, nicheConfig, parseInput, computeRoi } from "../../config/roi";

// Drop-in ROI estimate for any niche page: <RoiCalculator niche="hvac" lang="en" />.
// Math runs in the browser only: no storage, no network calls.
export default function RoiCalculator({ niche = "hvac", lang = "en", assumptions: override }) {
  const cfg = nicheConfig(niche);
  const a = { ...cfg.assumptions, ...(override || {}) };
  const c = COPY[lang] || COPY.en;
  const id = useId();
  const [calls, setCalls] = useState(String(cfg.defaults.missedCalls));
  const [value, setValue] = useState(String(cfg.defaults.jobValue));
  const pc = parseInput(calls, { integer: true });
  const pv = parseInput(value);
  const ok = !pc.error && !pv.error;
  const r = ok ? computeRoi({ missedCalls: pc.value, jobValue: pv.value }, a) : null;
  const locale = lang === "es" ? "es-US" : "en-US";
  const money = (n) => new Intl.NumberFormat(locale, { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(n);
  const num = (n) => new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(n);
  const pct = (n) => `${Math.round(n * 100)}%`;

  const field = (key, label, val, set, parsed, step) => (
    <div>
      <label htmlFor={`${id}-${key}`} className="block text-sm text-white/80 mb-1">{label}</label>
      <input
        id={`${id}-${key}`} data-testid={`roi-${key}`} type="number" inputMode="decimal" min="0" step={step}
        value={val} onChange={(e) => set(e.target.value)}
        aria-invalid={parsed.error ? "true" : "false"} aria-describedby={parsed.error ? `${id}-${key}-err` : undefined}
        className="w-full h-11 rounded-lg bg-white/5 border border-white/20 px-3 text-white"
      />
      {parsed.error && <p id={`${id}-${key}-err`} role="alert" className="mt-1 text-sm text-rose-200" data-testid={`roi-${key}-error`}>{c[parsed.error]}</p>}
    </div>
  );

  return (
    <section aria-labelledby={`${id}-title`} className="glass-crystal rounded-2xl p-6 text-white" data-testid="roi-calculator">
      <h2 id={`${id}-title`} className="font-display text-2xl">{c.title}</h2>
      <p className="mt-2 text-white/70 text-sm">{c.intro}</p>
      <div className="mt-5 grid grid-cols-1 sm:grid-cols-2 gap-4">
        {field("missedCalls", c.missedCalls, calls, setCalls, pc, "1")}
        {field("jobValue", c.jobValue, value, setValue, pv, "any")}
      </div>
      <div className="mt-5 grid grid-cols-1 sm:grid-cols-2 gap-4" aria-live="polite">
        <div className="rounded-xl border border-white/15 p-4">
          <div className="text-sm text-white/70">{c.revenue}</div>
          <div className="mt-1 text-3xl font-display hvac-warm" data-testid="roi-revenue">{r ? `≈ ${money(r.recoveredRevenue)}` : "—"}</div>
        </div>
        <div className="rounded-xl border border-white/15 p-4">
          <div className="text-sm text-white/70">{c.time}</div>
          <div className="mt-1 text-3xl font-display hvac-cool" data-testid="roi-hours">{r ? `≈ ${num(r.hoursSaved)} ${c.hours}` : "—"}</div>
        </div>
      </div>
      <div className="mt-5 text-sm text-white/70" data-testid="roi-assumptions">
        <h3 className="font-medium text-white/80">{c.assumptionsTitle}</h3>
        <ul className="mt-2 grid grid-cols-1 sm:grid-cols-3 gap-2">
          <li>{c.recoveryRate}: <strong>{pct(a.recoveryRate)}</strong></li>
          <li>{c.bookingRate}: <strong>{pct(a.bookingRate)}</strong></li>
          <li>{c.minutesPerCall}: <strong>{a.minutesPerCall}</strong></li>
        </ul>
        <p className="mt-2">{c.formula}</p>
        <p className="mt-2 text-white/60" data-testid="roi-disclaimer">{c.disclaimer}</p>
      </div>
    </section>
  );
}
