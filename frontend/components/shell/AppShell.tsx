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
import { AnimatePresence, motion } from "motion/react";
import { useScrollingDown } from "@/lib/useScrollDirection";
import { EASE } from "@/lib/utils";
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

type NavItem = (typeof NAV)[number];
const SPRING = { type: "spring", duration: 0.4, bounce: 0.15 } as const;

/*
 * One shell, four arrangements (tailwind.config.js screens):
 *   phone portrait      a floating glass menu bar at the bottom
 *   phone on its side   an icon rail (`short:`), the header slides away on scroll
 *   tablet (md)         an icon rail
 *   laptop up (lg)      the 240px sidebar
 */
export function AppShell({ children, actions, banner, onUpgrade }: Props) {
  const pathname = usePathname();
  const { shown } = useAccess();
  const nav = NAV.filter(n => !n.features || n.features.some(shown));
  // Pages that don't handle the upgrade themselves get the plans here
  const [plans, setPlans] = useState(false);
  const down = useScrollingDown();

  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <div className="min-h-screen">
      {banner}

      {/* ── Laptop sidebar ── */}
      <aside className="hidden lg:flex fixed inset-y-0 left-0 w-60 flex-col border-r border-n-900/70 glass-thin z-30">
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
                  "group relative flex items-center gap-3 px-3 h-11 rounded-xl text-sm font-medium",
                  "transition-[color,background-color,scale] duration-150 ease-[cubic-bezier(0.2,0,0,1)] active:scale-[0.98]",
                  active ? "text-n-0" : "text-n-400 hover:text-n-0 hover:bg-n-800/40"
                )}
              >
                {/* The chosen item's surface slides between items */}
                {active && (
                  <motion.span layoutId="side-nav-pill" aria-hidden
                    className="absolute inset-0 rounded-xl bg-surface [box-shadow:var(--shadow-card)]"
                    transition={SPRING} />
                )}
                <Icon size={17} strokeWidth={active ? 2.3 : 2}
                  className={clsx("relative transition-transform duration-200 group-hover:scale-110", active && "text-accent")} />
                <span className="relative">{label}</span>
              </Link>
            );
          })}
        </nav>

        <div className="px-5 py-4 border-t border-n-900/70 flex items-start gap-2.5">
          <span className="shrink-0 font-display font-bold text-[13px] leading-none text-n-300 rounded-md px-1.5 py-1 [box-shadow:var(--ring-control)]">18+</span>
          <p className="text-[11px] leading-relaxed text-n-500 text-pretty">
            Predictions are probabilities, not guarantees. Gamble responsibly.
          </p>
        </div>
      </aside>

      {/* ── Tablet and phone-on-its-side rail ── */}
      <aside className="hidden md:flex short:flex lg:hidden fixed inset-y-0 left-0 z-30 w-rail pl-[env(safe-area-inset-left)] pt-safe flex-col items-center border-r border-n-900/70 glass-thin">
        <Link href="/" aria-label="BetIQ home" className="mt-3 short:mt-2 mb-2 flex items-center justify-center h-11 w-11 rounded-xl active:scale-95 transition-transform">
          <img src="/logo.svg" alt="" className="w-8 h-8 rounded-lg img-outline" />
        </Link>
        <nav aria-label="Main" className="flex-1 flex flex-col items-center gap-1 short:gap-0.5 py-2 overflow-y-auto no-scrollbar">
          {nav.map(item => <RailLink key={item.href} item={item} active={isActive(item.href)} />)}
        </nav>
        <span className="mb-4 short:mb-2 font-display font-bold text-[12px] leading-none text-n-400 rounded-md px-1.5 py-1 [box-shadow:var(--ring-control)]"
          title="Predictions are probabilities, not guarantees. Gamble responsibly.">18+</span>
      </aside>

      {/* ── Top bar ── */}
      <header className={clsx(
        "shell-header sticky top-0 z-20 pt-safe md:pl-rail short:pl-rail lg:pl-60 border-b border-n-900/60 glass",
        "transition-transform duration-300 ease-[cubic-bezier(0.2,0,0,1)]",
        // No room to spare on a phone held sideways: the bar slides away while reading
        down && "short:-translate-y-full"
      )}>
        <div className="flex items-center justify-between gap-3 px-4 sm:px-6 h-14 short:h-12 lg:h-16 pr-[max(1rem,env(safe-area-inset-right))]">
          <div className="lg:hidden">
            <Logo />
          </div>
          <div className="hidden lg:block" />
          <div className="flex items-center gap-1.5 sm:gap-2">
            {actions}
            {shown("bet_slip") && <SlipButton />}
            <ThemeToggle />
            <UserMenu onUpgrade={onUpgrade ?? (() => setPlans(true))} />
          </div>
        </div>
      </header>

      {/* ── Main content ── */}
      <main className="pb-dock md:pb-10 short:pb-8 md:pl-rail short:pl-rail lg:pl-60 pr-[env(safe-area-inset-right)]">
        {/* key: each page fades up as it arrives (CSS, so nothing fixed inside is moved after) */}
        <div key={pathname} className="max-w-6xl 3xl:max-w-7xl mx-auto px-4 sm:px-6 py-6 short:py-4 lg:py-8 animate-page-in">
          <Trial onPlans={onUpgrade ?? (() => setPlans(true))} />
          {children}
        </div>
      </main>

      {shown("bet_slip") && <SlipDrawer />}
      {plans && <PaywallModal onClose={() => setPlans(false)} onSuccess={() => setPlans(false)} />}

      {/* ── Phone menu bar: a floating glass pill that tucks its labels away while scrolling down ── */}
      <div className="md:hidden short:hidden fixed inset-x-0 z-30 flex justify-center px-3 pointer-events-none"
        style={{ bottom: "calc(10px + env(safe-area-inset-bottom))" }}>
        <motion.nav
          aria-label="Main"
          layout
          initial={{ y: 32, opacity: 0, scale: 0.96 }}
          animate={{ y: 0, opacity: 1, scale: 1 }}
          transition={{ ...SPRING, layout: SPRING }}
          style={{ borderRadius: 28 }}
          className="pointer-events-auto glass-thick glass-edge flex items-stretch gap-0.5 p-1.5 max-w-full"
        >
          {nav.map(item => <DockLink key={item.href} item={item} active={isActive(item.href)} compact={down} />)}
        </motion.nav>
      </div>
    </div>
  );
}

