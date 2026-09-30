"use client";

import clsx from "clsx";
import type { MatchEvent, MatchdayMatch, StatRow } from "@/lib/matchday";

// A match's live (and full-time) numbers from ESPN (or Sportradar when ESPN has none): key events in order, then
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
  { key: "dangerous", label: "Dangerous attacks" },
];

function EventIcon({ kind }: { kind: MatchEvent["kind"] }) {
  if (kind === "yellow") return <span className="inline-block w-2.5 h-3.5 rounded-[2px] bg-amber-400" aria-label="Yellow card" />;
  if (kind === "red") return <span className="inline-block w-2.5 h-3.5 rounded-[2px] bg-rose-500" aria-label="Red card" />;
  return <span aria-label="Goal" className="text-[13px] leading-none">⚽</span>;
}

const NOTE: Partial<Record<MatchEvent["kind"], string>> = { penalty_goal: "pen", own_goal: "og" };

/** `split` puts the timeline beside the stat bars on wide screens. */
export function LiveStats({ m, compact = false, split = false }: {
  m: Pick<MatchdayMatch, "stats" | "events">; compact?: boolean; split?: boolean;
}) {
  const stats = m.stats ?? {};
  const rows = STAT_ROWS.filter(r => stats[r.key]);
  const events = m.events ?? [];
  if (!rows.length && !events.length) return null;
  return (
    <div className={clsx("space-y-3", split && rows.length > 0 && events.length > 0 && "lg:space-y-0 lg:grid lg:grid-cols-2 lg:gap-8 lg:items-start")}>
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
            const fmt = (v: number) => (r.pct ? `${Math.round(v)}%` : String(Math.round(v)));
            return <StatBar key={r.key} label={r.label} h={h} a={a} hs={fmt(h)} as={fmt(a)} />;
          })}
        </div>
      )}
    </div>
  );
}

/** One stat, both sides: the numbers either end, a split bar under them. */
function StatBar({ label, h, a, hs, as }: { label: string; h: number; a: number; hs: string; as: string }) {
  const total = h + a;
  const hw = total > 0 ? (h / total) * 100 : 50;
  return (
    <div>
      <div className="flex justify-between gap-2 text-[11px] tnum">
        <span className={clsx("shrink-0", h > a ? "text-n-0 font-semibold" : "text-n-400")}>{hs}</span>
        <span className="text-n-500 truncate">{label}</span>
        <span className={clsx("shrink-0", a > h ? "text-n-0 font-semibold" : "text-n-400")}>{as}</span>
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
}

const plain = (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(1));

/** Basketball, tennis and table tennis stats: in play (with the stage) or at the final. */
export function SportStats({ rows, status, minute }: { rows?: StatRow[] | null; status: string; minute?: string | null }) {
  if (!rows?.length || (status !== "live" && status !== "finished")) return null;
  return (
    <div className="space-y-2">
      {status === "live" ? (
        <p className="eyebrow inline-flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-danger animate-pulse" /> Live stats{minute ? ` · ${minute}` : ""}
        </p>
      ) : <p className="eyebrow">Match stats</p>}
      <div className="space-y-1.5">
        {rows.map(r => <StatBar key={r.key} label={r.label} h={r.h} a={r.a} hs={r.hs ?? plain(r.h)} as={r.as ?? plain(r.a)} />)}
      </div>
    </div>
  );
}
