"use client";

import { useState } from "react";
import clsx from "clsx";
import { ChevronDown } from "lucide-react";
import { CompetitionBadge } from "@/components/CompetitionBadge";
import { Crest } from "@/components/BasketballCard";
import { VerdictIcon } from "@/components/MatchdayList";
import { localTime } from "@/lib/matchTime";
import { BasketballFacts } from "@/components/BasketballFacts";
import { BB_GRADE_LABELS, type BasketballPrediction, type BBMatchdayMatch } from "@/lib/basketball";
import { Reveal } from "@/components/ui/reveal";
import { SportStats } from "@/components/LiveStats";
import { useAccess } from "@/lib/access";

const pct = (x?: number | null) => (typeof x === "number" ? `${Math.round(x * 100)}%` : "—");

/** Tipped off 10+ minutes ago with no score yet (or ended, final on its way). */
const awaiting = (m: BBMatchdayMatch, now = Date.now()) =>
  m.status === "scheduled" && !!m.time && Date.parse(`${m.date}T${m.time}:00Z`) < now - 10 * 60_000;

function Status({ m }: { m: BBMatchdayMatch }) {
  if (m.status === "live") {
    return <span className="text-[11px] font-bold text-danger tnum inline-flex items-center gap-1">
      <span className="w-1.5 h-1.5 rounded-full bg-danger animate-pulse" />{m.minute || "Live"}</span>;
  }
  if (m.status === "finished") return <span className="text-[11px] font-bold text-n-400">{m.aet ? "OT" : "FT"}</span>;
  if (awaiting(m)) return <span className="text-[11px] font-bold text-n-400" title="Waiting for the score">…</span>;
  return <span className="text-[12px] font-semibold text-n-300 tnum">{m.time ? localTime(m.date, m.time) : "TBD"}</span>;
}

