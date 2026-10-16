import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { Moon, Sun } from "lucide-react";

const LABELS = {
  en: { toDark: "Switch to dark theme", toLight: "Switch to light theme" },
  es: { toDark: "Cambiar a tema oscuro", toLight: "Cambiar a tema claro" },
};

// Accessible light/dark toggle: a real <button> (Enter/Space work natively), an aria-label that
// names the action, aria-pressed for the dark state, and a visible focus ring. Shows the icon of
// the theme you would switch to.
export default function ThemeToggle({ lang = "en", className = "" }) {
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const isDark = mounted ? resolvedTheme === "dark" : document.documentElement.classList.contains("dark");
  const l = LABELS[lang] || LABELS.en;
  const label = isDark ? l.toLight : l.toDark;
  return (
    <button
      type="button"
      onClick={() => setTheme(isDark ? "light" : "dark")}
      aria-label={label}
      aria-pressed={isDark}
      title={label}
      data-testid="theme-toggle"
      className={`inline-flex h-10 w-10 items-center justify-center rounded-md border border-current/20 hover:bg-black/5 dark:hover:bg-white/10 focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-blue-600 ${className}`}
    >
      {isDark ? <Sun className="h-5 w-5" aria-hidden /> : <Moon className="h-5 w-5" aria-hidden />}
    </button>
  );
}
