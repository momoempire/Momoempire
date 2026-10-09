#!/usr/bin/env node
// EMP-WL-013: WCAG 2.x contrast check for the text/button colors used on the (dark) waitlist
// landing page. Backgrounds are the darkest/lightest surfaces measured in headless Chrome at
// 390 px and 1280 px (page gradient worst case, waitlist card, inputs, translucent panels).
// Usage: node scripts/waitlist-contrast.js   (exit 1 if any pair is below its minimum)

const PAGE = [38, 30, 73]; // lightest point of the page gradient behind text (worst case for white text)
const CARD = [21, 18, 36]; // waitlist card
const INPUT = [33, 30, 47]; // form fields (bg-white/5 on the card)

function over(fg, alpha, bg) {
  return fg.map((c, i) => Math.round(c * alpha + bg[i] * (1 - alpha)));
}
function luminance([r, g, b]) {
  const lin = (v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}
function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const WHITE = [255, 255, 255];
const PANEL5 = over(WHITE, 0.05, PAGE); // bg-white/5 panels on the page
const PANEL10 = over(WHITE, 0.1, PAGE); // bg-white/10 chips/badges on the page
const ROSE200 = [254, 205, 211]; // tailwind rose-200
const EMERALD100 = [209, 250, 229]; // tailwind emerald-100
const EMERALD_BG = over([52, 211, 153], 0.1, CARD); // bg-emerald-400/10 success box on the card

const TEXT = 4.5; // WCAG AA normal text
const UI = 3; // WCAG 1.4.11 non-text (input borders)

const alphaOn = (a, bg) => over(WHITE, a, bg);

const PAIRS = [
  ...[0.6, 0.7, 0.8].flatMap((a) => [
    [`text-white/${a * 100} on page`, alphaOn(a, PAGE), PAGE, TEXT],
    [`text-white/${a * 100} on card`, alphaOn(a, CARD), CARD, TEXT],
    [`text-white/${a * 100} on bg-white/5 panel`, alphaOn(a, PANEL5), PANEL5, TEXT],
    [`text-white/${a * 100} on bg-white/10 chip`, alphaOn(a, PANEL10), PANEL10, TEXT],
  ]),
  ["placeholder text-white/60 in input", alphaOn(0.6, INPUT), INPUT, TEXT],
  ["input text white in input", WHITE, INPUT, TEXT],
  [".overline (white 0.7) on page", alphaOn(0.7, PAGE), PAGE, TEXT],
  ["error text-rose-200 on card", ROSE200, CARD, TEXT],
  ["success text-emerald-100 on emerald box", EMERALD100, EMERALD_BG, TEXT],
  ["primary button text-black on bg-white", [0, 0, 0], WHITE, TEXT],
  ["primary button hover text-black on bg-white/90", [0, 0, 0], alphaOn(0.9, CARD), TEXT],
  ["input border-white/40 vs card", alphaOn(0.4, CARD), CARD, UI],
];

function results() {
  return PAIRS.map(([name, fg, bg, min]) => ({ name, ratio: Math.round(contrast(fg, bg) * 100) / 100, min }));
}

// Previous (pre-fix) values, kept for the report/tests.
const BEFORE = [
  ["text-white/40 on page (old step numbers, ©)", alphaOn(0.4, PAGE), PAGE, TEXT],
  ["text-white/50 on page (old footer tagline)", alphaOn(0.5, PAGE), PAGE, TEXT],
  ["old .overline hsl(220 10% 44%) on page", [101, 109, 124], PAGE, TEXT],
].map(([name, fg, bg, min]) => ({ name, ratio: Math.round(contrast(fg, bg) * 100) / 100, min }));

module.exports = { contrast, over, luminance, results, BEFORE, PAGE, CARD, INPUT };

if (require.main === module) {
  let bad = 0;
  for (const r of results()) {
    const ok = r.ratio >= r.min;
    if (!ok) bad += 1;
    console.log(`${ok ? "ok  " : "FAIL"} ${r.ratio.toFixed(2).padStart(5)}:1 (min ${r.min}) ${r.name}`);
  }
  console.log("\nbefore the fix:");
  for (const r of BEFORE) console.log(`     ${r.ratio.toFixed(2).padStart(5)}:1 ${r.name}`);
  process.exit(bad ? 1 : 0);
}
