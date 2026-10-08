"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import clsx from "clsx";
import { ChevronDown, Pin } from "lucide-react";
import { CompetitionBadge } from "./CompetitionBadge";

// A day's matches under their league, like Flashscore: each league a header
// (tap to fold it away) with its matches below; pinned leagues come first.
// Pins are kept on this device, per sport.

export interface LeagueInfo { id: string; name: string; flag?: string }

const pinKey = (sport: string) => `betiq:pinned:${sport}`;

/** The leagues pinned on this device for a sport, and a toggle. */
export function usePinnedLeagues(sport: string): [string[], (id: string) => void] {
  const [pins, setPins] = useState<string[]>([]);
  useEffect(() => {
    try {
      const raw = localStorage.getItem(pinKey(sport));
      setPins(raw ? (JSON.parse(raw) as string[]).filter(x => typeof x === "string") : []);
    } catch { setPins([]); }
  }, [sport]);
  const toggle = useCallback((id: string) => {
    setPins(ps => {
      const next = ps.includes(id) ? ps.filter(x => x !== id) : [...ps, id];
      try { localStorage.setItem(pinKey(sport), JSON.stringify(next)); } catch { /* storage off */ }
      return next;
    });
  }, [sport]);
  return [pins, toggle];
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
  const [pins, togglePin] = usePinnedLeagues(sport);
  const [folded, setFolded] = useState<Set<string>>(new Set());

  // Leagues in the order their first match plays (items come sorted); pinned first, in pin order
  const groups = new Map<string, { league: LeagueInfo; items: T[] }>();
  for (const it of items) {
    const lg = leagueOf(it);
    const g = groups.get(lg.id) ?? { league: lg, items: [] };
    g.items.push(it);
    groups.set(lg.id, g);
  }
  const ordered = Array.from(groups.values()).sort((a, b) => {
    const pa = pins.indexOf(a.league.id), pb = pins.indexOf(b.league.id);
    if (pa >= 0 || pb >= 0) return (pa < 0 ? Infinity : pa) - (pb < 0 ? Infinity : pb);
    return 0;
  });
  const fold = (id: string) => setFolded(f => {
    const n = new Set(f);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });

  return (
    <div className="space-y-5">
      {ordered.map(({ league, items: list }) => {
        const pinned = pins.includes(league.id);
        const open = !folded.has(league.id);
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
              <button type="button" onClick={() => togglePin(league.id)} aria-pressed={pinned}
                aria-label={pinned ? `Unpin ${league.name}` : `Pin ${league.name} to the top`}
                title={pinned ? "Unpin" : "Pin to the top"}
                className={clsx("shrink-0 rounded-lg p-1.5 transition-colors",
                  pinned ? "text-accent" : "text-n-500 hover:text-n-200")}>
                <Pin size={16} className={clsx(pinned && "fill-current")} />
              </button>
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
