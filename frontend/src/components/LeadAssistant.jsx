import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errMessage } from "@/lib/api";
import { waitlistErrorText } from "@/lib/waitlistErrors";
import TurnstileWidget from "@/components/TurnstileWidget";
import { LEAD_ASSISTANT_COPY } from "@/i18n/leadAssistant";
import { resolveSkin } from "@/components/mascotSkins";
import "./LeadAssistant.css";

// Mae — the landing page's animated AI-assistant mascot and conversational lead capture.
// Loaded with React.lazy from Landing.jsx so the landing bundle doesn't pay for it up front.
// The chat feeds the SAME lead path as the page's own form: POST /public/waitlist with the
// form's exact payload shape ({ email, name, business_name, industry, note, website,
// turnstile_token }); the qualification answers ride along in `note`, the way the instant-quote
// estimator already encodes its context there.
//
// v2: Mae is a FULL-BODY character who lives in a stage strip along the bottom of the
// viewport. A small JS state machine (idle / walk / wave / point / dance / alive / duty)
// walks her back and forth, pauses, waves, and occasionally dances; clicking her runs the
// "come alive" sequence (hop + spin + wave) and opens the chat, where she stands on duty
// beside the panel. All locomotion is transform-only on a 50 ms stepper (smoothed by a CSS
// transition), so there is no layout thrash. Characters are SKINS over one shared
// animation rig — see components/mascotSkins.js for the catalogue and the plan gate.
// Reduced-motion visitors get v1 behaviour: Mae stands still in the corner.

const DISMISS_KEY = "mae.dismissed.v1";
const MAX_PROACTIVE = 2;

const MAE_W = 76; // walker (character) width in px; height follows the SVG aspect
const STEP_MS = 50; // locomotion stepper tick
const WALK_SPEED = 62; // px/s on patrol
const HURRY_SPEED = 170; // px/s when hurrying to her duty dock / the form
const DANCE_GAP_MS = 45000; // at most one dance per ~45 s, never twice in a row

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

/* ------------------------------------------------------------------ *
 * The character. One shared rig for every skin: the pose groups
 * (.mae-leg-l/.mae-leg-r, .mae-arm-point/.mae-arm-wave, .mae-head,
 * .mae-eyes, .mae-upper) exist with identical geometry in all three
 * artworks, so the CSS animation classes drive any skin unchanged.
 * A skin swaps only fills / props / accessories inside those groups.
 * ------------------------------------------------------------------ */
