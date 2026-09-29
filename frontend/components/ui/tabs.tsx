"use client";

import * as React from "react";
import { motion } from "motion/react";
import { cn } from "@/lib/utils";

export interface TabItem<T extends string> {
  value: T;
  label: React.ReactNode;
  /** A count or badge after the label */
  extra?: React.ReactNode;
}

// Segmented tabs with a sliding pill (better-ui: the indicator moves, the
// labels don't). Arrow keys move between tabs, as a tablist should.
export function Tabs<T extends string>({
  items, value, onChange, label, className, size = "md", id,
}: {
  items: TabItem<T>[];
  value: T;
  onChange: (v: T) => void;
  /** Accessible name for the tab list */
  label: string;
  className?: string;
  size?: "sm" | "md";
  /** Unique per page when several tab rows are on screen (keeps pills apart) */
  id?: string;
}) {
  const auto = React.useId();
  const group = id ?? auto;
  const refs = React.useRef<(HTMLButtonElement | null)[]>([]);
  const onKey = (e: React.KeyboardEvent, i: number) => {
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    const to = e.key === "Home" ? 0 : e.key === "End" ? items.length - 1 : step ? (i + step + items.length) % items.length : -1;
    if (to < 0) return;
    e.preventDefault();
    onChange(items[to].value);
    refs.current[to]?.focus();
  };
  return (
    <div role="tablist" aria-label={label}
      className={cn("inline-flex items-center gap-1 p-1 rounded-xl bg-n-800/50 max-w-full overflow-x-auto no-scrollbar", className)}>
      {items.map((t, i) => {
        const active = t.value === value;
        return (
          <button key={t.value} ref={el => { refs.current[i] = el; }} type="button" role="tab" aria-selected={active}
            tabIndex={active ? 0 : -1} onClick={() => onChange(t.value)} onKeyDown={e => onKey(e, i)}
            className={cn(
              "relative inline-flex items-center gap-1.5 whitespace-nowrap font-semibold rounded-lg select-none",
              "transition-[color,scale] duration-150 ease-[cubic-bezier(0.2,0,0,1)] active:scale-[0.96]",
              size === "sm" ? "h-8 px-3 text-[13px]" : "h-9 px-3.5 text-sm",
              active ? "text-n-0" : "text-n-400 hover:text-n-100",
            )}>
            {active && (
              <motion.span layoutId={`tab-pill-${group}`} aria-hidden
                className="absolute inset-0 rounded-lg bg-surface [box-shadow:var(--shadow-card)]"
                transition={{ type: "spring", duration: 0.3, bounce: 0 }} />
            )}
            <span className="relative">{t.label}</span>
            {t.extra != null && <span className="relative text-[11px] text-n-500 tnum">{t.extra}</span>}
          </button>
        );
      })}
    </div>
  );
}