function Quarters({ m }: { m: BBMatchdayMatch }) {
  const q = m.periods ?? [];
  if (!q.length) return null;
  return (
    <div className="overflow-x-auto">
      <table className="text-[12px] tnum">
        <thead>
          <tr className="text-n-500">
            <th className="text-left font-semibold pr-4" />
            {q.map((_, i) => <th key={i} className="font-semibold px-2">Q{i + 1}</th>)}
            <th className="font-semibold pl-3 text-n-300">{m.aet ? "Final (OT)" : "Final"}</th>
          </tr>
        </thead>
        <tbody>
          {[m.home, m.away].map((team, side) => (
            <tr key={team}>
              <td className="pr-4 text-n-300 truncate max-w-[9rem]">{team}</td>
              {q.map((p, i) => <td key={i} className="px-2 text-center text-n-200">{p[side]}</td>)}
              <td className="pl-3 text-center font-bold text-n-0">{m.score?.[side] ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The teams' form, averages and meetings (as on the match card's modal), on request. */
function FormAndStats({ m }: { m: BBMatchdayMatch }) {
  const [open, setOpen] = useState(false);
  const p = { ...m.pred, home: m.home, away: m.away, date: m.date, time: m.time, league: m.league,
              league_name: m.league_name, flag: m.flag, home_logo: m.home_logo, away_logo: m.away_logo,
              sportybet_event_id: m.id } as unknown as BasketballPrediction;
  return (
    <div className="space-y-2">
      <button type="button" onClick={() => setOpen(o => !o)} aria-expanded={open}
        className="text-xs font-semibold text-accent inline-flex items-center gap-1">
        {open ? "Hide" : "Show"} form &amp; stats <ChevronDown size={12} className={clsx("transition-transform", open && "rotate-180")} />
      </button>
      {open && <BasketballFacts p={p} />}
    </div>
  );
}

function Detail({ m }: { m: BBMatchdayMatch }) {
  const showStats = useAccess().shown("live_stats");
  const p = m.pred;
  const grades = Object.entries(m.grades ?? {});
  const facts = [
    typeof p.exp_home_pts === "number" && typeof p.exp_away_pts === "number"
      ? `Expected ${Math.round(p.exp_home_pts)}–${Math.round(p.exp_away_pts)}` : null,
    p.tip_goals ? `${p.tip_goals} · ${pct(p.goals_confidence)}` : null,
    m.best ? `Best line: ${m.best.label} · ${pct(m.best.prob)} @ ${m.best.odds.toFixed(2)}` : null,
  ].filter(Boolean) as string[];
  const h = Math.round((p.p_home ?? 0) * 100);
  return (
    <div className="px-3 sm:px-4 pb-4 pt-1 space-y-4 bg-surface-sunken/60">
      <div className="space-y-2">
        <p className="eyebrow">Before tip-off{m.locked ? "" : " · updates until tip-off"}</p>
        <div className="flex h-1.5 rounded-full overflow-hidden gap-0.5">
          <div style={{ width: `${h}%` }} className={clsx(m.status === "finished" && m.score && m.score[0] > m.score[1] ? "bg-accent" : "bg-n-600")} />
          <div style={{ width: `${100 - h}%` }} className={clsx(m.status === "finished" && m.score && m.score[1] > m.score[0] ? "bg-accent" : "bg-n-600")} />
        </div>
        <div className="flex justify-between text-[11px] text-n-400 tnum">
          <span className="truncate">{m.home} {h}%</span><span className="truncate">{100 - h}% {m.away}</span>
        </div>
        {facts.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {facts.map(f => <span key={f} className="text-[11px] text-n-300 bg-surface [box-shadow:var(--ring-control)] rounded-md px-2 py-0.5">{f}</span>)}
          </div>
        )}
      </div>
      {(m.status === "live" || m.status === "finished") && <Quarters m={m} />}
      {showStats && <SportStats rows={m.live_stats} status={m.status} minute={m.minute} />}
      {m.status === "finished" && (
        <div className="space-y-1.5">
          <p className="eyebrow">How our picks did</p>
          {grades.length === 0 ? (
            <p className="text-xs text-n-400">No picks to grade for this game.</p>
          ) : (
            <ul className="divide-y divide-n-800 rounded-xl [box-shadow:var(--ring-control)] bg-surface">
              {grades.map(([k, g]) => g && (
                <li key={k} className="flex items-center gap-3 px-3 py-2 text-xs">
                  <span className="w-24 shrink-0 text-n-400">{BB_GRADE_LABELS[k] ?? k}</span>
                  <span className="flex-1 min-w-0 truncate text-n-0 font-semibold">{g.pick}</span>
                  <span className="tnum text-n-400">{pct(g.prob)}</span>
                  <VerdictIcon verdict={g.verdict} size={12} />
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {m.status === "live" && <p className="text-xs text-n-400">In play: the score updates every minute. Picks are graded at the final buzzer.</p>}
      <FormAndStats m={m} />
      {awaiting(m) && <p className="text-xs text-n-400">Tipped off: waiting for the score from SportyBet.</p>}
    </div>
  );
}

function Row({ m, open, onToggle }: { m: BBMatchdayMatch; open: boolean; onToggle: () => void }) {
  const [hs, as] = m.score ?? [null, null];
  const winner = m.status === "finished" && m.score ? (m.score[0] > m.score[1] ? 0 : m.score[1] > m.score[0] ? 1 : -1) : -1;
  const tip = m.pred.tip_code === "1" ? m.home : m.pred.tip_code === "2" ? m.away : null;
  return (
    <li>
      <button onClick={onToggle} aria-expanded={open}
        className="w-full grid grid-cols-[52px_minmax(0,1fr)_auto] items-center gap-2 sm:gap-3 px-3 sm:px-4 py-2.5 text-left hover:bg-surface-raised/60 transition-colors duration-150">
        <span className="text-center"><Status m={m} /></span>
        <span className="min-w-0 space-y-1">
          {[m.home, m.away].map((team, i) => (
            <span key={team + i} className="flex items-center gap-2 min-w-0">
              <Crest src={i === 0 ? m.home_logo : m.away_logo} name={team} size={18} />
              <span className={clsx("truncate text-[14px] flex-1", winner === i ? "text-n-0 font-bold" : "text-n-200")}>{team}</span>
              <span className={clsx("tnum text-[14px] font-bold w-8 text-right shrink-0",
                m.status === "live" ? "text-danger" : winner === i ? "text-n-0" : "text-n-300")}>
                {i === 0 ? hs ?? "" : as ?? ""}
              </span>
            </span>
          ))}
        </span>
        <span className="flex items-center gap-2 pl-2 sm:pl-3 border-l border-n-800 min-w-[92px] sm:min-w-[140px] max-w-[130px] sm:max-w-[200px]">
          <span className="min-w-0 flex-1 text-right">
            <span className="block text-[12px] font-semibold text-n-0 truncate">{tip ?? "No pick"}</span>
            <span className="block text-[11px] text-n-500 tnum">{pct(m.pred.tip_confidence)}</span>
          </span>
          {m.status === "finished" && m.grades?.tip ? <VerdictIcon verdict={m.grades.tip.verdict} /> : (
            <ChevronDown size={14} className={clsx("text-n-500 shrink-0 transition-transform duration-200 ease-[cubic-bezier(0.2,0,0,1)]", open && "rotate-180")} />
          )}
        </span>
      </button>
      <Reveal open={open}><Detail m={m} /></Reveal>
    </li>
  );
}

/** A day's basketball games grouped by competition, like the football list. */
export function BasketballMatchList({ matches }: { matches: BBMatchdayMatch[] }) {
  const [openKey, setOpenKey] = useState<string | null>(null);
  const groups: { name: string; flag: string; items: BBMatchdayMatch[] }[] = [];
  for (const m of matches) {
    const name = m.league_name || m.league;
    const g = groups.find(x => x.name === name);
    if (g) g.items.push(m);
    else groups.push({ name, flag: m.flag, items: [m] });
  }
  return (
    <div className="space-y-3">
      {groups.map(g => {
        const settled = g.items.filter(m => m.grades?.tip);
        const right = settled.filter(m => m.grades!.tip!.verdict === "won").length;
        return (
          <section key={g.name} className="card overflow-hidden">
            <header className="flex items-center gap-2 px-3 sm:px-4 py-2 bg-surface-sunken/60 border-b border-n-800">
              <CompetitionBadge name={g.name} fallbackEmoji={g.flag} size={16} className="text-sm" />
              <span className="text-[12px] font-bold uppercase tracking-[0.06em] text-n-200 truncate">{g.name}</span>
              <span className="ml-auto text-[11px] text-n-500 tnum shrink-0">
                {settled.length ? `${right}/${settled.length} tips right` : `${g.items.length} ${g.items.length === 1 ? "game" : "games"}`}
              </span>
            </header>
            <ul className="divide-y divide-n-800">
              {g.items.map(m => (
                <Row key={m.key} m={m} open={openKey === m.key} onToggle={() => setOpenKey(openKey === m.key ? null : m.key)} />
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
