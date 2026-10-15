// Spanish copy for plan names, descriptions and feature bullets. The plans API (backend
// DEFAULT_PLANS / admin dashboard) stores English text; this maps it by plan key and by the
// exact English feature string. Anything without a translation falls back to the API's text,
// so an admin edit never disappears. Prices are never translated here (see lib/pricing.js).
const ES = {
  names: {
    trial: "Prueba gratis / Sandbox",
    starter: "Inicial",
    growth: "Crecimiento",
    ai_office: "Oficina IA",
    high_volume: "Alto volumen",
    enterprise: "Empresarial",
  },
  descriptions: {
    trial: "Sandbox de dos meses para probar con familia, amigos o algunas llamadas reales.",
    starter: "Diga adiós al buzón de voz.",
    growth: "Capte cada cliente potencial y agende cada trabajo.",
    ai_office: "Su oficina digital completa.",
    high_volume: "Para negocios con mucho trabajo y varias ubicaciones.",
    enterprise: "Precio a medida. Aplican políticas de uso razonable.",
  },
  features: {
    "Basic AI receptionist": "Recepcionista IA básica",
    "Limited calls": "Llamadas limitadas",
    "No branded page": "Sin página con su marca",
    "No customer portal": "Sin portal de clientes",
    "Basic SMS": "SMS básicos",
    "Basic business configuration": "Configuración básica del negocio",
    "Basic branded presence": "Presencia básica con su marca",
    "Usage warnings": "Avisos de uso",
    "Advanced receptionist": "Recepcionista avanzada",
    "Appointment booking": "Agenda de citas",
    "Follow-ups": "Seguimientos",
    "Missed-call recovery": "Recuperación de llamadas perdidas",
    "More SMS": "Más SMS",
    "More automation": "Más automatización",
    "Full AI employee": "Empleado IA completo",
    "Scheduling": "Programación",
    "Customer management": "Gestión de clientes",
    "Lead recovery": "Recuperación de clientes potenciales",
    "Reviews": "Reseñas",
    "Business intelligence": "Inteligencia de negocio",
    "AI business advisor": "Asesor de negocio con IA",
    "Customer portal": "Portal de clientes",
    "Higher call volume": "Mayor volumen de llamadas",
    "Multiple locations": "Varias ubicaciones",
    "More phone numbers": "Más números de teléfono",
    "Advanced integrations": "Integraciones avanzadas",
    "Advanced automation": "Automatización avanzada",
    "Multiple AI personas": "Varias personalidades de IA",
    "Advanced reporting": "Informes avanzados",
    "Custom AI minutes": "Minutos de IA a medida",
    "Custom locations": "Ubicaciones a medida",
    "Custom users": "Usuarios a medida",
    "Custom phone numbers": "Números de teléfono a medida",
    "Custom integrations": "Integraciones a medida",
    "Multiple AI employees/personas": "Varios empleados/personalidades de IA",
    "Custom SMS volume": "Volumen de SMS a medida",
    "Priority support": "Soporte prioritario",
    "Custom workflows": "Flujos de trabajo a medida",
  },
};

export const PLAN_COPY = { es: ES };

/** Returns a copy of `plan` with name/description/features in `lang` (price untouched). */
export function localizePlan(plan, lang) {
  const copy = PLAN_COPY[(lang || "en").slice(0, 2)];
  if (!plan || !copy) return plan;
  return {
    ...plan,
    name: copy.names[plan.key] || plan.name,
    description: plan.description ? copy.descriptions[plan.key] || plan.description : plan.description,
    features: (plan.features || []).map((f) => copy.features[f] || f),
  };
}
