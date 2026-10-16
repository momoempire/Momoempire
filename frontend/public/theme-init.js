/* No flash of the wrong theme: set <html class="dark"> before first paint, using the same
   rule as next-themes (src/components/ThemeProvider.jsx): saved "theme" choice, else the
   OS setting. External file (not inline) so the Content-Security-Policy can stay
   strict: script-src 'self' covers it. */
(function () {
    try {
        var t = localStorage.getItem("theme");
        var dark = t === "dark" || ((!t || t === "system") && window.matchMedia("(prefers-color-scheme: dark)").matches);
        var c = document.documentElement.classList;
        c.add(dark ? "dark" : "light");
        document.documentElement.style.colorScheme = dark ? "dark" : "light";
    } catch (e) {}
})();
