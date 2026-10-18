// Industry landing pages (/hvac, /plumbing, ... and /es/<slug>). DRAFT COPY for Brann's review.
// Rule: only claim what works in the waitlist-only build today (the early-access form). Each
// claim below maps to evidence listed in the PR. Do not add product-feature claims here until
// those features are live and verified.
//
// ONE shared template, driven by the packs (owner audit, Track C, 2026-10-17): there are no
// per-industry page files. Every one of the 33 backend trade packs (backend/trade_packs/packs)
// gets a landing route from this table — the slug is the pack id hyphenated (appliance_repair ->
// appliance-repair), matching the brand subdomain convention in config/brand.js. Adding a pack
// means adding one row here; routes in App.js / waitlist/WaitlistApp.jsx and the sitemap in
// site-meta.js all derive from INDUSTRY_SLUGS / INDUSTRY_PATHS.
//
// es labels follow the company's Spanish trade names (backend/locales/es_mx_trades.yaml, the
// per-pack overlay) for the 28 packs added in Track C; the original five keep their reviewed
// neutral-Spanish labels. es.noun is used mid-sentence ("negocios de ..."), so it keeps its
// own capitalization.

const { BRAND_NAME } = require("./brand");

// slug -> backend trade-pack id (backend/trade_packs/packs/<id>.yaml).
const INDUSTRY_PACK_IDS = {
  hvac: "hvac",
  plumbing: "plumbing",
  electrical: "electrical",
  roofing: "roofing",
  landscaping: "landscaping",
  "appliance-repair": "appliance_repair",
  "auto-repair": "auto_repair",
  barbershop: "barbershop",
  cleaning: "cleaning",
  "decks-outdoor": "decks_outdoor",
  "fencing-concrete": "fencing_concrete",
  flooring: "flooring",
  "foundation-repair": "foundation_repair",
  freelancer: "freelancer",
  "garage-door": "garage_door",
  generators: "generators",
  handyman: "handyman",
  "home-inspection": "home_inspection",
  irrigation: "irrigation",
  "junk-removal": "junk_removal",
  "kitchen-bath": "kitchen_bath",
  locksmith: "locksmith",
  painting: "painting",
  "pest-control": "pest_control",
  "pool-service": "pool_service",
  "real-estate": "real_estate",
  restoration: "restoration",
  "septic-well": "septic_well",
  "siding-gutters": "siding_gutters",
  "smart-home": "smart_home",
  solar: "solar",
  "tree-service": "tree_service",
  "windows-doors": "windows_doors",
};

const INDUSTRY_SLUGS = Object.keys(INDUSTRY_PACK_IDS);

const NAMES = {
  // es.noun is used mid-sentence ("negocios de ..."), so it keeps its own capitalization.
  hvac: { en: "HVAC", es: "HVAC (climatización)", esNoun: "HVAC y climatización" },
  plumbing: { en: "Plumbing", es: "Plomería", esNoun: "plomería" },
  electrical: { en: "Electrical", es: "Electricidad", esNoun: "electricidad" },
  roofing: { en: "Roofing", es: "Techos", esNoun: "techos" },
  landscaping: { en: "Landscaping", es: "Jardinería y paisajismo", esNoun: "jardinería y paisajismo" },
  "appliance-repair": { en: "Appliance Repair", es: "Reparación de línea blanca y electrodomésticos", esNoun: "reparación de línea blanca y electrodomésticos" },
  "auto-repair": { en: "Auto Repair", es: "Taller mecánico y reparación de autos", esNoun: "taller mecánico y reparación de autos" },
  barbershop: { en: "Barbershop", es: "Barbería", esNoun: "barbería" },
  cleaning: { en: "Cleaning", es: "Limpieza profesional", esNoun: "limpieza profesional" },
  "decks-outdoor": { en: "Decks & Outdoor Living", es: "Terrazas, pérgolas y exteriores", esNoun: "terrazas, pérgolas y exteriores" },
  "fencing-concrete": { en: "Fencing & Concrete", es: "Cercas y concreto", esNoun: "cercas y concreto" },
  flooring: { en: "Flooring", es: "Pisos y recubrimientos", esNoun: "pisos y recubrimientos" },
  "foundation-repair": { en: "Foundation Repair", es: "Reparación de cimientos y cimentaciones", esNoun: "reparación de cimientos y cimentaciones" },
  freelancer: { en: "Freelancer", es: "Servicios profesionales independientes", esNoun: "servicios profesionales independientes" },
  "garage-door": { en: "Garage Door", es: "Puertas de garaje y portones eléctricos", esNoun: "puertas de garaje y portones eléctricos" },
  generators: { en: "Whole-Home Generators", es: "Plantas de luz y generadores eléctricos", esNoun: "plantas de luz y generadores eléctricos" },
  handyman: { en: "Handyman", es: "Mantenimiento y reparaciones del hogar", esNoun: "mantenimiento y reparaciones del hogar" },
  "home-inspection": { en: "Home Inspection", es: "Inspección de vivienda", esNoun: "inspección de vivienda" },
  irrigation: { en: "Irrigation", es: "Sistemas de riego", esNoun: "sistemas de riego" },
  "junk-removal": { en: "Junk Removal", es: "Retiro de escombro y cacharros", esNoun: "retiro de escombro y cacharros" },
  "kitchen-bath": { en: "Kitchen & Bath Remodeling", es: "Remodelación de cocina y baño", esNoun: "remodelación de cocina y baño" },
  locksmith: { en: "Locksmith", es: "Cerrajería", esNoun: "cerrajería" },
  painting: { en: "Painting", es: "Pintura", esNoun: "pintura" },
  "pest-control": { en: "Pest Control", es: "Control de plagas y fumigación", esNoun: "control de plagas y fumigación" },
  "pool-service": { en: "Pool Service", es: "Mantenimiento de albercas", esNoun: "mantenimiento de albercas" },
  "real-estate": { en: "Real Estate", es: "Bienes raíces", esNoun: "bienes raíces" },
  restoration: { en: "Restoration", es: "Restauración por daños de agua e incendio", esNoun: "restauración por daños de agua e incendio" },
  "septic-well": { en: "Septic & Well", es: "Fosas sépticas, pozos y desazolve", esNoun: "fosas sépticas, pozos y desazolve" },
  "siding-gutters": { en: "Siding & Gutters", es: "Fachadas, lámina, tablaroca y canalones", esNoun: "fachadas, lámina, tablaroca y canalones" },
  "smart-home": { en: "Smart Home & Security", es: "Casa inteligente y automatización del hogar", esNoun: "casa inteligente y automatización del hogar" },
  solar: { en: "Solar", es: "Paneles solares y energía solar", esNoun: "paneles solares y energía solar" },
  "tree-service": { en: "Tree Service", es: "Poda y derribo de árboles", esNoun: "poda y derribo de árboles" },
  "windows-doors": { en: "Windows & Doors", es: "Ventanas, puertas y cancelería", esNoun: "ventanas, puertas y cancelería" },
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

module.exports = { INDUSTRY_SLUGS, INDUSTRY_PATHS, INDUSTRY_PACK_IDS, industryPage };