function MaeSvg({ size = 76, skin = "mae", crop = false }) {
  const height = crop ? size : Math.round((size * 136) / 96);
  const viewBox = crop ? "6 0 84 78" : "0 0 96 136";

  const face = (eyes) => (
    <>
      {eyes}
      <ellipse cx="29.5" cy="44.5" rx="4" ry="2.4" fill="#f9a8d4" opacity="0.55" />
      <ellipse cx="66.5" cy="44.5" rx="4" ry="2.4" fill="#f9a8d4" opacity="0.55" />
      <path d="M41.5 46.5 q6.5 5.5 13 0" fill="none" stroke="#16233c" strokeWidth="3" strokeLinecap="round" />
    </>
  );
  const dotEyes = (
    <g className="mae-eyes" fill="#16233c">
      <circle cx="38" cy="36" r="3.6" />
      <circle cx="58" cy="36" r="3.6" />
    </g>
  );

  let legs;
  let arms;
  let torso;
  let head;

  if (skin === "sterling") {
    // Sterling — Empire-tier: tailored charcoal suit, mint tie, shades, dark gloves.
    legs = (
      <>
        <g className="mae-leg mae-leg-l">
          <rect x="35.5" y="90" width="9" height="31" rx="4.5" fill="#26292f" />
          <rect x="32" y="116" width="16" height="8" rx="4" fill="#10151d" />
          <line x1="35" y1="119" x2="43" y2="119" stroke="rgba(255,255,255,0.25)" strokeWidth="1.4" strokeLinecap="round" />
        </g>
        <g className="mae-leg mae-leg-r">
          <rect x="51.5" y="90" width="9" height="31" rx="4.5" fill="#26292f" />
          <rect x="48" y="116" width="16" height="8" rx="4" fill="#10151d" />
          <line x1="51" y1="119" x2="59" y2="119" stroke="rgba(255,255,255,0.25)" strokeWidth="1.4" strokeLinecap="round" />
        </g>
      </>
    );
    arms = (
      <>
        <g className="mae-arm-point">
          <line x1="30" y1="68" x2="18" y2="82" stroke="#2e3138" strokeWidth="7.5" strokeLinecap="round" />
          <circle cx="16.5" cy="84" r="4.6" fill="#16233c" />
        </g>
        <g className="mae-arm-wave">
          <line x1="66" y1="68" x2="80" y2="56" stroke="#2e3138" strokeWidth="7.5" strokeLinecap="round" />
          <circle cx="82" cy="54" r="4.6" fill="#16233c" />
        </g>
      </>
    );
    torso = (
      <>
        <rect x="27" y="61" width="42" height="33" rx="11" fill="#2e3138" />
        <path d="M40 61 h16 l-8 15 z" fill="#f4f4f5" />
        <path d="M33 61 l8 1 l-3 13 l-7 -9 z" fill="#22252b" />
        <path d="M63 61 l-8 1 l3 13 l7 -9 z" fill="#22252b" />
        <rect x="45.8" y="62.5" width="4.4" height="4.4" rx="1" fill="#34d399" />
        <path d="M48 67 l3.4 3.8 L48 79 l-3.4 -8.2 z" fill="#34d399" />
        <circle cx="48" cy="85" r="1.7" fill="#101216" />
      </>
    );
    head = (
      <>
        <rect x="18" y="13" width="60" height="47" rx="17" fill="#eef3fa" />
        <path d="M18 26 a30 20 0 0 1 60 0 z" fill="#3a3f47" />
        {face(
          <g className="mae-eyes">
            <line x1="26" y1="34" x2="19" y2="32" stroke="#11161d" strokeWidth="3" strokeLinecap="round" />
            <line x1="70" y1="34" x2="77" y2="32" stroke="#11161d" strokeWidth="3" strokeLinecap="round" />
            <rect x="26" y="30" width="19" height="12" rx="6" fill="#11161d" />
            <rect x="51" y="30" width="19" height="12" rx="6" fill="#11161d" />
            <rect x="45" y="34" width="6" height="3" fill="#11161d" />
            <line x1="30" y1="34.5" x2="38" y2="34.5" stroke="rgba(255,255,255,0.35)" strokeWidth="1.6" strokeLinecap="round" />
          </g>,
        )}
      </>
    );
  } else if (skin === "sloane") {
    // Sloane — Empire-tier: tailored blazer suit, shades, gloves; Sterling's counterpart.
    legs = (
      <>
        <g className="mae-leg mae-leg-l">
          <rect x="35.5" y="90" width="9" height="31" rx="4.5" fill="#2b2833" />
          <rect x="32" y="115" width="16" height="7" rx="3.5" fill="#10151d" />
          <rect x="43" y="121" width="4" height="3.5" fill="#10151d" />
        </g>
        <g className="mae-leg mae-leg-r">
          <rect x="51.5" y="90" width="9" height="31" rx="4.5" fill="#2b2833" />
          <rect x="48" y="115" width="16" height="7" rx="3.5" fill="#10151d" />
          <rect x="59" y="121" width="4" height="3.5" fill="#10151d" />
        </g>
      </>
    );
    arms = (
      <>
        <g className="mae-arm-point">
          <line x1="30" y1="68" x2="18" y2="82" stroke="#34313b" strokeWidth="7.5" strokeLinecap="round" />
          <circle cx="16.5" cy="84" r="4.6" fill="#16233c" />
        </g>
        <g className="mae-arm-wave">
          <line x1="66" y1="68" x2="80" y2="56" stroke="#34313b" strokeWidth="7.5" strokeLinecap="round" />
          <circle cx="82" cy="54" r="4.6" fill="#16233c" />
        </g>
      </>
    );
    torso = (
      <>
        <rect x="27" y="61" width="42" height="33" rx="11" fill="#34313b" />
        <path d="M41 61 h14 l-7 13 z" fill="#f4f4f5" />
        <path d="M33 61 l8 1 l-3 13 l-7 -9 z" fill="#262330" />
        <path d="M63 61 l-8 1 l3 13 l7 -9 z" fill="#262330" />
        <line x1="48" y1="64" x2="48" y2="70" stroke="#34d399" strokeWidth="1.2" />
        <circle cx="48" cy="72" r="2" fill="#34d399" />
        <circle cx="48" cy="86" r="1.7" fill="#17151d" />
      </>
    );
    head = (
      <>
        <rect x="13.5" y="11" width="69" height="54" rx="22" fill="#5b4030" />
        <rect x="21" y="15" width="54" height="45" rx="16" fill="#eef3fa" />
        <circle cx="19.5" cy="48" r="1.8" fill="#34d399" />
        <circle cx="76.5" cy="48" r="1.8" fill="#34d399" />
        {face(
          <g className="mae-eyes">
            <line x1="27" y1="34" x2="21.5" y2="32.5" stroke="#11161d" strokeWidth="3" strokeLinecap="round" />
            <line x1="69" y1="34" x2="74.5" y2="32.5" stroke="#11161d" strokeWidth="3" strokeLinecap="round" />
            <rect x="27" y="30" width="17" height="11" rx="5.5" fill="#11161d" />
            <rect x="52" y="30" width="17" height="11" rx="5.5" fill="#11161d" />
            <rect x="44" y="33.5" width="8" height="3" fill="#11161d" />
            <line x1="30.5" y1="34" x2="37" y2="34" stroke="rgba(255,255,255,0.35)" strokeWidth="1.6" strokeLinecap="round" />
          </g>,
        )}
      </>
    );
  } else if (skin === "foreman") {
    // The Foreman — Empire-tier: hard hat, hi-vis vest, clipboard, work gloves + boots.
    legs = (
      <>
        <g className="mae-leg mae-leg-l">
          <rect x="35.5" y="90" width="9" height="31" rx="4.5" fill="#475569" />
          <rect x="31.5" y="114" width="17" height="11" rx="3.5" fill="#292524" />
        </g>
        <g className="mae-leg mae-leg-r">
          <rect x="51.5" y="90" width="9" height="31" rx="4.5" fill="#475569" />
          <rect x="47.5" y="114" width="17" height="11" rx="3.5" fill="#292524" />
        </g>
      </>
    );
    arms = (
      <>
        <g className="mae-arm-point">
          <line x1="30" y1="68" x2="18" y2="82" stroke="#24466e" strokeWidth="7.5" strokeLinecap="round" />
          <circle cx="16.5" cy="84" r="4.6" fill="#57534e" />
        </g>
        <g className="mae-arm-wave">
          <line x1="66" y1="68" x2="80" y2="56" stroke="#24466e" strokeWidth="7.5" strokeLinecap="round" />
          <circle cx="82" cy="54" r="4.6" fill="#57534e" />
        </g>
      </>
    );
    torso = (
      <>
        <rect x="27" y="61" width="42" height="33" rx="11" fill="#24466e" />
        <rect x="27" y="61" width="42" height="33" rx="11" fill="#a3e635" opacity="0.96" />
        <rect x="31" y="61" width="4.5" height="33" fill="#e7e5e4" opacity="0.9" />
        <rect x="60.5" y="61" width="4.5" height="33" fill="#e7e5e4" opacity="0.9" />
        <rect x="27" y="79" width="42" height="4" fill="#e7e5e4" opacity="0.9" />
        <path d="M43 61 h10 l-2 33 h-6 z" fill="#24466e" />
        {/* clipboard tucked under the left arm */}
        <g transform="rotate(-8 15 76)">
          <rect x="7" y="65" width="16" height="22" rx="2" fill="#b45309" />
          <rect x="9.5" y="68" width="11" height="15.5" fill="#f8fafc" />
          <rect x="12.5" y="63.5" width="5" height="4" rx="1.5" fill="#94a3b8" />
          <line x1="11.5" y1="72" x2="18.5" y2="72" stroke="#cbd5e1" strokeWidth="1.4" />
          <line x1="11.5" y1="75.5" x2="18.5" y2="75.5" stroke="#cbd5e1" strokeWidth="1.4" />
          <line x1="11.5" y1="79" x2="16.5" y2="79" stroke="#cbd5e1" strokeWidth="1.4" />
        </g>
      </>
    );
    head = (
      <>
        <rect x="18" y="13" width="60" height="47" rx="17" fill="#eef3fa" />
        {face(
          <g className="mae-eyes" fill="#16233c">
            <circle cx="38" cy="38" r="3.6" />
            <circle cx="58" cy="38" r="3.6" />
          </g>,
        )}
        <path d="M16 30 a32 24 0 0 1 64 0 z" fill="#fbbf24" />
        <rect x="43.5" y="3" width="9" height="9" rx="3" fill="#f59e0b" />
        <rect x="10" y="28" width="76" height="6" rx="3" fill="#f59e0b" />
      </>
    );
  } else if (skin === "mo") {
    // Mo — base roster, every plan: Mae's guy counterpart. Same front desk (headset,
    // antenna, navy body, same friendly face) with a squarer head, a short-hair
    // hairline, and brows.
    legs = (
      <>
        <g className="mae-leg mae-leg-l">
          <rect x="35.5" y="90" width="9" height="31" rx="4.5" fill="#1d3a5f" />
          <rect x="32" y="116" width="16" height="8" rx="4" fill="#34d399" />
        </g>
        <g className="mae-leg mae-leg-r">
          <rect x="51.5" y="90" width="9" height="31" rx="4.5" fill="#1d3a5f" />
          <rect x="48" y="116" width="16" height="8" rx="4" fill="#34d399" />
        </g>
      </>
    );
    arms = (
      <>
        <g className="mae-arm-point">
          <line x1="30" y1="68" x2="18" y2="82" stroke="#24466e" strokeWidth="7.5" strokeLinecap="round" />
          <circle cx="16.5" cy="84" r="4.6" fill="#eef3fa" />
        </g>
        <g className="mae-arm-wave">
          <line x1="66" y1="68" x2="80" y2="56" stroke="#24466e" strokeWidth="7.5" strokeLinecap="round" />
          <circle cx="82" cy="54" r="4.6" fill="#eef3fa" />
        </g>
      </>
    );
    torso = (
      <>
        <rect x="27" y="61" width="42" height="33" rx="11" fill="#24466e" />
        <rect x="38.5" y="68" width="19" height="11" rx="3" fill="rgba(255,255,255,0.16)" />
        <polyline points="41,73.5 44.5,73.5 46.5,70.5 48.5,76.5 50,73.5 53,73.5" fill="none" stroke="#34d399" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      </>
    );
    head = (
      <>
        <line x1="48" y1="13" x2="48" y2="6" stroke="#8fb4dd" strokeWidth="2.5" strokeLinecap="round" />
        <circle className="mae-antenna-dot" cx="48" cy="5" r="3.4" fill="#34d399" />
        <rect x="18" y="13" width="60" height="47" rx="10" fill="#eef3fa" />
        <path d="M19.5 29 a28.5 20 0 0 1 57 0 l0 -5.5 a28.5 20 0 0 0 -57 0 z" fill="#3a3f47" />
        <path d="M19 39 v-5 a29 21 0 0 1 58 0 v5" fill="none" stroke="#34d399" strokeWidth="5" strokeLinecap="round" />
        <rect x="12.5" y="35" width="9.5" height="17" rx="4.5" fill="#34d399" />
        <rect x="74" y="35" width="9.5" height="17" rx="4.5" fill="#34d399" />
        <path d="M79 52 q1.5 10.5 -10.5 11.5" fill="none" stroke="#34d399" strokeWidth="3.5" strokeLinecap="round" />
        <circle cx="66.5" cy="63.5" r="3.2" fill="#34d399" />
        <g className="mae-eyes" fill="#16233c">
          <circle cx="38" cy="37" r="3.6" />
          <circle cx="58" cy="37" r="3.6" />
        </g>
        <rect x="29.5" y="29.5" width="14" height="3.2" rx="1.6" fill="#3a3f47" />
        <rect x="52.5" y="29.5" width="14" height="3.2" rx="1.6" fill="#3a3f47" />
        <ellipse cx="29.5" cy="45.5" rx="4" ry="2.4" fill="#f9a8d4" opacity="0.35" />
        <ellipse cx="66.5" cy="45.5" rx="4" ry="2.4" fill="#f9a8d4" opacity="0.35" />
        <path d="M41.5 47.5 q6.5 5.5 13 0" fill="none" stroke="#16233c" strokeWidth="3" strokeLinecap="round" />
      </>
    );
  } else {
    // Mae — the default skin, every plan: headset bot, mint accents.
    legs = (
      <>
        <g className="mae-leg mae-leg-l">
          <rect x="35.5" y="90" width="9" height="31" rx="4.5" fill="#1d3a5f" />
          <rect x="32" y="116" width="16" height="8" rx="4" fill="#34d399" />
        </g>
        <g className="mae-leg mae-leg-r">
          <rect x="51.5" y="90" width="9" height="31" rx="4.5" fill="#1d3a5f" />
          <rect x="48" y="116" width="16" height="8" rx="4" fill="#34d399" />
        </g>
      </>
    );
    arms = (
      <>
        <g className="mae-arm-point">
          <line x1="30" y1="68" x2="18" y2="82" stroke="#24466e" strokeWidth="7.5" strokeLinecap="round" />
          <circle cx="16.5" cy="84" r="4.6" fill="#eef3fa" />
        </g>
        <g className="mae-arm-wave">
          <line x1="66" y1="68" x2="80" y2="56" stroke="#24466e" strokeWidth="7.5" strokeLinecap="round" />
          <circle cx="82" cy="54" r="4.6" fill="#eef3fa" />
        </g>
      </>
    );
    torso = (
      <>
        <rect x="27" y="61" width="42" height="33" rx="11" fill="#24466e" />
        <rect x="38.5" y="68" width="19" height="11" rx="3" fill="rgba(255,255,255,0.16)" />
        <polyline points="41,73.5 44.5,73.5 46.5,70.5 48.5,76.5 50,73.5 53,73.5" fill="none" stroke="#34d399" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      </>
    );
    head = (
      <>
        <line x1="48" y1="13" x2="48" y2="6" stroke="#8fb4dd" strokeWidth="2.5" strokeLinecap="round" />
        <circle className="mae-antenna-dot" cx="48" cy="5" r="3.4" fill="#34d399" />
        <rect x="18" y="13" width="60" height="47" rx="17" fill="#eef3fa" />
        <path d="M19 39 v-5 a29 21 0 0 1 58 0 v5" fill="none" stroke="#34d399" strokeWidth="5" strokeLinecap="round" />
        <rect x="12.5" y="35" width="9.5" height="17" rx="4.5" fill="#34d399" />
        <rect x="74" y="35" width="9.5" height="17" rx="4.5" fill="#34d399" />
        <path d="M79 52 q1.5 10.5 -10.5 11.5" fill="none" stroke="#34d399" strokeWidth="3.5" strokeLinecap="round" />
        <circle cx="66.5" cy="63.5" r="3.2" fill="#34d399" />
        {face(dotEyes)}
      </>
    );
  }

  return (
    <svg
      className="mae-bot"
      width={size}
      height={height}
      viewBox={viewBox}
      data-skin={skin}
      aria-hidden="true"
      focusable="false"
    >
      <g data-skin-art={skin}>
        {legs}
        <g className="mae-upper">
          {arms}
          {torso}
          <g className="mae-head">{head}</g>
        </g>
      </g>
    </svg>
  );
}

