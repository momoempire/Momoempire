// EMP-WL-064 / WL-071: user-facing text for a failed waitlist signup (landing form and estimator).
// - 503 (database unavailable): translated message (Spanish build shows Spanish), instead of the
//   backend's English sentence.
// - 422 (validation): a plain sentence instead of pydantic's raw
//   "value is not a valid email address: ..." text.
// Anything else (rate limit, Turnstile, network) keeps the existing errMessage() text.
// Strings live in their own bundle (like i18n/estimator.js) so the shared locale files that
// PR #2 edits are untouched. This file is identical on every branch that adds it.
import i18n from "@/i18n";

const en = {
  waitlistErrors: {
    unavailable: "Sorry, we couldn't save your signup right now. Please try again in a few minutes.",
    email: "Please enter a valid email address.",
    invalid: "Please check the form and try again.",
  },
};
const es = {
  waitlistErrors: {
    unavailable: "Lo sentimos, no pudimos guardar su registro en este momento. Inténtelo de nuevo en unos minutos.",
    email: "Ingrese un correo electrónico válido.",
    invalid: "Revise el formulario e inténtelo de nuevo.",
  },
};
i18n.addResourceBundle("en", "translation", en, true, false);
i18n.addResourceBundle("es", "translation", es, true, false);

export const WAITLIST_ERROR_STRINGS = { en, es };

export function waitlistErrorText(err, t, fallback) {
  const res = err && err.response;
  const status = res && res.status;
  if (status === 503) return t("waitlistErrors.unavailable");
  if (status === 422) {
    const detail = res.data && res.data.detail;
    const onEmail = Array.isArray(detail)
      && detail.some((d) => d && Array.isArray(d.loc) && d.loc.includes("email"));
    return t(onEmail ? "waitlistErrors.email" : "waitlistErrors.invalid");
  }
  return fallback(err);
}
