"use client";

import clsx from "clsx";

// How the model rates each side before a match: tennis and table tennis
// players' Elo (tennis also on the match's surface), basketball teams'
// attack and defence in points against an average team of their league.

export interface PlayerRating { elo: number; matches: number; surface?: string; surface_elo?: number; surface_matches?: number }
export interface TeamRating { attack: number; defence: number; net: number; games: number; elo?: number }
export interface SideRatings<T> { home?: T | null; away?: T | null }

const signed = (v: number) => `${v > 0 ? "+" : ""}${v.toFixed(1)}`;

function Row({ label, h, a, hs, as, note }: { label: string; h?: number | null; a?: number | null; hs: string; as: string; note?: string }) {
  const hb = h != null && a != null && h > a, ab = h != null && a != null && a > h;
  return (
    <div className="grid grid-cols-[4.5rem_1fr_4.5rem] items-baseline gap-2 py-1.5 text-xs">
      <span className={clsx("tnum", hb ? "text-n-0 font-semibold" : "text-n-400")}>{hs}</span>
      <span className="text-n-500 text-center">
        {label}{note && <span className="block text-[10px] text-n-600">{note}</span>}
      </span>
      <span className={clsx("tnum text-right", ab ? "text-n-0 font-semibold" : "text-n-400")}>{as}</span>
    </div>
  );
}

function Names({ home, away }: { home: string; away: string }) {
  return (
    <div className="flex justify-between gap-3 text-[11px] font-semibold text-n-300">
      <span className="truncate">{home}</span><span className="truncate text-right">{away}</span>
    </div>
  );
}

/** Elo's 1–1 chance from a gap: 100 points ≈ 64% (the logistic Elo uses). */
const eloChance = (gap: number) => 1 / (1 + 10 ** (-gap / 400));

/** Tennis and table tennis: each player's Elo. */
export function PlayerRatings({ r, sport, home, away }: {
  r?: SideRatings<PlayerRating> | null; sport: "tennis" | "table_tennis"; home: string; away: string;
}) {
  const h = r?.home, a = r?.away;
  if (!h && !a) return null;
  const dash = "—";
  const gap = h && a ? (h.surface_elo != null && a.surface_elo != null ? (h.elo + h.surface_elo) / 2 - (a.elo + a.surface_elo) / 2 : h.elo - a.elo) : null;
  return (
    <div className="space-y-1">
      <p className="eyebrow">Ratings (Elo)</p>
      <Names home={home} away={away} />
      <div className="divide-y divide-n-800">
        <Row label="Elo" h={h?.elo} a={a?.elo} hs={h ? String(h.elo) : dash} as={a ? String(a.elo) : dash} />
        {sport === "tennis" && (h?.surface || a?.surface) && (
          <Row label={`On ${(h?.surface || a?.surface || "").toLowerCase()}`} h={h?.surface_elo} a={a?.surface_elo}
            hs={h?.surface_elo != null ? String(h.surface_elo) : dash} as={a?.surface_elo != null ? String(a.surface_elo) : dash} />
        )}
        <Row label="Matches rated" hs={h ? String(h.matches) : dash} as={a ? String(a.matches) : dash} />
      </div>
      <p className="text-[11px] text-n-500 leading-snug">
        Everyone starts at 1500 and moves with each result, more for beating a stronger player.
        {gap != null && Math.abs(gap) >= 5
          ? ` The ${Math.round(Math.abs(gap))}-point gap alone makes ${gap > 0 ? home : away} about ${Math.round(eloChance(Math.abs(gap)) * 100)}% to win; our chance also weighs SportyBet's prices${sport === "tennis" ? " and serve and return" : ""}.`
          : !h || !a ? " A player we haven't rated yet is priced from SportyBet's odds." : ""}
      </p>
    </div>
  );
}

/** Basketball: each team's attack, defence and net, in points a game. */
export function TeamRatings({ r, home, away }: { r?: SideRatings<TeamRating> | null; home: string; away: string }) {
  const h = r?.home, a = r?.away;
  if (!h && !a) return null;
  const dash = "—";
  return (
    <div className="space-y-1">
      <p className="eyebrow">Team ratings</p>
      <Names home={home} away={away} />
      <div className="divide-y divide-n-800">
        <Row label="Net" note="pts a game vs average" h={h?.net} a={a?.net} hs={h ? signed(h.net) : dash} as={a ? signed(a.net) : dash} />
        <Row label="Attack" note="scored above average" h={h?.attack} a={a?.attack} hs={h ? signed(h.attack) : dash} as={a ? signed(a.attack) : dash} />
        <Row label="Defence" note="conceded below average" h={h?.defence} a={a?.defence} hs={h ? signed(h.defence) : dash} as={a ? signed(a.defence) : dash} />
        {(h?.elo != null || a?.elo != null) && (
          <Row label="Elo" note="wins weighted by margin" h={h?.elo} a={a?.elo}
            hs={h?.elo != null ? String(h.elo) : dash} as={a?.elo != null ? String(a.elo) : dash} />
        )}
        <Row label="Games rated" hs={h ? String(Math.round(h.games)) : dash} as={a ? String(Math.round(a.games)) : dash} />
      </div>
      <p className="text-[11px] text-n-500 leading-snug">
        From every game in the league, recent ones counting more: how many points a team scores and concedes
        against an average team of its league, home court apart. Elo (1500 is average) moves with each result,
        more for a big win; where it predicted better in the past, the expected margin leans on it.
      </p>
    </div>
  );
}