export default function LeadAssistant({
  t: tProp,
  presetIndustry = "",
  waitlistOnly = false,
  onShowPricing,
  onShowForm,
  plan = "",
  skin = "",
}) {
  const { t: tOwn } = useTranslation();
  const t = tProp || tOwn;
  // Mascot copy ships as a plain EN strings module (see i18n/leadAssistant.js for why it is not
  // an i18next bundle); only two shared labels — the success title and the "AI online" status —
  // come from the page's locale via t.
  const K = useCallback((key, opts) => fillTemplate(LEAD_ASSISTANT_COPY[key] || key, opts), []);
  // Skin gate: the requested skin renders only when the plan unlocks it (see mascotSkins.js).
  // Marketing pages pass no plan → Mae only.
  const skinId = resolveSkin(skin, plan);

  const [dismissed, setDismissed] = useState(() => {
    try { return !!window.localStorage.getItem(DISMISS_KEY); } catch { return false; }
  });
  const [open, setOpen] = useState(false);
  const [bubble, setBubble] = useState(null); // "greet" | "nudge"
  const [pose, setPose] = useState("idle"); // idle|walk|wave|point|dance|alive|duty
  const [zone, setZone] = useState("right"); // bubble anchor zone, from Mae's x position
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

  /* ---------------- stage / locomotion state ---------------- */
  const poseRef = useRef("idle");
  const openRef = useRef(false);
  openRef.current = open;
  const dismissedRef = useRef(false);
  dismissedRef.current = dismissed;
  const walkerRef = useRef(null);
  const flipRef = useRef(null);
  const posRef = useRef({ x: 0, dir: 1 });
  const zoneRef = useRef("right");
  const moveRef = useRef({ timer: null });
  const routineRef = useRef(null);
  const lastDanceRef = useRef(0);
  const lastActionRef = useRef("");
  const formInViewRef = useRef(false);

  const later = useCallback((fn, ms) => {
    const id = setTimeout(fn, ms);
    timers.current.push(id);
    return id;
  }, []);

  useEffect(() => {
    const stash = timers.current;
    return () => stash.forEach((id) => clearTimeout(id));
  }, []);

  // Never leave a locomotion interval running on an unmounted mascot.
  useEffect(() => () => {
    if (moveRef.current.timer) clearInterval(moveRef.current.timer);
    if (routineRef.current) clearTimeout(routineRef.current);
  }, []);

  const getBounds = useCallback(() => {
    const vw = typeof window !== "undefined" && window.innerWidth ? window.innerWidth : 1024;
    const small = vw < 640;
    const max = Math.max(0, vw - MAE_W - 10);
    const min = small ? Math.round(vw * 0.4) : 12;
    return { vw, small, min: Math.min(min, max), max };
  }, []);

  const applyFlip = useCallback(() => {
    if (!flipRef.current) return;
    const walking = poseRef.current === "walk";
    flipRef.current.style.transform = `scaleX(${walking ? posRef.current.dir : 1})`;
  }, []);

  const setPoseBoth = useCallback((p) => {
    poseRef.current = p;
    setPose(p);
    applyFlip();
  }, [applyFlip]);

  const setWalkerX = useCallback((x) => {
    posRef.current.x = x;
    if (walkerRef.current) walkerRef.current.style.transform = `translate3d(${x}px, 0, 0)`;
    const { vw } = getBounds();
    const z = x < vw * 0.28 ? "left" : x > vw - 300 ? "right" : "mid";
    if (z !== zoneRef.current) {
      zoneRef.current = z;
      setZone(z);
    }
  }, [getBounds]);

  const stopMove = useCallback(() => {
    if (moveRef.current.timer) {
      clearInterval(moveRef.current.timer);
      moveRef.current.timer = null;
    }
  }, []);

  // Transform-only locomotion: a 50 ms stepper nudges translate3d toward the target;
  // the CSS transition on .mae-walker smooths the steps into a glide.
  const moveTo = useCallback((targetX, speed, onArrive) => {
    stopMove();
    const { min, max } = getBounds();
    const target = Math.max(min, Math.min(max, targetX));
    const stepFn = () => {
      const cur = posRef.current.x;
      const dx = target - cur;
      const stepPx = Math.max(1, speed * (STEP_MS / 1000));
      if (Math.abs(dx) <= stepPx) {
        setWalkerX(target);
        stopMove();
        if (onArrive) onArrive();
        return;
      }
      const dir = dx > 0 ? 1 : -1;
      if (dir !== posRef.current.dir) {
        posRef.current.dir = dir;
        applyFlip();
      }
      setWalkerX(cur + dir * stepPx);
    };
    moveRef.current.timer = setInterval(stepFn, STEP_MS);
    stepFn();
  }, [applyFlip, getBounds, setWalkerX, stopMove]);

  const clearRoutine = useCallback(() => {
    if (routineRef.current) {
      clearTimeout(routineRef.current);
      routineRef.current = null;
    }
  }, []);

  const stopAmbient = useCallback(() => {
    clearRoutine();
    stopMove();
  }, [clearRoutine, stopMove]);

  const ambientOk = useCallback(() => (
    !dismissedRef.current
    && !reducedMotion.current
    && !openRef.current
    && !formInViewRef.current
    && !(typeof document !== "undefined" && document.hidden)
  ), []);

  const scheduleRoutine = useCallback((delay) => {
    clearRoutine();
    if (!ambientOk()) return;
    routineRef.current = setTimeout(() => {
      routineRef.current = null;
      if (!ambientOk()) return;
      const now = Date.now();
      const r = Math.random();
      const danceOk = now - lastDanceRef.current >= DANCE_GAP_MS && lastActionRef.current !== "dance";
      let action;
      if (r < 0.5) action = "walk";
      else if (r < 0.75) action = "wave";
      else if (danceOk) action = "dance";
      else action = lastActionRef.current === "wave" ? "walk" : "wave";

      if (action === "walk") {
        lastActionRef.current = "walk";
        const { vw, min, max } = getBounds();
        const pad = vw >= 760 ? 120 : 0;
        const lo = Math.min(min + pad, max);
        const hi = Math.max(max - pad, lo);
        let target = lo + Math.random() * Math.max(0, hi - lo);
        if (Math.abs(target - posRef.current.x) < 130) {
          target = posRef.current.x < (lo + hi) / 2 ? hi : lo;
        }
        setPoseBoth("walk");
        moveTo(target, WALK_SPEED, () => {
          setPoseBoth("idle");
          if (ambientOk()) scheduleRoutine(2200 + Math.random() * 2600);
        });
      } else if (action === "wave") {
        lastActionRef.current = "wave";
        setPoseBoth("wave");
        routineRef.current = setTimeout(() => {
          routineRef.current = null;
          setPoseBoth("idle");
          if (ambientOk()) scheduleRoutine(2600 + Math.random() * 3000);
        }, 2600);
      } else {
        lastActionRef.current = "dance";
        lastDanceRef.current = now;
        setPoseBoth("dance");
        routineRef.current = setTimeout(() => {
          routineRef.current = null;
          setPoseBoth("idle");
          if (ambientOk()) scheduleRoutine(3200 + Math.random() * 3000);
        }, 2350);
      }
    }, delay);
  }, [ambientOk, clearRoutine, getBounds, moveTo, setPoseBoth]);

  // Walk to the lead form and point at it (form scrolled into view).
  const goPointAtForm = useCallback(() => {
    if (!ambientOk() && openRef.current) return;
    const el = typeof document !== "undefined" ? document.getElementById("waitlist") : null;
    if (!el) return;
    stopAmbient();
    const rect = el.getBoundingClientRect();
    const target = rect.left + rect.width * 0.72 - MAE_W / 2;
    setPoseBoth("walk");
    moveTo(target, WALK_SPEED, () => {
      if (formInViewRef.current && !openRef.current) setPoseBoth("point");
    });
  }, [ambientOk, moveTo, setPoseBoth, stopAmbient]);

  /* ---------------- chat behaviour (v1, unchanged) ---------------- */

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
    setOpen(true);
    if (stepRef.current === "idle") startFlow();
  }, [startFlow]);

  const closePanel = useCallback(() => {
    setOpen(false);
    stopAmbient();
    if (poseRef.current === "duty" || poseRef.current === "alive") setPoseBoth("idle");
    // Resume her patrol (or form-pointing) a few seconds after the panel closes.
    later(() => {
      if (openRef.current || dismissedRef.current || reducedMotion.current) return;
      if (formInViewRef.current) goPointAtForm();
      else scheduleRoutine(800);
    }, 2800);
  }, [goPointAtForm, later, scheduleRoutine, setPoseBoth, stopAmbient]);

  // Click / tap / Enter / Space on Mae: she comes alive — hop, spin, wave, greet —
  // and the chat opens with her hurrying to her duty spot beside the panel.
  const activate = useCallback(() => {
    openedOnce.current = true;
    proactive.current = MAX_PROACTIVE;
    stopAmbient();
    if (reducedMotion.current) {
      setPoseBoth("duty");
      openPanel();
      return;
    }
    setPoseBoth("alive");
    setBubble("greet");
    later(() => setBubble((b) => (b === "greet" ? null : b)), 1900);
    openPanel();
    later(() => {
      if (!openRef.current) return;
      const { vw, max } = getBounds();
      const dock = vw >= 640 ? Math.max(12, vw - MAE_W - 400) : max;
      if (Math.abs(posRef.current.x - dock) < 24) {
        setPoseBoth("duty");
        return;
      }
      setPoseBoth("walk");
      moveTo(dock, HURRY_SPEED, () => {
        if (openRef.current) setPoseBoth("duty");
      });
      later(() => {
        if (openRef.current && poseRef.current === "walk") {
          stopMove();
          setPoseBoth("duty");
        }
      }, 4000);
    }, 950);
  }, [getBounds, later, moveTo, openPanel, setPoseBoth, stopAmbient, stopMove]);

  const dismissForever = useCallback(() => {
    try { window.localStorage.setItem(DISMISS_KEY, String(Date.now())); } catch { /* private mode */ }
    stopAmbient();
    setDismissed(true);
    setBubble(null);
    setOpen(false);
  }, [stopAmbient]);

  const browse = useCallback(() => {
    closePanel();
    setBubble(null);
    if (onShowPricing) onShowPricing();
  }, [closePanel, onShowPricing]);

  const useFormInstead = useCallback(() => {
    closePanel();
    if (onShowForm) onShowForm();
  }, [closePanel, onShowForm]);

  /* ---------------- stage setup: park her at the right, start the routine ---------------- */

  useEffect(() => {
    setWalkerX(getBounds().max);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (dismissed || reducedMotion.current) return undefined;
    scheduleRoutine(6500); // after the intro wave + greet bubble have had their moment
    return () => clearRoutine();
  }, [dismissed, scheduleRoutine, clearRoutine]);

  useEffect(() => {
    const onResize = () => {
      const { min, max } = getBounds();
      setWalkerX(Math.max(min, Math.min(max, posRef.current.x)));
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [getBounds, setWalkerX]);

  useEffect(() => {
    const onVis = () => {
      if (document.hidden) {
        stopAmbient();
        if (["walk", "wave", "dance"].includes(poseRef.current)) setPoseBoth("idle");
      } else if (!openRef.current) {
        scheduleRoutine(1200);
      }
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, [scheduleRoutine, setPoseBoth, stopAmbient]);

  /* ---------------- form-in-view: walk over and point at the lead form ---------------- */

  useEffect(() => {
    if (dismissed || reducedMotion.current) return undefined;
    if (typeof IntersectionObserver === "undefined") return undefined;
    const el = document.getElementById("waitlist");
    if (!el) return undefined;
    const io = new IntersectionObserver((entries) => {
      const vis = entries.some((e) => e.isIntersecting);
      if (vis === formInViewRef.current) return;
      formInViewRef.current = vis;
      if (openRef.current || dismissedRef.current) return;
      if (vis) {
        goPointAtForm();
      } else {
        if (poseRef.current === "point" || poseRef.current === "walk") {
          stopAmbient();
          setPoseBoth("idle");
        }
        scheduleRoutine(1500);
      }
    }, { threshold: 0.2 });
    io.observe(el);
    return () => io.disconnect();
  }, [dismissed, goPointAtForm, scheduleRoutine, setPoseBoth, stopAmbient]);

  /* ---------------- proactive intro + nudges (v1 triggers, pose-aware) ---------------- */

  useEffect(() => {
    if (dismissed || reducedMotion.current) return undefined;
    const ids = [];
    const after = (fn, ms) => { ids.push(setTimeout(fn, ms)); };
    after(() => {
      if (!openedOnce.current && poseRef.current === "idle") {
        setPoseBoth("wave");
        after(() => { if (poseRef.current === "wave") setPoseBoth("idle"); }, 2900);
      }
    }, 900);
    after(() => {
      if (openedOnce.current || proactive.current >= MAX_PROACTIVE) return;
      proactive.current += 1;
      setBubble("greet");
      if (poseRef.current === "idle") {
        setPoseBoth("point");
        after(() => { if (poseRef.current === "point") setPoseBoth("idle"); }, 2400);
      }
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
  }, [dismissed, setPoseBoth]);

  /* ---------------- panel niceties ---------------- */

  useEffect(() => {
    if (msgsRef.current) msgsRef.current.scrollTop = msgsRef.current.scrollHeight;
  }, [messages, typing, open]);

  useEffect(() => {
    if (open && inputRef.current) inputRef.current.focus({ preventScroll: true });
  }, [open, step]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === "Escape") closePanel(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, closePanel]);

  if (dismissed) return null;

  const shellCls = ["mae-shell"]
    .concat([`is-${pose}`])
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
    <div className={shellCls} data-testid="lead-assistant">
      {open && (
        <div className="mae-root">
          <div className="mae-panel" role="dialog" aria-label={K("panel_title")}>
            <div className="mae-panel-head">
              <MaeSvg size={34} skin={skinId} crop />
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
        </div>
      )}

      <div
        className={`mae-stage${reducedMotion.current ? " is-static" : ""}`}
        data-testid="mae-stage"
        data-pose={pose}
        data-skin={skinId}
      >
        <div className={`mae-walker mae-zone-${zone}`} ref={walkerRef}>
          {bubble && (
            <div className="mae-bubble" role="status">
              <div>{bubble === "greet" ? K("greet_bubble") : K("nudge_bubble")}</div>
              <div className="mae-bubble-actions">
                <button type="button" className="mae-bubble-cta" onClick={activate} data-testid="mae-bubble-cta">{K("start_cta")}</button>
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
          <div className="mae-scale">
            <div className="mae-flip" ref={flipRef}>
              <button type="button" className="mae-fab" onClick={activate} aria-label={K("open_label")} data-testid="mae-fab">
                <MaeSvg size={MAE_W} skin={skinId} />
                <span className="mae-shadow" aria-hidden="true" />
              </button>
            </div>
          </div>
          <button type="button" className="mae-dismiss" onClick={dismissForever} aria-label={K("dismiss_label")} data-testid="mae-dismiss">×</button>
        </div>
      </div>
    </div>
  );
}
