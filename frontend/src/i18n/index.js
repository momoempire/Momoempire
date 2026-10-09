import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import LanguageDetector from "i18next-browser-languagedetector";

// EMP-WL-093: the waitlist-only build bundles only the waitlist strings, not the full
// translation files (trial, pricing, login, billing, dashboard...). The condition is a build-time
// constant, so webpack drops the other branch. waitlist.*.json = every "landing" key with
// "waitlist" in its name (kept in sync by src/waitlist/WaitlistI18n.test.js).
const WAITLIST_ONLY = process.env.REACT_APP_WAITLIST_ONLY === "true";
const en = WAITLIST_ONLY ? require("./locales/waitlist.en.json") : require("./locales/en.json");
const es = WAITLIST_ONLY ? require("./locales/waitlist.es.json") : require("./locales/es.json");

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      en: { translation: en },
      es: { translation: es },
    },
    fallbackLng: "en",
    supportedLngs: ["en", "es"],
    interpolation: { escapeValue: false },
    detection: {
      order: ["localStorage", "navigator"],
      caches: ["localStorage"],
      lookupLocalStorage: "aiop_lang",
    },
  });

export default i18n;