function DockLink({ item: { href, label, icon: Icon }, active, compact }: { item: NavItem; active: boolean; compact: boolean }) {
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={clsx(
        "relative flex flex-col items-center justify-center min-w-0 rounded-[22px] font-medium",
        "transition-[color,scale] duration-150 ease-[cubic-bezier(0.2,0,0,1)] active:scale-[0.94]",
        compact ? "min-h-[44px] w-12" : "min-h-[52px] w-[clamp(60px,17.5vw,70px)]",
        active ? "text-n-0" : "text-n-400 hover:text-n-200"
      )}
    >
      {active && (
        <motion.span layoutId="dock-pill" aria-hidden className="absolute inset-0 rounded-[22px] glass-lens" transition={SPRING} />
      )}
      <motion.span layout="position" transition={SPRING} className="relative flex">
        <Icon size={20} strokeWidth={active ? 2.3 : 2} className={clsx(active && "text-accent")} />
      </motion.span>
      {/* Hidden from sight, never from screen readers */}
      <AnimatePresence initial={false}>
        {compact ? (
          <span className="sr-only">{label}</span>
        ) : (
          <motion.span key="label"
            initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.2, ease: EASE }}
            className={clsx("relative max-w-full truncate text-[10.5px] leading-tight mt-1 px-0.5", active && "font-semibold")}>
            {label}
          </motion.span>
        )}
      </AnimatePresence>
    </Link>
  );
}

function RailLink({ item: { href, label, icon: Icon }, active }: { item: NavItem; active: boolean }) {
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      title={label}
      className={clsx(
        "group relative flex flex-col items-center justify-center gap-1 w-[60px] min-h-[56px] short:min-h-[48px] rounded-2xl font-medium",
        "transition-[color,scale] duration-150 ease-[cubic-bezier(0.2,0,0,1)] active:scale-[0.94]",
        active ? "text-n-0" : "text-n-400 hover:text-n-0"
      )}
    >
      {active && (
        <motion.span layoutId="rail-pill" aria-hidden className="absolute inset-0 rounded-2xl glass-lens" transition={SPRING} />
      )}
      <Icon size={19} strokeWidth={active ? 2.3 : 2}
        className={clsx("relative transition-transform duration-200 group-hover:scale-110", active && "text-accent")} />
      <span className={clsx("relative max-w-full truncate text-[10px] leading-tight short:sr-only", active && "font-semibold")}>{label}</span>
    </Link>
  );
}
