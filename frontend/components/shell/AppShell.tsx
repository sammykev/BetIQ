"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { Home, Flame, Target, LayoutDashboard, Sparkles } from "lucide-react";
import { UserMenu } from "@/components/UserMenu";
import { ThemeToggle } from "@/components/ThemeToggle";
import { SlipButton, SlipDrawer } from "@/components/BetSlip";
import { PaywallModal } from "@/components/PaywallModal";
import { Trial } from "@/components/Trial";
import { useAccess, type FeatureId } from "@/lib/access";
import clsx from "clsx";
import { motion } from "motion/react";
import type { ReactNode } from "react";

// Each link shows while any of its features is switched on for this visitor
// (admin → Access); a locked one still shows, and its page offers the upgrade
const NAV: { href: string; label: string; icon: typeof Home; features?: FeatureId[] }[] = [
  { href: "/", label: "Predictions", icon: Home },
  { href: "/daily", label: "Daily odds", icon: Flame, features: ["daily_slips"] },
  { href: "/optimizer", label: "Optimizer", icon: Sparkles, features: ["optimizer", "code_check"] },
  { href: "/history", label: "Record", icon: Target, features: ["record"] },
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard, features: ["dashboard"] },
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
      <img src="/logo.svg" alt="" className="w-8 h-8 rounded-lg shrink-0 img-outline" />
      <Wordmark className="text-[26px]" />
    </Link>
  );
}

export function AppShell({ children, actions, banner, onUpgrade }: Props) {
  const pathname = usePathname();
  const { shown } = useAccess();
  const nav = NAV.filter(n => !n.features || n.features.some(shown));
  // Pages that don't handle the upgrade themselves get the plans here
  const [plans, setPlans] = useState(false);

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

        <nav aria-label="Main" className="flex-1 px-3 py-4 space-y-0.5">
          {nav.map(({ href, label, icon: Icon }) => {
            const active = isActive(href);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={clsx(
                  "relative flex items-center gap-3 px-3 h-10 rounded-xl text-sm font-medium",
                  "transition-[color,background-color,scale] duration-150 ease-[cubic-bezier(0.2,0,0,1)] active:scale-[0.98]",
                  active ? "text-n-0" : "text-n-400 hover:text-n-0 hover:bg-n-800/40"
                )}
              >
                {/* The chosen item's surface slides between items */}
                {active && (
                  <motion.span layoutId="side-nav-pill" aria-hidden
                    className="absolute inset-0 rounded-xl bg-surface [box-shadow:var(--shadow-card)]"
                    transition={{ type: "spring", duration: 0.35, bounce: 0 }} />
                )}
                <Icon size={17} strokeWidth={active ? 2.3 : 2} className={clsx("relative", active && "text-accent")} />
                <span className="relative">{label}</span>
              </Link>
            );
          })}
        </nav>

        <div className="px-5 py-4 border-t border-n-900 flex items-start gap-2.5">
          <span className="shrink-0 font-display font-bold text-[13px] leading-none text-n-300 rounded-md px-1.5 py-1 [box-shadow:var(--ring-control)]">18+</span>
          <p className="text-[11px] leading-relaxed text-n-500 text-pretty">
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
            {shown("bet_slip") && <SlipButton />}
            <ThemeToggle />
            <UserMenu onUpgrade={onUpgrade ?? (() => setPlans(true))} />
          </div>
        </div>
      </header>

      {/* ── Main content ── */}
      <main className="lg:pl-60 pb-24 lg:pb-10">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 py-6 lg:py-8">
          <Trial onPlans={onUpgrade ?? (() => setPlans(true))} />
          {children}
        </div>
      </main>

      {shown("bet_slip") && <SlipDrawer />}
      {plans && <PaywallModal onClose={() => setPlans(false)} onSuccess={() => setPlans(false)} />}

      {/* ── Mobile bottom nav ── */}
      <nav aria-label="Main" className="lg:hidden fixed bottom-0 inset-x-0 z-30 border-t border-n-900 bg-canvas/90 backdrop-blur-md pb-[env(safe-area-inset-bottom)]">
        {/* One column per link, so all of them sit on one row */}
        <div className="grid px-1" style={{ gridTemplateColumns: `repeat(${nav.length}, minmax(0, 1fr))` }}>
          {nav.map(({ href, label, icon: Icon }) => {
            const active = isActive(href);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={clsx(
                  "relative flex flex-col items-center gap-1 pt-2.5 pb-2 px-0.5 min-w-0 min-h-[56px] text-[10px] font-medium",
                  "transition-[color,scale] duration-150 ease-[cubic-bezier(0.2,0,0,1)] active:scale-[0.96]",
                  active ? "text-n-0" : "text-n-500 hover:text-n-300"
                )}
              >
                <span className="relative flex items-center justify-center h-7 w-12">
                  {active && (
                    <motion.span layoutId="bottom-nav-pill" aria-hidden
                      className="absolute inset-0 rounded-full bg-accent/15"
                      transition={{ type: "spring", duration: 0.35, bounce: 0 }} />
                  )}
                  <Icon size={19} strokeWidth={active ? 2.3 : 2} className={clsx("relative", active && "text-accent")} />
                </span>
                <span className={clsx("max-w-full truncate", active && "font-semibold")}>{label}</span>
              </Link>
            );
          })}
        </div>
      </nav>
    </div>
  );
}
