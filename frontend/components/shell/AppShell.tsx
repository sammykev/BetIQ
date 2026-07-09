"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Home, TrendingUp, CalendarDays, LayoutDashboard, ShieldCheck } from "lucide-react";
import { UserMenu } from "@/components/UserMenu";
import clsx from "clsx";
import type { ReactNode } from "react";

const NAV = [
  { href: "/", label: "Predictions", icon: Home },
  { href: "/value-bets", label: "Value Bets", icon: TrendingUp },
  { href: "/history", label: "History", icon: CalendarDays },
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
];

interface Props {
  children: ReactNode;
  /** Extra controls rendered on the right side of the top bar (before the user menu). */
  actions?: ReactNode;
  /** Rendered above the header, e.g. announcement banner. */
  banner?: ReactNode;
  onUpgrade?: () => void;
}

function Logo() {
  return (
    <Link href="/" className="flex items-center gap-2.5 min-w-0">
      <img src="/logo.svg" alt="BetIQ" className="w-8 h-8 rounded-lg shrink-0" />
      <div className="min-w-0">
        <p className="text-[15px] font-bold tracking-tight text-zinc-900 dark:text-white leading-none">
          BetIQ
        </p>
        <p className="text-[10px] text-zinc-400 dark:text-zinc-500 leading-none mt-1 truncate">
          AI Football Predictions
        </p>
      </div>
    </Link>
  );
}

export function AppShell({ children, actions, banner, onUpgrade }: Props) {
  const pathname = usePathname();

  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <div className="min-h-screen">
      {banner}

      {/* ── Desktop sidebar ── */}
      <aside className="hidden lg:flex fixed inset-y-0 left-0 w-60 flex-col border-r border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 z-30">
        <div className="px-5 py-5 border-b border-zinc-100 dark:border-zinc-900">
          <Logo />
        </div>

        <nav className="flex-1 px-3 py-4 space-y-1">
          {NAV.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className={clsx(
                "flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-all",
                isActive(href)
                  ? "bg-brand-50 dark:bg-brand-900/20 text-brand-700 dark:text-brand-400"
                  : "text-zinc-500 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 hover:bg-zinc-50 dark:hover:bg-zinc-900"
              )}
            >
              <Icon size={17} strokeWidth={isActive(href) ? 2.4 : 2} />
              {label}
            </Link>
          ))}
        </nav>

        <div className="px-5 py-4 border-t border-zinc-100 dark:border-zinc-900">
          <div className="flex items-start gap-2 text-zinc-400 dark:text-zinc-500">
            <ShieldCheck size={14} className="mt-0.5 shrink-0" />
            <p className="text-[11px] leading-relaxed">
              Gamble responsibly. For educational use only.
            </p>
          </div>
        </div>
      </aside>

      {/* ── Top bar ── */}
      <header className="sticky top-0 z-20 lg:pl-60 border-b border-zinc-200/80 dark:border-zinc-800 bg-white/85 dark:bg-zinc-950/85 backdrop-blur-md">
        <div className="flex items-center justify-between gap-3 px-4 sm:px-6 h-14">
          <div className="lg:hidden">
            <Logo />
          </div>
          <div className="hidden lg:block" />
          <div className="flex items-center gap-2">
            {actions}
            <UserMenu onUpgrade={onUpgrade ?? (() => {})} />
          </div>
        </div>
      </header>

      {/* ── Main content ── */}
      <main className="lg:pl-60 pb-20 lg:pb-8">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 py-6">{children}</div>
      </main>

      {/* ── Mobile bottom nav ── */}
      <nav className="lg:hidden fixed bottom-0 inset-x-0 z-30 border-t border-zinc-200 dark:border-zinc-800 bg-white/95 dark:bg-zinc-950/95 backdrop-blur-md pb-[env(safe-area-inset-bottom)]">
        <div className="grid grid-cols-4">
          {NAV.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className={clsx(
                "flex flex-col items-center gap-1 py-2.5 text-[10px] font-medium transition-colors",
                isActive(href)
                  ? "text-brand-600 dark:text-brand-400"
                  : "text-zinc-400 dark:text-zinc-500 hover:text-zinc-600 dark:hover:text-zinc-300"
              )}
            >
              <Icon size={19} strokeWidth={isActive(href) ? 2.4 : 2} />
              {label}
            </Link>
          ))}
        </div>
      </nav>
    </div>
  );
}
