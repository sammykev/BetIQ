"use client";

import { useState } from "react";
import clsx from "clsx";
import { ChevronDown } from "lucide-react";
import { PinButton, pinnedFirst, useFolded, usePinnedLeagues } from "@/components/LeagueSections";
import { CompetitionBadge } from "@/components/CompetitionBadge";
import { FormDots } from "@/components/BasketballCard";
import { VerdictIcon } from "@/components/MatchdayList";
import { localTime } from "@/lib/matchTime";
import { TennisFacts } from "@/components/TennisFacts";
import { PlayerRatings } from "@/components/Ratings";
import { RK_WORDS, type RKMatchdayMatch, tournamentLabel, LOGO_SPORT } from "@/lib/racket";
import type { RacketSport } from "@/lib/tennis";
import { Reveal } from "@/components/ui/reveal";
import { SportStats } from "@/components/LiveStats";
import { useAccess } from "@/lib/access";

const pct = (x?: number | null) => (typeof x === "number" ? `${Math.round(x * 100)}%` : "—");

/** Started 10+ minutes ago with no score yet (or ended, final on its way). */
const awaiting = (m: RKMatchdayMatch, now = Date.now()) =>
  m.status === "scheduled" && !!m.time && Date.parse(`${m.date}T${m.time}:00Z`) < now - 10 * 60_000;

function Status({ m }: { m: RKMatchdayMatch }) {
  if (m.status === "live") {
    return <span className="text-[11px] font-bold text-danger tnum inline-flex items-center gap-1">
      <span className="w-1.5 h-1.5 rounded-full bg-danger animate-pulse shrink-0" /><span className="truncate">{m.minute || "Live"}</span></span>;
  }
  if (m.status === "finished") return <span className="text-[11px] font-bold text-n-400">{m.ret ? "Ret." : "FT"}</span>;
  if (awaiting(m)) return <span className="text-[11px] font-bold text-n-400" title="Waiting for the score">…</span>;
  return <span className="text-[12px] font-semibold text-n-300 tnum">{m.time ? localTime(m.date, m.time) : "TBD"}</span>;
}

