"use client";

import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
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
    // Every colour changes at once: switch with transitions off for one frame (better-ui)
    const root = document.documentElement;
    root.classList.add("theme-switching");
    applyTheme(next);
    void root.offsetHeight;
    requestAnimationFrame(() => root.classList.remove("theme-switching"));
    setDark(next);
    try { localStorage.setItem(THEME_KEY, next ? "dark" : "light"); } catch { /* ignore */ }
  };

  return (
    <button
      onClick={toggle}
      aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
      title={dark ? "Light mode" : "Dark mode"}
      className={clsx(
        "relative inline-flex items-center justify-center w-9 h-9 rounded-xl bg-surface text-n-400 hover:text-n-0 active:scale-[0.96] [box-shadow:var(--ring-control)] hover:[box-shadow:var(--ring-control-hover)] transition-[color,box-shadow,scale] duration-150 ease-[cubic-bezier(0.2,0,0,1)]",
        className
      )}
    >
      {/* Render nothing theme-specific until mounted to avoid a hydration mismatch */}
      {dark === null ? <span className="w-[15px] h-[15px]" /> : (
        <AnimatePresence initial={false} mode="popLayout">
          <motion.span key={dark ? "sun" : "moon"} className="inline-flex"
            initial={{ opacity: 0, scale: 0.25, filter: "blur(4px)" }}
            animate={{ opacity: 1, scale: 1, filter: "blur(0px)" }}
            exit={{ opacity: 0, scale: 0.25, filter: "blur(4px)" }}
            transition={{ type: "spring", duration: 0.3, bounce: 0 }}>
            {dark ? <Sun size={15} /> : <Moon size={15} />}
          </motion.span>
        </AnimatePresence>
      )}
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
