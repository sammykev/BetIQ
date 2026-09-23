// Theme plumbing shared by the root layout (server) and ThemeToggle (client).

export const THEME_KEY = "betiq-theme";
export const THEME_COLOR = { light: "#f5f7fb", dark: "#070b14" } as const;

/**
 * Runs in <head> before first paint: an explicit choice wins, otherwise the
 * OS preference. Keeps the page from flashing the wrong theme on load.
 */
export const themeInitScript = `(function(){try{var t=localStorage.getItem("${THEME_KEY}");var d=t?t==="dark":window.matchMedia("(prefers-color-scheme: dark)").matches;document.documentElement.classList.toggle("dark",d);}catch(e){}})();`;

/** Browser only: the explicit choice if there is one, else the OS preference. */
export function preferredDark(): boolean {
  try {
    const t = localStorage.getItem(THEME_KEY);
    if (t) return t === "dark";
  } catch { /* storage unavailable — fall through to the OS setting */ }
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

/** Browser only: set the class and the browser-chrome colour. */
export function applyTheme(dark: boolean) {
  document.documentElement.classList.toggle("dark", dark);
  document.querySelectorAll('meta[name="theme-color"]').forEach(m =>
    m.setAttribute("content", dark ? THEME_COLOR.dark : THEME_COLOR.light)
  );
}
