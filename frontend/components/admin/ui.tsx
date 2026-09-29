"use client";

import { createContext, useContext, type ReactNode } from "react";
import clsx from "clsx";
import { Loader2 } from "lucide-react";

// Shared pieces for the admin panel (app/betiq-hq). Styling uses the site's
// theme tokens, so the panel follows the light/dark toggle like every page.

export const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

export type AdminFetch = (url: string, init?: RequestInit) => Promise<Response>;

export interface AdminApi {
  adminFetch: AdminFetch;
  /** GET JSON from the backend (path starting /api/…), null on failure */
  get: <T = any>(path: string) => Promise<T | null>;
  /** POST JSON to the backend; throws with the server's message on failure */
  post: <T = any>(path: string, body?: object) => Promise<T>;
  flash: (type: "ok" | "err", text: string) => void;
}

export const AdminContext = createContext<AdminApi | null>(null);

export function useAdmin(): AdminApi {
  const ctx = useContext(AdminContext);
  if (!ctx) throw new Error("useAdmin outside the admin panel");
  return ctx;
}

/* ── formatting ── */
export const num = (n: number | null | undefined) => (n ?? 0).toLocaleString("en-US");
export const naira = (n: number | null | undefined) =>
  `₦${(n ?? 0).toLocaleString("en-NG", { maximumFractionDigits: 0 })}`;
export function ago(when: string | number | null | undefined): string {
  if (when === null || when === undefined || when === "") return "never";
  const t = typeof when === "number" ? (when < 1e12 ? when * 1000 : when) : new Date(when).getTime();
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400 * 2) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}
const regionNames = typeof Intl !== "undefined" && "DisplayNames" in Intl
  ? new Intl.DisplayNames(["en"], { type: "region" }) : null;
export function countryName(code: string): string {
  if (!code || code === "??") return "Unknown";
  try { return regionNames?.of(code) ?? code; } catch { return code; }
}
export function flag(code: string): string {
  if (!/^[A-Z]{2}$/.test(code || "")) return "🌐";
  return String.fromCodePoint(0x1f1a5 + code.charCodeAt(0), 0x1f1a5 + code.charCodeAt(1));
}

