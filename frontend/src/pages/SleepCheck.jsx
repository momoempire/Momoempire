import { useState } from "react";
import { Link } from "react-router-dom";
import { Moon, Sun, Wind, Thermometer, ArrowRight, RotateCcw, Info } from "lucide-react";
import { Logo } from "../components/Logo";
import ThemeToggle from "../components/ThemeToggle";
import { SLEEP_COPY as C, SLEEP_QUESTIONS, tipsFor } from "../config/sleepCheck";

// Practice "Sleep Habits Check". Privacy by construction: no API, storage or analytics imports;
// answers live only in this component's state and are gone on reload. Results are worked out
// in the browser (tipsFor). No third-party script is loaded by this page.
function Disclaimer({ testid }) {
  return (
    <p role="note" data-testid={testid} className="glass-crystal rounded-xl p-4 text-sm text-white/80 flex gap-2">
      <Info className="hvac-cool h-4 w-4 mt-0.5 shrink-0" aria-hidden />
      <span>{C.disclaimer}</span>
    </p>
  );
}

export default function SleepCheck() {
  const [answers, setAnswers] = useState({});
  const [done, setDone] = useState(false);
  const answered = Object.keys(answers).length;
  const total = SLEEP_QUESTIONS.length;
  const tips = done ? tipsFor(answers) : [];
  const restart = () => { setAnswers({}); setDone(false); window.scrollTo?.(0, 0); };

  return (
    <div className="marketing-shell marketing-themed relative overflow-x-hidden" data-testid="sleep-check-page">
      <header className="sticky top-0 z-30 glass-crystal border-b border-white/10">
        <div className="max-w-3xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between gap-3">
          <Link to="/" className="text-white" aria-label={C.back}><Logo /></Link>
          <ThemeToggle className="text-white" />
        </div>
      </header>

      <main className="relative max-w-3xl mx-auto px-4 sm:px-6 py-12">
        <div className="flex items-center gap-2 text-white/70 text-sm">
          <span className="hvac-chip hvac-cool h-9 w-9 rounded-lg grid place-items-center" aria-hidden><Moon className="h-4 w-4" /></span>
          <span className="hvac-chip hvac-neutral h-9 w-9 rounded-lg grid place-items-center" aria-hidden><Thermometer className="h-4 w-4" /></span>
          <span className="hvac-chip hvac-cool h-9 w-9 rounded-lg grid place-items-center" aria-hidden><Wind className="h-4 w-4" /></span>
          <span className="hvac-chip hvac-warm h-9 w-9 rounded-lg grid place-items-center" aria-hidden><Sun className="h-4 w-4" /></span>
          <span className="ml-2 rounded-full border border-white/20 px-3 py-1">{C.badge}</span>
        </div>
        <h1 className="font-display text-4xl sm:text-5xl tracking-tight text-white mt-5">{C.title}</h1>
        <p className="mt-4 text-white/70 text-lg">{C.intro}</p>
        <p className="mt-2 text-white/70 text-sm">{C.privacy}</p>
        {!done && <div className="mt-6"><Disclaimer testid="sleep-disclaimer" /></div>}

        {!done ? (
          <form className="mt-10 space-y-6" onSubmit={(e) => { e.preventDefault(); if (answered === total) { setDone(true); window.scrollTo?.(0, 0); } }} data-testid="sleep-form">
            {SLEEP_QUESTIONS.map((q, qi) => (
              <fieldset key={q.id} className="glass-crystal hvac-card rounded-2xl p-5 sm:p-6" data-testid={`sleep-q-${q.id}`}>
                <legend className="sr-only">{q.q}</legend>
                <div className="text-white font-medium text-lg" aria-hidden><span className="hvac-accent-text mr-2">{qi + 1}.</span>{q.q}</div>
                <div className="mt-4 grid gap-2">
                  {q.options.map((o, oi) => (
                    <label key={oi} className="flex items-center gap-3 rounded-lg border border-white/15 px-4 min-h-[44px] cursor-pointer hover:bg-white/5 text-white/80">
                      <input type="radio" name={q.id} value={oi} checked={answers[q.id] === oi} onChange={() => setAnswers((a) => ({ ...a, [q.id]: oi }))} className="h-4 w-4 accent-orange-600" />
                      <span>{o.label}</span>
                    </label>
                  ))}
                </div>
              </fieldset>
            ))}
            <div className="flex flex-wrap items-center gap-4">
              <button type="submit" disabled={answered < total} data-testid="sleep-submit"
                className="inline-flex items-center h-12 px-6 rounded-md bg-white text-black hover:bg-white/90 font-medium disabled:opacity-60 disabled:cursor-not-allowed focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2">
                {C.submit}<ArrowRight className="h-4 w-4 ml-1.5" aria-hidden />
              </button>
              <span className="text-white/70 text-sm" aria-live="polite">{C.progress(answered, total)}{answered < total ? ` · ${C.submitHint}` : ""}</span>
            </div>
          </form>
        ) : (
          <section className="mt-10" data-testid="sleep-results" aria-labelledby="sleep-results-title">
            <div className="mb-6"><Disclaimer testid="sleep-results-disclaimer" /></div>
            <h2 id="sleep-results-title" className="font-display text-3xl text-white">{C.resultsTitle}</h2>
            {tips.length ? (
              <>
                <p className="mt-3 text-white/70">{C.resultsIntro}</p>
                <ul className="mt-5 space-y-3" data-testid="sleep-tips">
                  {tips.map((t) => (
                    <li key={t} className="glass-crystal rounded-xl p-4 text-white/80 flex gap-3"><Moon className="hvac-cool h-4 w-4 mt-1 shrink-0" aria-hidden />{t}</li>
                  ))}
                </ul>
              </>
            ) : (
              <p className="mt-3 text-white/80" data-testid="sleep-all-set">{C.allSet}</p>
            )}
            <div className="mt-6 flex flex-wrap gap-3">
              <button type="button" onClick={restart} data-testid="sleep-restart" className="inline-flex items-center h-12 px-5 rounded-md border border-white/20 text-white hover:bg-white/10 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600">
                <RotateCcw className="h-4 w-4 mr-2" aria-hidden />{C.restart}
              </button>
              <Link to="/" className="inline-flex items-center h-12 px-5 rounded-md text-white/80 hover:text-white underline-offset-4 hover:underline">{C.back}</Link>
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
