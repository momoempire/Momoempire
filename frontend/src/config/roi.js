// ROI calculator + workflow diagrams config, per niche. ALL NUMBERS ARE PLACEHOLDER ASSUMPTIONS
// for Brann to confirm. They are not measured results or product claims. Editing a niche's
// defaults/assumptions here changes every page that drops in <RoiCalculator niche="..." />.

const BASE_ASSUMPTIONS = {
  // Share of missed calls the assistant reaches (answers or follows up). Placeholder: 50%.
  recoveryRate: 0.5,
  // Share of reached callers who book a job. Placeholder: 25%.
  bookingRate: 0.25,
  // Staff minutes a handled call would otherwise take (answer, notes, call back). Placeholder: 5.
  minutesPerCall: 5,
};

const NICHES = {
  hvac: { defaults: { missedCalls: 20, jobValue: 300 }, assumptions: { ...BASE_ASSUMPTIONS } },
  plumbing: { defaults: { missedCalls: 20, jobValue: 250 }, assumptions: { ...BASE_ASSUMPTIONS } },
  electrical: { defaults: { missedCalls: 15, jobValue: 250 }, assumptions: { ...BASE_ASSUMPTIONS } },
  roofing: { defaults: { missedCalls: 10, jobValue: 1000 }, assumptions: { ...BASE_ASSUMPTIONS } },
  landscaping: { defaults: { missedCalls: 15, jobValue: 150 }, assumptions: { ...BASE_ASSUMPTIONS } },
};

// Niche pages that show the calculator + diagrams. HVAC first; add slugs once Brann confirms their defaults.
const ROI_PAGES = ["hvac"];

const MAX_INPUT = 1000000;

const COPY = {
  en: {
    title: "Estimate what missed calls may be costing you",
    intro: "Enter your own numbers. Everything is calculated in your browser; nothing is saved or sent.",
    missedCalls: "Average missed calls per month",
    jobValue: "Average job value ($)",
    revenue: "Estimated recovered revenue / month",
    time: "Estimated staff time saved / month",
    hours: "hours",
    assumptionsTitle: "Assumptions used (estimates, not guarantees)",
    recoveryRate: "Missed calls reached",
    bookingRate: "Reached callers who book",
    minutesPerCall: "Staff minutes per call",
    formula: "Revenue = missed calls × reached × book rate × job value. Time = missed calls × reached × minutes per call ÷ 60.",
    disclaimer: "Illustrative estimate only, based on the placeholder assumptions above. Your actual results will vary.",
    errRequired: "Enter a number.",
    errNegative: "Enter 0 or more.",
    errTooLarge: "Enter a smaller number.",
    errWhole: "Enter a whole number.",
    diagramsTitle: "How the assistant is designed to work (illustration)",
    diagramsNote: "Simplified diagrams of the planned workflow. Features roll out to early-access members over time.",
  },
  es: {
    title: "Calcule cuánto le pueden costar las llamadas perdidas",
    intro: "Ingrese sus propios números. Todo se calcula en su navegador; nada se guarda ni se envía.",
    missedCalls: "Promedio de llamadas perdidas al mes",
    jobValue: "Valor promedio por trabajo ($)",
    revenue: "Ingresos recuperados estimados / mes",
    time: "Tiempo de personal ahorrado estimado / mes",
    hours: "horas",
    assumptionsTitle: "Supuestos usados (estimaciones, no garantías)",
    recoveryRate: "Llamadas perdidas atendidas",
    bookingRate: "Clientes atendidos que reservan",
    minutesPerCall: "Minutos de personal por llamada",
    formula: "Ingresos = llamadas perdidas × atendidas × tasa de reserva × valor del trabajo. Tiempo = llamadas perdidas × atendidas × minutos por llamada ÷ 60.",
    disclaimer: "Solo es una estimación ilustrativa basada en los supuestos de arriba. Sus resultados reales pueden variar.",
    errRequired: "Ingrese un número.",
    errNegative: "Ingrese 0 o más.",
    errTooLarge: "Ingrese un número menor.",
    errWhole: "Ingrese un número entero.",
    diagramsTitle: "Cómo está diseñado el asistente (ilustración)",
    diagramsNote: "Diagramas simplificados del flujo planeado. Las funciones llegan a los miembros de acceso anticipado con el tiempo.",
  },
};

// Diagram steps per language. Generic across niches.
const DIAGRAMS = {
  en: [
    { id: "reception", title: "Customer reception", steps: ["Customer calls or texts", "Assistant answers and greets", "Captures the need and address", "Offers a time and alerts your team"] },
    { id: "sales", title: "Sales intelligence", steps: ["Conversation notes", "Spots job type and urgency", "Flags high-priority leads", "Sends you a short summary"] },
    { id: "growth", title: "Growth automation", steps: ["Job marked done", "Thank-you follow-up", "Review request", "Seasonal tune-up reminder"] },
  ],
  es: [
    { id: "reception", title: "Recepción de clientes", steps: ["El cliente llama o escribe", "El asistente contesta y saluda", "Anota la necesidad y dirección", "Ofrece una hora y avisa al equipo"] },
    { id: "sales", title: "Inteligencia de ventas", steps: ["Notas de la conversación", "Detecta tipo de trabajo y urgencia", "Marca clientes prioritarios", "Le envía un resumen corto"] },
    { id: "growth", title: "Automatización de crecimiento", steps: ["Trabajo terminado", "Mensaje de agradecimiento", "Solicitud de reseña", "Recordatorio de mantenimiento"] },
  ],
};

function nicheConfig(niche) {
  return NICHES[niche] || NICHES.hvac;
}

// Validates one raw input string. Returns { value, error } where error is a COPY key or null.
function parseInput(raw, { integer = false } = {}) {
  const s = String(raw ?? "").trim();
  if (s === "") return { value: null, error: "errRequired" };
  const n = Number(s);
  if (!Number.isFinite(n)) return { value: null, error: "errRequired" };
  if (n < 0) return { value: null, error: "errNegative" };
  if (n > MAX_INPUT) return { value: null, error: "errTooLarge" };
  if (integer && !Number.isInteger(n)) return { value: null, error: "errWhole" };
  return { value: n, error: null };
}

// The formula. Pure, browser-only.
function computeRoi({ missedCalls, jobValue }, { recoveryRate, bookingRate, minutesPerCall }) {
  const reached = missedCalls * recoveryRate;
  const recoveredRevenue = reached * bookingRate * jobValue;
  const hoursSaved = (reached * minutesPerCall) / 60;
  return { reached, recoveredRevenue, hoursSaved };
}

module.exports = { ROI_PAGES, NICHES, BASE_ASSUMPTIONS, COPY, DIAGRAMS, MAX_INPUT, nicheConfig, parseInput, computeRoi };