/* ── layout ── */
export function Card({ title, icon, action, children, className, subtitle }: {
  title?: ReactNode; icon?: ReactNode; action?: ReactNode; subtitle?: ReactNode;
  children: ReactNode; className?: string;
}) {
  return (
    <section className={clsx("card p-4 sm:p-5 space-y-4 min-w-0", className)}>
      {(title || action) && (
        <header className="flex items-start gap-2">
          {icon && <span className="text-accent mt-0.5 shrink-0">{icon}</span>}
          <div className="min-w-0 flex-1">
            {title && <h2 className="font-semibold text-sm text-n-0">{title}</h2>}
            {subtitle && <p className="text-xs text-n-400 mt-0.5">{subtitle}</p>}
          </div>
          {action && <div className="shrink-0 flex items-center gap-2">{action}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, sub, tone }: {
  label: string; value: ReactNode; sub?: ReactNode; tone?: "accent" | "warn" | "danger" | "info";
}) {
  return (
    <div className="rounded-xl bg-surface-sunken px-3 py-2.5 min-w-0">
      <p className="eyebrow truncate">{label}</p>
      <p className={clsx("font-display font-extrabold text-2xl tnum mt-0.5 truncate",
        tone === "accent" ? "text-accent" : tone === "warn" ? "text-warn" : tone === "danger" ? "text-danger"
          : tone === "info" ? "text-info" : "text-n-0")}>{value}</p>
      {sub && <p className="text-[11px] text-n-400 truncate">{sub}</p>}
    </div>
  );
}

export function Btn({ onClick, busy, disabled, children, variant = "ghost", type = "button", title }: {
  onClick?: () => void; busy?: boolean; disabled?: boolean; children: ReactNode;
  variant?: "primary" | "ghost" | "danger"; type?: "button" | "submit"; title?: string;
}) {
  return (
    <button type={type} onClick={onClick} disabled={busy || disabled} title={title}
      className={clsx("inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors disabled:opacity-50 whitespace-nowrap",
        variant === "primary" && "bg-brand-400 hover:bg-brand-300 text-ink",
        variant === "ghost" && "border border-n-800 text-n-300 hover:border-n-700 hover:text-n-0 bg-surface",
        variant === "danger" && "border border-danger/40 text-danger hover:bg-danger/10")}>
      {busy && <Loader2 size={12} className="animate-spin" />}
      {children}
    </button>
  );
}

export function Toggle({ on, onChange, busy, label, hint }: {
  on: boolean | null; onChange: () => void; busy?: boolean; label: string; hint?: string;
}) {
  return (
    <button type="button" role="switch" aria-checked={!!on} onClick={onChange} disabled={busy || on === null}
      className="flex items-center gap-3 rounded-xl bg-surface-sunken px-3 py-2.5 text-left w-full disabled:opacity-60">
      <span className={clsx("relative h-5 w-9 rounded-full transition-colors shrink-0", on ? "bg-brand-400" : "bg-n-700")}>
        <span className={clsx("absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-[left] duration-150 ease-[cubic-bezier(0.2,0,0,1)]", on ? "left-[18px]" : "left-0.5")} />
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-semibold text-n-0">{label}</span>
        {hint && <span className="block text-[11px] text-n-400 truncate">{hint}</span>}
      </span>
      {busy && <Loader2 size={14} className="animate-spin text-n-400 ml-auto" />}
    </button>
  );
}

/** Ranked horizontal bars: label, bar to scale, value. */
export function BarList({ items, format = num, empty = "Nothing yet", max }: {
  items: { key: string; label: ReactNode; value: number; sub?: ReactNode }[];
  format?: (n: number) => string; empty?: string; max?: number;
}) {
  if (!items.length) return <p className="text-xs text-n-500">{empty}</p>;
  const top = max ?? Math.max(...items.map(i => i.value), 1);
  return (
    <ul className="space-y-1.5">
      {items.map(i => (
        <li key={i.key} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 text-xs"
          title={`${typeof i.label === "string" ? i.label : i.key}: ${format(i.value)}`}>
          <div className="min-w-0">
            <div className="flex items-baseline gap-1.5 min-w-0">
              <span className="truncate text-n-200">{i.label}</span>
              {i.sub && <span className="text-n-500 text-[11px] shrink-0">{i.sub}</span>}
            </div>
            <div className="mt-1 h-1.5 rounded-full bg-n-800 overflow-hidden">
              <div className="h-full rounded-full" style={{ width: `${Math.max(2, (i.value / top) * 100)}%`, background: "rgb(var(--chart-1))" }} />
            </div>
          </div>
          <span className="tnum font-semibold text-n-0">{format(i.value)}</span>
        </li>
      ))}
    </ul>
  );
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: rows }, (_, i) => <div key={i} className="skeleton h-3" style={{ width: `${90 - i * 12}%` }} />)}
    </div>
  );
}

export function Pill({ tone = "muted", children }: { tone?: "ok" | "warn" | "danger" | "muted" | "info"; children: ReactNode }) {
  return (
    <span className={clsx("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold border",
      tone === "ok" && "text-accent border-accent/30 bg-accent/10",
      tone === "warn" && "text-warn border-warn/30 bg-warn/10",
      tone === "danger" && "text-danger border-danger/30 bg-danger/10",
      tone === "info" && "text-info border-info/30 bg-info/10",
      tone === "muted" && "text-n-400 border-n-800 bg-surface-sunken")}>
      {children}
    </span>
  );
}

export const inputClass =
  "w-full rounded-lg bg-surface-sunken border border-n-800 px-3 py-2 text-sm text-n-0 placeholder:text-n-500 outline-none focus:border-accent";
