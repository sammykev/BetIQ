"use client";

import clsx from "clsx";
import type { MatchEvent, MatchdayMatch } from "@/lib/matchday";

// A match's live (and full-time) numbers from ESPN: key events in order, then
// each team stat as a split bar. Refreshed every few minutes while it's on.

const STAT_ROWS: { key: string; label: string; pct?: boolean }[] = [
  { key: "possession", label: "Possession", pct: true },
  { key: "shots", label: "Shots" },
  { key: "sot", label: "Shots on target" },
  { key: "corners", label: "Corners" },
  { key: "fouls", label: "Fouls" },
  { key: "offsides", label: "Offsides" },
  { key: "saves", label: "Saves" },
  { key: "yellow", label: "Yellow cards" },
  { key: "red", label: "Red cards" },
];

function EventIcon({ kind }: { kind: MatchEvent["kind"] }) {
  if (kind === "yellow") return <span className="inline-block w-2.5 h-3.5 rounded-[2px] bg-amber-400" aria-label="Yellow card" />;
  if (kind === "red") return <span className="inline-block w-2.5 h-3.5 rounded-[2px] bg-rose-500" aria-label="Red card" />;
  return <span aria-label="Goal" className="text-[13px] leading-none">⚽</span>;
}

const NOTE: Partial<Record<MatchEvent["kind"], string>> = { penalty_goal: "pen", own_goal: "og" };

export function LiveStats({ m, compact = false }: { m: MatchdayMatch; compact?: boolean }) {
  const stats = m.stats ?? {};
  const rows = STAT_ROWS.filter(r => stats[r.key]);
  const events = m.events ?? [];
  if (!rows.length && !events.length) return null;
  return (
    <div className="space-y-3">
      {events.length > 0 && (
        <ul className="space-y-1">
          {events.map((e, i) => {
            const text = (
              <span className="inline-flex items-center gap-1.5 min-w-0">
                <EventIcon kind={e.kind} />
                <span className="truncate text-n-200">{e.player ?? (e.kind === "yellow" || e.kind === "red" ? "Card" : "Goal")}</span>
                {NOTE[e.kind] && <span className="text-[10px] text-n-500 uppercase">{NOTE[e.kind]}</span>}
              </span>
            );
            return (
              <li key={i} className="grid grid-cols-[1fr_auto_1fr] items-center gap-2 text-xs">
                <span className="flex justify-end min-w-0">{e.side === "home" ? text : null}</span>
                <span className="font-mono text-[11px] text-n-400 tnum w-12 text-center">{e.minute}</span>
                <span className="flex justify-start min-w-0">{e.side === "away" ? text : null}</span>
              </li>
            );
          })}
        </ul>
      )}
      {rows.length > 0 && (
        <div className={clsx("space-y-2", compact && "space-y-1.5")}>
          {rows.map(r => {
            const [h, a] = stats[r.key] as [number, number];
            const total = h + a;
            const hw = total > 0 ? (h / total) * 100 : 50;
            const fmt = (v: number) => (r.pct ? `${Math.round(v)}%` : String(Math.round(v)));
            return (
              <div key={r.key}>
                <div className="flex justify-between text-[11px] tnum">
                  <span className={clsx(h > a ? "text-n-0 font-semibold" : "text-n-400")}>{fmt(h)}</span>
                  <span className="text-n-500">{r.label}</span>
                  <span className={clsx(a > h ? "text-n-0 font-semibold" : "text-n-400")}>{fmt(a)}</span>
                </div>
                <div className="flex h-1.5 gap-0.5 mt-0.5">
                  <div className="flex-1 flex justify-end rounded-l-full bg-n-800 overflow-hidden">
                    <div className={clsx("h-full rounded-l-full", h >= a ? "bg-accent" : "bg-n-500")} style={{ width: `${hw}%` }} />
                  </div>
                  <div className="flex-1 rounded-r-full bg-n-800 overflow-hidden">
                    <div className={clsx("h-full rounded-r-full", a >= h ? "bg-accent" : "bg-n-500")} style={{ width: `${100 - hw}%` }} />
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
