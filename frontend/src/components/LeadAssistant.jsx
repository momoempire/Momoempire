import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errMessage } from "@/lib/api";
import { waitlistErrorText } from "@/lib/waitlistErrors";
import TurnstileWidget from "@/components/TurnstileWidget";
import { LEAD_ASSISTANT_COPY } from "@/i18n/leadAssistant";
import "./LeadAssistant.css";

// Mae — the landing page's animated AI-assistant mascot and conversational lead capture.
// Loaded with React.lazy from Landing.jsx so the landing bundle doesn't pay for it up front.
// The chat feeds the SAME lead path as the page's own form: POST /public/waitlist with the
// form's exact payload shape ({ email, name, business_name, industry, note, website,
// turnstile_token }); the qualification answers ride along in `note`, the way the instant-quote
// estimator already encodes its context there. Character is inline SVG + CSS keyframes only.

const DISMISS_KEY = "mae.dismissed.v1";
const MAX_PROACTIVE = 2;

const TRADE_OPTIONS = [
  { value: "HVAC", label: "HVAC" },
  { value: "Plumbing", label: "Plumbing" },
  { value: "Electrical", label: "Electrical" },
  { value: "Roofing", label: "Roofing" },
  { value: "Landscaping", label: "Landscaping" },
];

const TRADE_KEYWORDS = [
  [/hvac|heat|air condition|cooling|furnace/i, "HVAC"],
  [/plumb/i, "Plumbing"],
  [/electric/i, "Electrical"],
  [/roof/i, "Roofing"],
  [/landscap|lawn|yard|garden/i, "Landscaping"],
  [/clean/i, "Cleaning"],
  [/pest|exterminat/i, "Pest Control"],
  [/garage/i, "Garage Door"],
  [/paint/i, "Painting"],
  [/handyman/i, "Handyman"],
  [/solar/i, "Solar"],
  [/pool/i, "Pool Service"],
  [/appliance/i, "Appliance Repair"],
  [/locksmith|lock/i, "Locksmith"],
  [/tree/i, "Tree Service"],
  [/concrete|fenc/i, "Fencing & Concrete"],
  [/auto|mechanic|car repair/i, "Auto Repair"],
  [/septic|well/i, "Septic & Well"],
  [/remodel|kitchen|bath/i, "Kitchen & Bath Remodeling"],
  [/floor/i, "Flooring"],
  [/junk/i, "Junk Removal"],
];

const HEADACHE_KEYWORDS = [
  [/miss|call|phone|answer/i, "missed"],
  [/schedul|book|dispatch|calendar|no.?show/i, "scheduling"],
  [/invoic|bill|paid|payment|money owed|chas/i, "invoicing"],
  [/all|everything|each/i, "all"],
];

