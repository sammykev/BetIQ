"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Home, TrendingUp, CalendarDays, LayoutDashboard, Sparkles } from "lucide-react";
import { UserMenu } from "@/components/UserMenu";
import { ThemeToggle } from "@/components/ThemeToggle";
import { SlipButton, SlipDrawer } from "@/components/BetSlip";
import clsx from "clsx";
import type { ReactNode } from "react";

const NAV = [
  { href: "/", label: "Predictions", icon: Home },
  { href: "/optimizer", label: "Optimizer", icon: Sparkles },
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

/** BET·IQ wordmark — condensed caps with the "IQ" in the accent. */
export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={clsx("font-display font-extrabold uppercase tracking-[0.02em] leading-none text-n-0", className)}>
      Bet<span className="text-accent">IQ</span>
    </span>
  );
}

function Logo() {
  return (
    <Link href="/" className="flex items-center gap-2.5 min-w-0" aria-label="BetIQ home">
      <img src="/logo.svg" alt="" className="w-8 h-8 rounded-lg shrink-0 ring-1 ring-n-800" />
      <Wordmark className="text-[26px]" />
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
      <aside className="hidden lg:flex fixed inset-y-0 left-0 w-60 flex-col border-r border-n-900 bg-canvas/70 backdrop-blur-md z-30">
        <div className="px-5 h-16 flex items-center">
          <Logo />
        </div>

        <nav className="flex-1 px-3 py-4 space-y-1">
          <p className="eyebrow px-3 pb-2">Menu</p>
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = isActive(href);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={clsx(
                  "relative flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-semibold transition-colors",
                  active
                    ? "bg-surface text-n-0"
                    : "text-n-400 hover:text-n-0 hover:bg-surface/60"
                )}
              >
                {active && <span className="absolute left-0 top-2 bottom-2 w-[3px] rounded-r-full bg-accent" />}
                <Icon size={17} strokeWidth={active ? 2.4 : 2} className={active ? "text-accent" : undefined} />
                {label}
              </Link>
            );
          })}
        </nav>

        <div className="px-5 py-4 border-t border-n-900 flex items-start gap-2.5">
          <span className="shrink-0 font-display font-bold text-[13px] leading-none text-n-300 border border-n-700 rounded-md px-1.5 py-1">18+</span>
          <p className="text-[11px] leading-relaxed text-n-500">
            Predictions are probabilities, not guarantees. Gamble responsibly.
          </p>
        </div>
      </aside>

      {/* ── Top bar ── */}
      <header className="sticky top-0 z-20 lg:pl-60 border-b border-n-900 bg-canvas/80 backdrop-blur-md">
        <div className="flex items-center justify-between gap-3 px-4 sm:px-6 h-14 lg:h-16">
          <div className="lg:hidden">
            <Logo />
          </div>
          <div className="hidden lg:block" />
          <div className="flex items-center gap-2">
            {actions}
            <SlipButton />
            <ThemeToggle />
            <UserMenu onUpgrade={onUpgrade ?? (() => {})} />
          </div>
        </div>
      </header>

      {/* ── Main content ── */}
      <main className="lg:pl-60 pb-24 lg:pb-10">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 py-6 lg:py-8">{children}</div>
      </main>

      <SlipDrawer />

      {/* ── Mobile bottom nav ── */}
      <nav className="lg:hidden fixed bottom-0 inset-x-0 z-30 border-t border-n-900 bg-canvas/95 backdrop-blur-md pb-[env(safe-area-inset-bottom)]">
        <div className="grid grid-cols-4">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = isActive(href);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={clsx(
                  "relative flex flex-col items-center gap-1 pt-3 pb-2.5 text-[10px] font-semibold transition-colors",
                  active ? "text-n-0" : "text-n-500 hover:text-n-300"
                )}
              >
                {active && <span className="absolute top-0 inset-x-6 h-[2px] rounded-b-full bg-accent" />}
                <Icon size={19} strokeWidth={active ? 2.4 : 2} className={active ? "text-accent" : undefined} />
                {label}
              </Link>
            );
          })}
        </div>
      </nav>
    </div>
  );
}
