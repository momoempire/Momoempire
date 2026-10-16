import { ThemeProvider as NextThemesProvider } from "next-themes";

// Light/dark theme for the whole app. `class` strategy: <html class="dark"> (Tailwind darkMode:
// ["class"]). The same storage key is read by the inline script in public/index.html, which sets
// the class before first paint so there is no flash of the wrong theme.
export const THEME_STORAGE_KEY = "theme";

export default function ThemeProvider({ children }) {
  return (
    <NextThemesProvider attribute="class" defaultTheme="system" enableSystem storageKey={THEME_STORAGE_KEY} disableTransitionOnChange>
      {children}
    </NextThemesProvider>
  );
}