/** Each set's games (table tennis: each game's points). */
function Sets({ m, sport }: { m: RKMatchdayMatch; sport: RacketSport }) {
  const s = m.periods ?? [];
  if (!s.length) return null;
  const w = RK_WORDS[sport];
  return (
    <div className="overflow-x-auto">
      <table className="text-[12px] tnum">
        <thead>
          <tr className="text-n-500">
            <th className="text-left font-semibold pr-4" />
            {s.map((_, i) => <th key={i} className="font-semibold px-2">{w.set === "set" ? "S" : "G"}{i + 1}</th>)}
            <th className="font-semibold pl-3 text-n-300 capitalize">{w.sets}</th>
          </tr>
        </thead>
        <tbody>
          {[m.home, m.away].map((player, side) => (
            <tr key={player + side}>
              <td className="pr-4 text-n-300 truncate max-w-[9rem]">{player}</td>
              {s.map((p, i) => <td key={i} className={clsx("px-2 text-center", p[side] > p[1 - side] ? "text-n-0 font-semibold" : "text-n-400")}>{p[side]}</td>)}
              <td className="pl-3 text-center font-bold text-n-0">{m.score?.[side] ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const GRADE_LABELS: Record<RacketSport, Record<string, string>> = {
  tennis: { tip: "Our tip", games: "Total games", best: "Best line" },
  table_tennis: { tip: "Our tip", games: "Total points", best: "Best line" },
};

/** Each player's last 5, numbers and meetings (as on the card's modal), on request. */
function FormAndStats({ m, sport }: { m: RKMatchdayMatch; sport: RacketSport }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="space-y-2">
      <button type="button" onClick={() => setOpen(o => !o)} aria-expanded={open}
        className="text-xs font-semibold text-accent inline-flex items-center gap-1">
        {open ? "Hide" : "Show"} form &amp; stats <ChevronDown size={12} className={clsx("transition-transform", open && "rotate-180")} />
      </button>
      {open && <TennisFacts m={{ home: m.home, away: m.away, date: m.date, time: m.time, league: m.league }} sport={sport} />}
    </div>
  );
}

function Detail({ m, sport }: { m: RKMatchdayMatch; sport: RacketSport }) {
  const showStats = useAccess().shown("live_stats");
  const p = m.pred;
  const grades = Object.entries(m.grades ?? {});
  const facts = [
    p.tip_goals ? `${p.tip_goals} · ${pct(p.goals_confidence)}` : null,
    m.best ? `Best line: ${m.best.label} · ${pct(m.best.prob)} @ ${m.best.odds.toFixed(2)}` : null,
    m.surface ? m.surface : null,
  ].filter(Boolean) as string[];
  const h = Math.round((p.p_home ?? 0) * 100);
  const won = m.status === "finished" && m.score ? (m.score[0] > m.score[1] ? 0 : 1) : -1;
  return (
    <div className="px-3 sm:px-4 pb-4 pt-1 space-y-4 bg-surface-sunken/60">
      <div className="space-y-2">
        <p className="eyebrow">Before the start{m.locked ? "" : " · updates until the start"}</p>
        <div className="flex h-1.5 rounded-full overflow-hidden gap-0.5">
          <div style={{ width: `${h}%` }} className={won === 0 ? "bg-accent" : "bg-n-600"} />
          <div style={{ width: `${100 - h}%` }} className={won === 1 ? "bg-accent" : "bg-n-600"} />
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
      {(m.status === "live" || m.status === "finished") && <Sets m={m} sport={sport} />}
      {showStats && <SportStats rows={m.live_stats} status={m.status} minute={m.minute} />}
      {m.status === "finished" && (
        <div className="space-y-1.5">
          <p className="eyebrow">How our picks did</p>
          {m.ret ? (
            <p className="text-xs text-n-400">A player retired: picks on this match are void, as on SportyBet.</p>
          ) : grades.length === 0 ? (
            <p className="text-xs text-n-400">No picks to grade for this match.</p>
          ) : (
            <ul className="divide-y divide-n-800 rounded-xl [box-shadow:var(--ring-control)] bg-surface">
              {grades.map(([k, g]) => g && (
                <li key={k} className="flex items-center gap-3 px-3 py-2 text-xs">
                  <span className="w-24 shrink-0 text-n-400">{GRADE_LABELS[sport][k] ?? k}</span>
                  <span className="flex-1 min-w-0 truncate text-n-0 font-semibold">{g.pick}</span>
                  <span className="tnum text-n-400">{pct(g.prob)}</span>
                  <VerdictIcon verdict={g.verdict} size={12} />
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {m.status === "live" && <p className="text-xs text-n-400">In play: the score updates every minute. Picks are graded when the match ends.</p>}
      <PlayerRatings r={p.ratings} sport={sport} home={m.home} away={m.away} />
      <FormAndStats m={m} sport={sport} />
      {awaiting(m) && <p className="text-xs text-n-400">Started: waiting for the score from SportyBet.</p>}
    </div>
  );
}

function Row({ m, sport, open, onToggle }: { m: RKMatchdayMatch; sport: RacketSport; open: boolean; onToggle: () => void }) {
  const [hs, as] = m.score ?? [null, null];
  const winner = m.status === "finished" && m.score ? (m.score[0] > m.score[1] ? 0 : m.score[1] > m.score[0] ? 1 : -1) : -1;
  const tip = m.pred.tip_code === "1" ? m.home : m.pred.tip_code === "2" ? m.away : null;
  const current = m.status === "live" && m.periods?.length ? m.periods[m.periods.length - 1] : null;
  return (
    <li>
      <button onClick={onToggle} aria-expanded={open}
        className="w-full grid grid-cols-[60px_minmax(0,1fr)_auto] items-center gap-2 sm:gap-3 px-3 sm:px-4 py-2.5 text-left hover:bg-surface-raised/60 transition-colors duration-150">
        <span className="text-center min-w-0"><Status m={m} /></span>
        <span className="min-w-0 space-y-1">
          {[m.home, m.away].map((player, i) => (
            <span key={player + i} className="flex items-center gap-2 min-w-0">
              <span className={clsx("truncate text-[14px] flex-1", winner === i ? "text-n-0 font-bold" : "text-n-200")}>{player}</span>
              {(i === 0 ? m.home_form : m.away_form) && <span className="hidden sm:inline"><FormDots form={(i === 0 ? m.home_form : m.away_form)!} /></span>}
              {current && <span className="tnum text-[12px] text-n-400 w-5 text-right shrink-0">{current[i]}</span>}
              <span className={clsx("tnum text-[14px] font-bold w-6 text-right shrink-0",
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
      <Reveal open={open}><Detail m={m} sport={sport} /></Reveal>
    </li>
  );
}

/** A day's tennis or table tennis matches grouped by tournament, like the football list. */
export function RacketMatchList({ matches, sport }: { matches: RKMatchdayMatch[]; sport: RacketSport }) {
  const [openKey, setOpenKey] = useState<string | null>(null);
  // Under each league (pinned ones first, shared with the cards above); a header folds its matches away
  const [pins, togglePin] = usePinnedLeagues(sport);
  const [folded, fold] = useFolded();
  const byLeague: { id: string; name: string; flag: string; items: RKMatchdayMatch[] }[] = [];
  for (const m of matches) {
    const id = m.league || m.league_name;
    const g = byLeague.find(x => x.id === id);
    if (g) g.items.push(m);
    else byLeague.push({ id, name: tournamentLabel(m.league_name || m.league), flag: m.flag, items: [m] });
  }
  const groups = pinnedFirst(byLeague, g => pins.indexOf(g.id));
  return (
    <div className="space-y-3">
      {groups.map(g => {
        const settled = g.items.filter(m => m.grades?.tip);
        const right = settled.filter(m => m.grades!.tip!.verdict === "won").length;
        return (
          <section key={g.id} className={clsx("card overflow-hidden", pins.includes(g.id) && "[box-shadow:0_0_0_1px_rgb(var(--accent)/0.35)]")}>
            <header className={clsx("flex items-center gap-2 px-3 sm:px-4 py-1.5 bg-surface-sunken/60", !folded.has(g.id) && "border-b border-n-800")}>
              <button type="button" onClick={() => fold(g.id)} aria-expanded={!folded.has(g.id)} className="flex min-w-0 flex-1 items-center gap-2 text-left">
                <ChevronDown size={14} className={clsx("shrink-0 text-n-500 transition-transform", folded.has(g.id) && "-rotate-90")} />
                <CompetitionBadge name={g.name} fallbackEmoji={g.flag} size={16} className="text-sm" sport={LOGO_SPORT[sport]} />
                <span className="text-[12px] font-bold uppercase tracking-[0.06em] text-n-200 truncate">{g.name}</span>
              </button>
              <span className="text-[11px] text-n-500 tnum shrink-0">
                {settled.length ? `${right}/${settled.length} tips right` : `${g.items.length} ${g.items.length === 1 ? "match" : "matches"}`}
              </span>
              <PinButton pinned={pins.includes(g.id)} name={g.name} onToggle={() => togglePin(g.id)} />
            </header>
            {!folded.has(g.id) && <ul className="divide-y divide-n-800">
              {g.items.map(m => (
                <Row key={m.key} m={m} sport={sport} open={openKey === m.key} onToggle={() => setOpenKey(openKey === m.key ? null : m.key)} />
              ))}
            </ul>}
          </section>
        );
      })}
    </div>
  );
}
