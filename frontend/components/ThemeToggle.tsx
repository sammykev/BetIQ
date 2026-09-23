"use client";

import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";
import clsx from "clsx";
import { THEME_KEY, applyTheme, preferredDark } from "@/lib/theme";

/** Sun/moon button that flips light ↔ dark and remembers the choice. */
export function ThemeToggle({ className }: { className?: string }) {
  const [dark, setDark] = useState<boolean | null>(null);

  useEffect(() => {
    setDark(preferredDark());
    // Until the user picks a theme, keep following the OS setting
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (e: MediaQueryListEvent) => {
      let stored: string | null = null;
      try { stored = localStorage.getItem(THEME_KEY); } catch { /* ignore */ }
      if (stored) return;
      applyTheme(e.matches);
      setDark(e.matches);
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const toggle = () => {
    const next = !document.documentElement.classList.contains("dark");
    applyTheme(next);
    setDark(next);
    try { localStorage.setItem(THEME_KEY, next ? "dark" : "light"); } catch { /* ignore */ }
  };

  return (
    <button
      onClick={toggle}
      aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
      title={dark ? "Light mode" : "Dark mode"}
      className={clsx(
        "inline-flex items-center justify-center w-8 h-8 rounded-lg border border-n-800 bg-surface text-n-400 hover:text-n-0 hover:border-n-700 transition-colors",
        className
      )}
    >
      {/* Render nothing theme-specific until mounted to avoid a hydration mismatch */}
      {dark === null ? <span className="w-[15px] h-[15px]" /> : dark ? <Sun size={15} /> : <Moon size={15} />}
    </button>
  );
}

/**
 * Re-applies the theme after hydration. The <head> script sets it before
 * paint, but if React ever re-renders <html> from scratch (e.g. recovering
 * from a hydration error) it restores the server's default class.
 */
export function ThemeSync() {
  useEffect(() => { applyTheme(preferredDark()); }, []);
  return null;
}