function titleCase(s) {
  return s.replace(/\w\S*/g, (w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase());
}

function parseTrade(text) {
  const hit = TRADE_KEYWORDS.find(([re]) => re.test(text));
  if (hit) return hit[1];
  const clean = text.trim().slice(0, 60);
  return clean ? titleCase(clean) : "Other";
}

function parseSize(text) {
  const n = parseInt((text.match(/\d+/) || [])[0], 10);
  if (Number.isFinite(n)) {
    if (n <= 1) return "1";
    if (n <= 5) return "2-5";
    if (n <= 15) return "6-15";
    return "16+";
  }
  if (/just me|solo|myself|one/i.test(text)) return "1";
  return text.trim().slice(0, 40);
}

function parseHeadache(text) {
  const hit = HEADACHE_KEYWORDS.find(([re]) => re.test(text));
  return hit ? hit[1] : "all";
}

function fillTemplate(tpl, opts) {
  return String(tpl || "").replace(/\{\{(\w+)\}\}/g, (_, k) => (opts && opts[k] != null ? String(opts[k]) : ""));
}

function MaeSvg({ size = 52 }) {
  return (
    <svg className="mae-bot" width={size} height={size} viewBox="0 0 96 96" aria-hidden="true" focusable="false">
      {/* antenna */}
      <line x1="48" y1="15" x2="48" y2="7" stroke="#8fb4dd" strokeWidth="2.5" strokeLinecap="round" />
      <circle className="mae-antenna-dot" cx="48" cy="6" r="3.4" fill="#34d399" />
      {/* arms (behind the body) */}
      <g className="mae-arm-point">
        <line x1="29" y1="68" x2="17" y2="80" stroke="#24466e" strokeWidth="7.5" strokeLinecap="round" />
        <circle cx="15.5" cy="81.5" r="4.4" fill="#eef3fa" />
      </g>
      <g className="mae-arm-wave">
        <line x1="67" y1="68" x2="81" y2="55" stroke="#24466e" strokeWidth="7.5" strokeLinecap="round" />
        <circle cx="83" cy="53" r="4.4" fill="#eef3fa" />
      </g>
      {/* head + headset */}
      <g className="mae-head">
        <rect x="18" y="15" width="60" height="47" rx="17" fill="#eef3fa" />
        <path d="M19 41 v-5 a29 21 0 0 1 58 0 v5" fill="none" stroke="#34d399" strokeWidth="5" strokeLinecap="round" />
        <rect x="12.5" y="37" width="9.5" height="17" rx="4.5" fill="#34d399" />
        <rect x="74" y="37" width="9.5" height="17" rx="4.5" fill="#34d399" />
        <path d="M79 54 q1.5 10.5 -10.5 11.5" fill="none" stroke="#34d399" strokeWidth="3.5" strokeLinecap="round" />
        <circle cx="66.5" cy="65.5" r="3.2" fill="#34d399" />
        <g className="mae-eyes" fill="#16233c">
          <circle cx="38" cy="38" r="3.6" />
          <circle cx="58" cy="38" r="3.6" />
        </g>
        <ellipse cx="29.5" cy="46.5" rx="4" ry="2.4" fill="#f9a8d4" opacity="0.55" />
        <ellipse cx="66.5" cy="46.5" rx="4" ry="2.4" fill="#f9a8d4" opacity="0.55" />
        <path d="M41.5 48.5 q6.5 5.5 13 0" fill="none" stroke="#16233c" strokeWidth="3" strokeLinecap="round" />
      </g>
      {/* torso */}
      <rect x="28" y="63" width="40" height="25" rx="10" fill="#24466e" />
      <rect x="39.5" y="69.5" width="17" height="10" rx="3" fill="rgba(255,255,255,0.16)" />
      <polyline points="42,74.5 45.5,74.5 47.5,71.5 49.5,77.5 51,74.5 54,74.5" fill="none" stroke="#34d399" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export default function LeadAssistant({
  t: tProp,
  presetIndustry = "",
  waitlistOnly = false,
  onShowPricing,
  onShowForm,
}) {
  const { t: tOwn } = useTranslation();
  const t = tProp || tOwn;
  // Mascot copy ships as a plain EN strings module (see i18n/leadAssistant.js for why it is not
  // an i18next bundle); only two shared labels — the success title and the "AI online" status —
  // come from the page's locale via t.
  const K = useCallback((key, opts) => fillTemplate(LEAD_ASSISTANT_COPY[key] || key, opts), []);

  const [dismissed, setDismissed] = useState(() => {
    try { return !!window.localStorage.getItem(DISMISS_KEY); } catch { return false; }
  });
  const [open, setOpen] = useState(false);
  const [bubble, setBubble] = useState(null); // "greet" | "nudge"
  const [waving, setWaving] = useState(false);
  const [pointing, setPointing] = useState(false);
  const [celebrating, setCelebrating] = useState(false);
  const [typing, setTyping] = useState(false);
  const [step, setStep] = useState("idle");
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [turnstileToken, setTurnstileToken] = useState("");
  const [answers, setAnswers] = useState({ trade: presetIndustry || "", size: "", headache: "", name: "", business: "", email: "", phone: "" });

  const timers = useRef([]);
  const proactive = useRef(0);
  const openedOnce = useRef(false);
  const phonePrompted = useRef(false);
  const answersRef = useRef(answers);
  answersRef.current = answers;
  const stepRef = useRef(step);
  stepRef.current = step;
  const msgsRef = useRef(null);
  const bubbleRef = useRef(null);
  bubbleRef.current = bubble;
  const inputRef = useRef(null);
  const reducedMotion = useRef(
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );

  const later = useCallback((fn, ms) => {
    const id = setTimeout(fn, ms);
    timers.current.push(id);
    return id;
  }, []);

  useEffect(() => {
    const stash = timers.current;
    return () => stash.forEach((id) => clearTimeout(id));
  }, []);

  const pushMsg = useCallback((from, text, kind) => {
    setMessages((m) => [...m, { id: `${Date.now()}-${m.length}`, from, text, kind }]);
  }, []);

  // Mae "thinks" (typing dots) briefly, then speaks. No delay for reduced motion.
  const say = useCallback((text, after) => {
    if (reducedMotion.current) {
      pushMsg("mae", text);
      if (after) after();
      return;
    }
    setTyping(true);
    later(() => {
      setTyping(false);
      pushMsg("mae", text);
      if (after) after();
    }, 550);
  }, [later, pushMsg]);

  /* ---------------- lead submit: the page form's exact path ---------------- */

  const submitLead = useCallback(async (finalAnswers) => {
    setStep("submitting");
    pushMsg("mae", K("sending"));
    const noteParts = ["Mae chat", `trade=${finalAnswers.trade || "unspecified"}`];
    if (finalAnswers.size) noteParts.push(`crew=${finalAnswers.size}`);
    if (finalAnswers.headache) noteParts.push(`headache=${finalAnswers.headache}`);
    if (finalAnswers.phone) noteParts.push(`phone=${finalAnswers.phone}`);
    try {
      await api.post("/public/waitlist", {
        email: finalAnswers.email,
        name: finalAnswers.name || "",
        business_name: finalAnswers.business || "",
        industry: (finalAnswers.trade || "").slice(0, 60),
        note: noteParts.join("; ").slice(0, 2000),
        website: "",
        turnstile_token: turnstileToken,
      });
      setStep("done");
      setCelebrating(true);
      later(() => setCelebrating(false), 2100);
      pushMsg("mae", t("landing.waitlist_success_title"));
      pushMsg("mae", K("success_next", { trade: finalAnswers.trade || "your", email: finalAnswers.email }));
    } catch (err) {
      setStep("error");
      pushMsg("mae", `${K("error_title")} ${waitlistErrorText(err, t, errMessage)}`, "error");
    }
  }, [K, later, pushMsg, t, turnstileToken]);

  /* ---------------- conversation flow ---------------- */

  const askSize = useCallback(() => {
    setStep("size");
    say(K("q_size"));
  }, [K, say]);

  const askHeadache = useCallback(() => {
    setStep("headache");
    say(K("q_headache"));
  }, [K, say]);

  const askName = useCallback(() => {
    setStep("name");
    say(K("q_name"));
  }, [K, say]);

  const askBusiness = useCallback(() => {
    setStep("business");
    say(K("q_business"));
  }, [K, say]);

  const askContact = useCallback(() => {
    setStep("contact");
    say(K("q_contact"));
  }, [K, say]);

  const answerTrade = useCallback((value, display) => {
    pushMsg("user", display || value);
    const a = { ...answersRef.current, trade: value };
    setAnswers(a);
    say(K("ack_trade", { trade: value }), askSize);
  }, [K, askSize, pushMsg, say]);

  const answerSize = useCallback((value, display) => {
    pushMsg("user", display || value);
    const a = { ...answersRef.current, size: value };
    setAnswers(a);
    if (value === "1") say(K("ack_size_solo"), askHeadache);
    else say(K("ack_size_team", { size: value }), askHeadache);
  }, [K, askHeadache, pushMsg, say]);

  const answerHeadache = useCallback((value, display) => {
    pushMsg("user", display || value);
    const a = { ...answersRef.current, headache: value };
    setAnswers(a);
    const fixKey = { missed: "fix_missed", scheduling: "fix_sched", invoicing: "fix_invoice", all: "fix_all" }[value] || "fix_all";
    say(K(fixKey), askName);
  }, [K, askName, pushMsg, say]);

  const answerName = useCallback((raw) => {
    const name = raw.trim().slice(0, 120);
    pushMsg("user", raw);
    const a = { ...answersRef.current, name };
    setAnswers(a);
    say(K("ack_name", { name: name.split(" ")[0] || "friend" }), askBusiness);
  }, [K, askBusiness, pushMsg, say]);

  const answerBusiness = useCallback((raw) => {
    const business = (raw || "").trim().slice(0, 120);
    if (business) pushMsg("user", business);
    const a = { ...answersRef.current, business };
    setAnswers(a);
    askContact();
  }, [askContact, pushMsg]);

  const answerContact = useCallback((raw) => {
    pushMsg("user", raw);
    const emailMatch = raw.match(/[^\s@]+@[^\s@]+\.[^\s@]{2,}/);
    const digits = raw.replace(/\D/g, "");
    const phone = !emailMatch && digits.length >= 10 ? raw.trim().slice(0, 40) : (answersRef.current.phone || "");
    const a = { ...answersRef.current, phone };
    if (emailMatch) {
      a.email = emailMatch[0].slice(0, 254);
      setAnswers(a);
      submitLead(a);
      return;
    }
    setAnswers(a);
    if (phone && !phonePrompted.current) {
      phonePrompted.current = true;
      say(K("contact_need_email"));
    } else {
      say(K("contact_retry"));
    }
  }, [K, say, submitLead]);

  const onTextSubmit = (e) => {
    e.preventDefault();
    const raw = input.trim();
    if (!raw || typing || step === "submitting" || step === "done") return;
    setInput("");
    if (step === "trade") answerTrade(parseTrade(raw), raw);
    else if (step === "size") answerSize(parseSize(raw), raw);
    else if (step === "headache") answerHeadache(parseHeadache(raw), raw);
    else if (step === "name") answerName(raw);
    else if (step === "business") answerBusiness(raw);
    else if (step === "contact") answerContact(raw);
  };

  const startFlow = useCallback(() => {
    if (presetIndustry) {
      setAnswers((a) => ({ ...a, trade: presetIndustry }));
      say(K("hello_industry", { industry: presetIndustry }), askSize);
    } else {
      say(K("hello"), () => {
        setStep("trade");
        say(K("q_trade"));
      });
    }
  }, [K, askSize, presetIndustry, say]);

  const openPanel = useCallback(() => {
    openedOnce.current = true;
    proactive.current = MAX_PROACTIVE; // she's got their attention; no more nudges
    setBubble(null);
    setOpen(true);
    if (stepRef.current === "idle") startFlow();
  }, [startFlow]);

  const closePanel = useCallback(() => setOpen(false), []);

  const dismissForever = useCallback(() => {
    try { window.localStorage.setItem(DISMISS_KEY, String(Date.now())); } catch { /* private mode */ }
    setDismissed(true);
    setBubble(null);
    setOpen(false);
  }, []);

  const browse = useCallback(() => {
    setOpen(false);
    setBubble(null);
    if (onShowPricing) onShowPricing();
  }, [onShowPricing]);

  const useFormInstead = useCallback(() => {
    setOpen(false);
    if (onShowForm) onShowForm();
  }, [onShowForm]);

  /* ---------------- proactive triggers ---------------- */

  useEffect(() => {
    if (dismissed || reducedMotion.current) return undefined;
    const ids = [];
    const after = (fn, ms) => { ids.push(setTimeout(fn, ms)); };
    after(() => {
      if (!openedOnce.current) {
        setWaving(true);
        after(() => setWaving(false), 2900);
      }
    }, 900);
    after(() => {
      if (openedOnce.current || proactive.current >= MAX_PROACTIVE) return;
      proactive.current += 1;
      setBubble("greet");
      setPointing(true);
      after(() => setPointing(false), 2400);
      after(() => setBubble((b) => (b === "greet" ? null : b)), 10000);
    }, 2800);
    const nudge = () => {
      if (openedOnce.current || bubbleRef.current) return;
      if (proactive.current < 1 || proactive.current >= MAX_PROACTIVE) return;
      proactive.current += 1;
      setBubble("nudge");
      after(() => setBubble((bb) => (bb === "nudge" ? null : bb)), 10000);
    };
    const onScroll = () => {
      const el = document.documentElement;
      const max = el.scrollHeight - el.clientHeight;
      if (max > 0 && el.scrollTop / max > 0.55) nudge();
    };
    const onLeave = (e) => { if (e.clientY <= 0) nudge(); };
    window.addEventListener("scroll", onScroll, { passive: true });
    document.addEventListener("mouseleave", onLeave);
    return () => {
      ids.forEach((id) => clearTimeout(id));
      window.removeEventListener("scroll", onScroll);
      document.removeEventListener("mouseleave", onLeave);
    };
  }, [dismissed]);

  /* ---------------- panel niceties ---------------- */

  useEffect(() => {
    if (msgsRef.current) msgsRef.current.scrollTop = msgsRef.current.scrollHeight;
  }, [messages, typing, open]);

  useEffect(() => {
    if (open && inputRef.current) inputRef.current.focus({ preventScroll: true });
  }, [open, step]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open ]);

  if (dismissed) return null;

  const rootCls = ["mae-root", "mae-enter"]
    .concat(waving ? ["is-waving"] : [])
    .concat(pointing ? ["is-pointing"] : [])
    .concat(celebrating ? ["is-celebrating"] : [])
    .join(" ");

  const chips = [];
  if (open && !typing) {
    if (step === "trade") {
      TRADE_OPTIONS.forEach((o) => chips.push({ label: o.label, onClick: () => answerTrade(o.value, o.label) }));
      chips.push({ label: waitlistOnly ? K("browse_generic") : K("browse_pricing"), quiet: true, onClick: browse });
    } else if (step === "size") {
      [["1", K("size_1")], ["2-5", K("size_2_5")], ["6-15", K("size_6_15")], ["16+", K("size_16")]]
        .forEach(([v, l]) => chips.push({ label: l, onClick: () => answerSize(v, l) }));
    } else if (step === "headache") {
      [["missed", K("headache_missed")], ["scheduling", K("headache_sched")], ["invoicing", K("headache_invoice")], ["all", K("headache_all")]]
        .forEach(([v, l]) => chips.push({ label: l, onClick: () => answerHeadache(v, l) }));
    } else if (step === "business") {
      chips.push({ label: K("skip"), quiet: true, onClick: () => answerBusiness("") });
    } else if (step === "error") {
      chips.push({ label: K("error_retry"), onClick: askContact });
      chips.push({ label: K("error_form"), quiet: true, onClick: useFormInstead });
    }
  }

  const inputPlaceholder = step === "name" ? K("name_ph")
    : step === "business" ? K("business_ph")
      : step === "contact" ? K("contact_ph")
        : step === "trade" ? K("trade_other_ph")
          : K("input_ph");

  return (
    <div className={rootCls} data-testid="lead-assistant">
      {open && (
        <div className="mae-panel" role="dialog" aria-label={K("panel_title")}>
          <div className="mae-panel-head">
            <MaeSvg size={34} />
            <div>
              <div className="mae-panel-title">{K("panel_title")}</div>
              <div className="mae-panel-sub">{t("landing.preview_online")}</div>
            </div>
            <button type="button" className="mae-panel-close" onClick={closePanel} aria-label={K("close_label")}>✕</button>
          </div>
          <div className="mae-msgs" ref={msgsRef} aria-live="polite">
            {messages.map((m) => (
              <div key={m.id} className={`mae-msg ${m.from === "mae" ? "mae-msg-mae" : "mae-msg-user"}${m.kind === "error" ? " mae-msg-error" : ""}`}>
                {m.text}
              </div>
            ))}
            {typing && (
              <div className="mae-msg mae-msg-mae">
                <span className="mae-typing" aria-hidden="true"><span /><span /><span /></span>
                <span className="mae-sr">{K("typing")}</span>
              </div>
            )}
          </div>
          {step === "contact" || step === "error" || step === "submitting" ? (
            <div className="mae-turnstile"><TurnstileWidget onToken={setTurnstileToken} /></div>
          ) : null}
          {chips.length > 0 && (
            <div className="mae-chips">
              {chips.map((c) => (
                <button key={c.label} type="button" className={`mae-chip${c.quiet ? " mae-chip-quiet" : ""}`} onClick={c.onClick}>{c.label}</button>
              ))}
            </div>
          )}
          <form className="mae-form" onSubmit={onTextSubmit}>
            <label className="mae-sr" htmlFor="mae-input">{K("input_label")}</label>
            <input
              id="mae-input"
              ref={inputRef}
              className="mae-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={inputPlaceholder}
              autoComplete={step === "name" ? "name" : step === "contact" ? "email" : "off"}
              disabled={typing || step === "submitting" || step === "done"}
              data-testid="mae-input"
            />
            <button type="submit" className="mae-send" disabled={typing || step === "submitting" || step === "done"} data-testid="mae-send">
              {K("send")}
            </button>
          </form>
        </div>
      )}

      {!open && bubble && (
        <div className="mae-bubble" role="status">
          <div>{bubble === "greet" ? K("greet_bubble") : K("nudge_bubble")}</div>
          <div className="mae-bubble-actions">
            <button type="button" className="mae-bubble-cta" onClick={openPanel} data-testid="mae-bubble-cta">{K("start_cta")}</button>
            <button
              type="button"
              className="mae-bubble-x"
              aria-label={K("close_label")}
              onClick={() => { proactive.current = MAX_PROACTIVE; setBubble(null); }}
            >
              ✕
            </button>
          </div>
        </div>
      )}

      {!open && (
        <div className="mae-fab-wrap">
          <button type="button" className="mae-fab" onClick={openPanel} aria-label={K("open_label")} data-testid="mae-fab">
            <MaeSvg size={52} />
          </button>
          <button type="button" className="mae-dismiss" onClick={dismissForever} aria-label={K("dismiss_label")} data-testid="mae-dismiss">×</button>
        </div>
      )}
    </div>
  );
}
