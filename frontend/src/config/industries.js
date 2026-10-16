// Industry landing pages (/hvac, /plumbing, ... and /es/<slug>). DRAFT COPY for Brann's review.
// Rule: only claim what works in the waitlist-only build today (the early-access form). Each
// claim below maps to evidence listed in the PR. Do not add product-feature claims here until
// those features are live and verified.

const { BRAND_NAME } = require("./brand");

const INDUSTRY_SLUGS = ["hvac", "plumbing", "electrical", "roofing", "landscaping"];

const NAMES = {
  // es.noun is used mid-sentence ("negocios de ..."), so it keeps its own capitalization.
  hvac: { en: "HVAC", es: "HVAC (climatización)", esNoun: "HVAC y climatización" },
  plumbing: { en: "Plumbing", es: "Plomería", esNoun: "plomería" },
  electrical: { en: "Electrical", es: "Electricidad", esNoun: "electricidad" },
  roofing: { en: "Roofing", es: "Techos", esNoun: "techos" },
  landscaping: { en: "Landscaping", es: "Jardinería y paisajismo", esNoun: "jardinería y paisajismo" },
};

function copyFor(slug, lang) {
  const n = NAMES[slug][lang];
  if (lang === "es") {
    return {
      label: n,
      title: `Acceso anticipado para negocios de ${NAMES[slug].esNoun}.`,
      sub: `Estamos preparando ${BRAND_NAME} para ${NAMES[slug].esNoun}. Únase a la lista de acceso anticipado y le avisaremos cuando se abran lugares.`,
      pointsTitle: "Cómo funciona la lista",
      points: [
        "Un formulario corto: solo el email es obligatorio",
        "Un registro por email: sin duplicados",
        "Registro protegido contra spam",
        "Lea nuestra política de privacidad antes de unirse",
      ],
      formIndustry: NAMES[slug].en,
    };
  }
  return {
    label: n,
    title: `Early access for ${n} businesses.`,
    sub: `We're getting ${BRAND_NAME} ready for ${n} businesses. Join the early-access list and we'll reach out when seats open.`,
    pointsTitle: "How the list works",
    points: [
      "One short form: only your email is required",
      "One sign-up per email address: no duplicates",
      "Spam-protected sign-up",
      "Read our privacy policy before you join",
    ],
    formIndustry: n,
  };
}

function industryPage(slug, lang = "en") {
  if (!INDUSTRY_SLUGS.includes(slug)) return null;
  const l = lang === "es" ? "es" : "en";
  return { slug, lang: l, copy: copyFor(slug, l) };
}

// Every industry route, both languages: "/hvac", "/es/hvac", ...
const INDUSTRY_PATHS = INDUSTRY_SLUGS.flatMap((s) => [`/${s}`, `/es/${s}`]);

module.exports = { INDUSTRY_SLUGS, INDUSTRY_PATHS, industryPage };
