"use client";

import { useCallback, useState, useSyncExternalStore, type ReactNode } from "react";
import clsx from "clsx";
import { ChevronDown, ChevronsDownUp, ChevronsUpDown, Pin } from "lucide-react";
import { CompetitionBadge } from "./CompetitionBadge";

// A day's matches under their league, like Flashscore: each league a header
// (tap to fold it away) with its matches below; pinned leagues come first.
// Pins are kept on this device, per sport.

export interface LeagueInfo { id: string; name: string; flag?: string }

const pinKey = (sport: string) => `betiq:pinned:${sport}`;

// One store for every list on the page (cards, results, the optimizer's
// slip), so a pin made in one shows in all of them at once
const pinStore: Record<string, string[]> = {};
const listeners = new Set<() => void>();
function readPins(sport: string): string[] {
  if (!(sport in pinStore)) {
    try {
      const raw = typeof window !== "undefined" ? localStorage.getItem(pinKey(sport)) : null;
      pinStore[sport] = raw ? (JSON.parse(raw) as unknown[]).filter((x): x is string => typeof x === "string") : [];
    } catch { pinStore[sport] = []; }
  }
  return pinStore[sport];
}
export function togglePin(sport: string, id: string): void {
  const ps = readPins(sport);
  pinStore[sport] = ps.includes(id) ? ps.filter(x => x !== id) : [...ps, id];
  try { localStorage.setItem(pinKey(sport), JSON.stringify(pinStore[sport])); } catch { /* storage off */ }
  listeners.forEach(l => l());
}
const NO_PINS: string[] = [];

/** The leagues pinned on this device for a sport, and a toggle. */
export function usePinnedLeagues(sport: string): [string[], (id: string) => void] {
  const pins = useSyncExternalStore(
    (l) => { listeners.add(l); return () => { listeners.delete(l); }; },
    () => readPins(sport),
    () => NO_PINS,
  );
  const toggle = useCallback((id: string) => togglePin(sport, id), [sport]);
  return [pins, toggle];
}

/** Pinned leagues first (in pin order), the rest as they came. */
export function pinnedFirst<G>(groups: G[], pinned: (g: G) => number): G[] {
  return groups.map((g, i) => ({ g, i, p: pinned(g) }))
    .sort((a, b) => (a.p < 0 ? Infinity : a.p) - (b.p < 0 ? Infinity : b.p) || a.i - b.i)
    .map(x => x.g);
}

/** The pin toggle in a league header. */
export function PinButton({ pinned, name, onToggle }: { pinned: boolean; name: string; onToggle: () => void }) {
  return (
    <button type="button" onClick={e => { e.stopPropagation(); onToggle(); }} aria-pressed={pinned}
      aria-label={pinned ? `Unpin ${name}` : `Pin ${name} to the top`} title={pinned ? "Unpin" : "Pin to the top"}
      className={clsx("shrink-0 rounded-lg p-1.5 transition-colors", pinned ? "text-accent" : "text-n-500 hover:text-n-200")}>
      <Pin size={15} className={clsx(pinned && "fill-current")} />
    </button>
  );
}

/** Which leagues are open. Sport pages start with every league collapsed
 *  except the pinned ones; a header's tap (or "Expand all") overrides that
 *  while the page is open. */
export function useLeagueOpen(defaultOpen: boolean, pinned: string[] = NO_PINS) {
  const [over, setOver] = useState<Record<string, boolean>>({});
  const isOpen = useCallback((id: string) => over[id] ?? (defaultOpen || pinned.includes(id)),
    [over, defaultOpen, pinned]);
  const toggle = useCallback((id: string) => setOver(o => ({ ...o, [id]: !(o[id] ?? (defaultOpen || pinned.includes(id))) })),
    [defaultOpen, pinned]);
  const setAll = useCallback((ids: string[], open: boolean) =>
    setOver(o => { const n = { ...o }; for (const id of ids) n[id] = open; return n; }), []);
  return { isOpen, toggle, setAll };
}

/** "Expand all" while any league is collapsed, else "Collapse all". */
export function ExpandAllButton({ ids, isOpen, setAll, className }: {
  ids: string[]; isOpen: (id: string) => boolean; setAll: (ids: string[], open: boolean) => void; className?: string;
}) {
  if (ids.length < 2) return null;
  const anyClosed = ids.some(id => !isOpen(id));
  return (
    <button type="button" onClick={() => setAll(ids, anyClosed)} aria-label={anyClosed ? "Expand all leagues" : "Collapse all leagues"}
      className={clsx("inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[12px] font-semibold text-n-300 hover:text-n-0 hover:bg-n-800/60 transition-colors", className)}>
      {anyClosed ? <ChevronsUpDown size={14} aria-hidden="true" /> : <ChevronsDownUp size={14} aria-hidden="true" />}
      {anyClosed ? "Expand all" : "Collapse all"}
    </button>
  );
}

export function LeagueSections<T>({ sport, items, leagueOf, itemKey, render, badgeSport }: {
  sport: string;
  /** CompetitionBadge's sport for logo lookups (Soccer by default) */
  badgeSport?: string;
  items: T[];
  leagueOf: (item: T) => LeagueInfo;
  itemKey: (item: T, i: number) => string;
  render: (item: T, i: number) => ReactNode;
}) {
  const [pins, toggle] = usePinnedLeagues(sport);
  const { isOpen, toggle: fold, setAll } = useLeagueOpen(false, pins);

  // Leagues in the order their first match plays (items come sorted); pinned first, in pin order
  const groups = new Map<string, { league: LeagueInfo; items: T[] }>();
  for (const it of items) {
    const lg = leagueOf(it);
    const g = groups.get(lg.id) ?? { league: lg, items: [] };
    g.items.push(it);
    groups.set(lg.id, g);
  }
  const ordered = pinnedFirst(Array.from(groups.values()), g => pins.indexOf(g.league.id));

  return (
    <div className="space-y-5">
      <div className="flex justify-end -mb-3">
        <ExpandAllButton ids={ordered.map(g => g.league.id)} isOpen={isOpen} setAll={setAll} className="-mr-2" />
      </div>
      {ordered.map(({ league, items: list }) => {
        const pinned = pins.includes(league.id);
        const open = isOpen(league.id);
        return (
          <section key={league.id} className="space-y-3">
            <div className={clsx("flex items-center gap-2 border-b pb-2", pinned ? "border-accent/40" : "border-n-800")}>
              <button type="button" onClick={() => fold(league.id)} aria-expanded={open}
                className="flex min-w-0 flex-1 items-center gap-2 text-left">
                <ChevronDown size={16} className={clsx("shrink-0 text-n-500 transition-transform", !open && "-rotate-90")} />
                <CompetitionBadge name={league.name} fallbackEmoji={league.flag || "🏆"} size={18} {...(badgeSport ? { sport: badgeSport } : {})} />
                <span className="truncate font-display font-bold text-n-0">{league.name}</span>
                <span className="shrink-0 tnum text-xs font-semibold text-n-500">{list.length}</span>
              </button>
              <PinButton pinned={pinned} name={league.name} onToggle={() => toggle(league.id)} />
            </div>
            {open && (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {list.map((it, i) => <div key={itemKey(it, i)} className="flex flex-col [&>*]:flex-1">{render(it, i)}</div>)}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
