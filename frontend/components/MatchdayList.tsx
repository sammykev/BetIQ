"use client";

import { useState } from "react";
import clsx from "clsx";
import { Check, ChevronDown, Minus, X as Cross } from "lucide-react";
import { TeamBadge } from "@/components/PredictionCard";
import { CompetitionBadge } from "@/components/CompetitionBadge";
import { localTime } from "@/lib/matchTime";
import { MARKET_LABELS, lost, won, type Grade, type MatchdayMatch } from "@/lib/matchday";
import { LiveStats } from "@/components/LiveStats";
import { useAccess } from "@/lib/access";

const pct = (x?: number | null) => (typeof x === "number" ? `${Math.round(x * 100)}%` : "—");

/** The pick a row leads with: our tip, else the goals tip. */
function leadPick(m: MatchdayMatch): { label: string; prob: number | null; grade?: Grade } {
  const p = m.pred;
  const g = m.grades ?? {};
  if (p.tip_1x2 && p.tip_code && p.tip_code !== "?") {
    const prob = typeof p.tip_confidence === "number" ? p.tip_confidence : g.tip?.prob ?? null;
    // Short in the row ("Home win"); team names are right beside it
    const short: Record<string, string> = { "1": "Home win", X: "Draw", "2": "Away win" };
    return { label: short[p.tip_code] ?? p.tip_1x2, prob, grade: g.tip };
  }
  if (p.tip_goals && p.tip_goals !== "Skip") return { label: p.tip_goals, prob: p.goals_confidence ?? null, grade: g.goals };
  return { label: "No pick", prob: null };
}

export function VerdictIcon({ verdict, size = 14 }: { verdict?: Grade["verdict"] | "void" | "pending"; size?: number }) {
  if (won(verdict as Grade["verdict"])) {
    return <span className="inline-flex items-center justify-center rounded-full bg-accent/15 text-accent shrink-0" style={{ width: size + 6, height: size + 6 }}
      aria-label="Won"><Check size={size - 2} strokeWidth={3} /></span>;
  }
  if (lost(verdict as Grade["verdict"])) {
    return <span className="inline-flex items-center justify-center rounded-full bg-danger/15 text-danger shrink-0" style={{ width: size + 6, height: size + 6 }}
      aria-label="Lost"><Cross size={size - 2} strokeWidth={3} /></span>;
  }
  if (verdict === "push" || verdict === "void") {
    return <span className="inline-flex items-center justify-center rounded-full bg-n-800 text-n-400 shrink-0" style={{ width: size + 6, height: size + 6 }}
      aria-label="Stake back"><Minus size={size - 2} strokeWidth={3} /></span>;
  }
  return null;
}

export function StatusCell({ m }: { m: MatchdayMatch }) {
  if (m.status === "live") {
    return <span className="text-[11px] font-bold text-danger tnum inline-flex items-center gap-1">
      <span className="w-1.5 h-1.5 rounded-full bg-danger animate-pulse" />{m.minute || "Live"}</span>;
  }
  if (m.status === "finished") return <span className="text-[11px] font-bold text-n-400">{m.aet ? "AET" : "FT"}</span>;
  if (m.status === "postponed") return <span className="text-[11px] font-bold text-warn">PP</span>;
  return <span className="text-[12px] font-semibold text-n-300 tnum">{m.time ? localTime(m.date, m.time) : "TBD"}</span>;
}

function Detail({ m, onOpen }: { m: MatchdayMatch; onOpen?: (m: MatchdayMatch) => void }) {
  const p = m.pred;
  const liveStats = useAccess().shown("live_stats") && !!(m.stats || m.events);
  const h = Math.round((p.p_home ?? 0) * 100), d = Math.round((p.p_draw ?? 0) * 100);
  const a = Math.max(0, 100 - h - d);
  const actual = m.score ? (m.score[0] > m.score[1] ? "1" : m.score[0] < m.score[1] ? "2" : "X") : null;
  const segs = [{ key: "1", w: h, name: m.home }, { key: "X", w: d, name: "Draw" }, { key: "2", w: a, name: m.away }];
  const grades = Object.entries(m.grades ?? {});
  const facts = [
    p.tip_goals && p.tip_goals !== "Skip" ? `Goals tip ${p.tip_goals} · ${pct(p.goals_confidence)}` : null,
    typeof p.p_over25 === "number" ? `Over 2.5 · ${pct(p.p_over25)}` : null,
    typeof p.p_btts === "number" ? `Both score · ${pct(p.p_btts)}` : null,
    typeof p.corners_mean === "number" ? `Corners ~${p.corners_mean.toFixed(1)}` : null,
    typeof p.bookings_mean === "number" ? `Bookings ~${p.bookings_mean.toFixed(1)}` : null,
    p.referee ? `Referee ${p.referee}` : null,
  ].filter(Boolean) as string[];

  return (
    <div className="px-3 sm:px-4 pb-4 pt-1 space-y-4 bg-surface-sunken/60">
      <div className="space-y-2">
        <p className="eyebrow">Before kick-off{m.locked ? "" : " · updates until kick-off"}</p>
        <div className="flex h-1.5 rounded-full overflow-hidden gap-0.5">
          {segs.map(s => (
            <div key={s.key} style={{ width: `${s.w}%` }}
              className={clsx(actual === s.key ? "bg-accent" : s.key === "X" ? "bg-n-700" : "bg-n-600")} />
          ))}
        </div>
        <div className="grid grid-cols-3 text-[11px] tnum">
          {segs.map((s, i) => (
            <span key={s.key} className={clsx("truncate", i === 1 && "text-center", i === 2 && "text-right",
              actual === s.key ? "text-accent font-semibold" : "text-n-400")}>
              {s.key === "X" ? "Draw" : s.key} {s.w}%
            </span>
          ))}
        </div>
        {facts.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {facts.map(f => <span key={f} className="text-[11px] text-n-300 bg-surface border border-n-800 rounded-md px-2 py-0.5">{f}</span>)}
          </div>
        )}
      </div>

      {m.status === "finished" && (
        <div className="space-y-1.5">
          <p className="eyebrow">How our picks did{m.aet ? "" : ""}</p>
          {m.aet ? (
            <p className="text-xs text-n-400">Went to extra time: bets settle on 90 minutes, which the source doesn&apos;t give, so it isn&apos;t graded.</p>
          ) : grades.length === 0 ? (
            <p className="text-xs text-n-400">No picks to grade for this match.</p>
          ) : (
            <ul className="divide-y divide-n-800 rounded-xl border border-n-800 bg-surface">
              {grades.map(([market, g]) => (
                <li key={market} className="flex items-center gap-3 px-3 py-2 text-xs">
                  <span className="w-28 shrink-0 text-n-400">{MARKET_LABELS[market] ?? market}</span>
                  <span className="flex-1 min-w-0 truncate text-n-0 font-semibold">{g.pick}</span>
                  <span className="tnum text-n-400">{pct(g.prob)}</span>
                  <VerdictIcon verdict={g.verdict} size={12} />
                </li>
              ))}
            </ul>
          )}
          {liveStats ? (
            <div className="rounded-xl border border-n-800 bg-surface px-3 py-3">
              <p className="eyebrow mb-2">Match stats</p>
              <LiveStats m={m} compact />
            </div>
          ) : (m.corners || m.bookings || m.sot) && (
            <p className="text-[11px] text-n-500 tnum">
              {[m.sot && m.shots ? `Shots ${m.shots[0]}–${m.shots[1]} (on target ${m.sot[0]}–${m.sot[1]})`
                  : m.sot && `Shots on target ${m.sot[0]}–${m.sot[1]}`,
                m.corners && `Corners ${m.corners[0]}–${m.corners[1]}`,
                m.bookings && `Booking points ${m.bookings[0]}–${m.bookings[1]}`].filter(Boolean).join(" · ")}
            </p>
          )}
        </div>
      )}
      {m.status === "live" && (
        <div className="space-y-2">
          {liveStats && (
            <div className="rounded-xl border border-n-800 bg-surface px-3 py-3">
              <p className="eyebrow mb-2 flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-danger animate-pulse" /> Live stats · {m.minute || "in play"}
              </p>
              <LiveStats m={m} compact />
            </div>
          )}
          <p className="text-xs text-n-400">In play: updated every few minutes. Picks are graded at full time.</p>
        </div>
      )}
      {m.status === "postponed" && <p className="text-xs text-n-400">Postponed: nothing to grade.</p>}
      {m.status !== "postponed" && onOpen && (
        <button onClick={() => onOpen(m)} className="text-xs font-semibold text-accent hover:underline">
          {m.status === "live" ? "Follow it on the match page →" : m.status === "finished" ? "Match page →" : "Full match analysis →"}
        </button>
      )}
    </div>
  );
}

function Row({ m, open, onToggle, onOpen }: {
  m: MatchdayMatch; open: boolean; onToggle: () => void; onOpen?: (m: MatchdayMatch) => void;
}) {
  const lead = leadPick(m);
  const [hs, as] = m.score ?? [null, null];
  const winner = m.status === "finished" && m.score ? (m.score[0] > m.score[1] ? 0 : m.score[1] > m.score[0] ? 1 : -1) : -1;
  return (
    <li>
      <button onClick={onToggle} aria-expanded={open}
        className="w-full grid grid-cols-[44px_minmax(0,1fr)_auto] items-center gap-2 sm:gap-3 px-3 sm:px-4 py-2.5 text-left hover:bg-surface-raised/60 transition-colors">
        <span className="text-center"><StatusCell m={m} /></span>
        <span className="min-w-0 space-y-1">
          {[m.home, m.away].map((team, i) => (
            <span key={team + i} className="flex items-center gap-2 min-w-0">
              <TeamBadge name={team} size={18} />
              <span className={clsx("truncate text-[14px] flex-1", winner === i ? "text-n-0 font-bold" : "text-n-200")}>{team}</span>
              <span className={clsx("tnum text-[14px] font-bold w-5 text-right shrink-0",
                m.status === "live" ? "text-danger" : winner === i ? "text-n-0" : "text-n-300")}>
                {i === 0 ? hs ?? "" : as ?? ""}
              </span>
            </span>
          ))}
        </span>
        <span className="flex items-center gap-2 pl-2 sm:pl-3 border-l border-n-800 min-w-[92px] sm:min-w-[140px] max-w-[130px] sm:max-w-[200px]">
          <span className="min-w-0 flex-1 text-right">
            <span className="block text-[12px] font-semibold text-n-0 truncate">{lead.label}</span>
            <span className="block text-[11px] text-n-500 tnum">{pct(lead.prob)}</span>
          </span>
          {m.status === "finished" && lead.grade ? <VerdictIcon verdict={lead.grade.verdict} /> : (
            <ChevronDown size={14} className={clsx("text-n-500 shrink-0 transition-transform", open && "rotate-180")} />
          )}
        </span>
      </button>
      {open && <Detail m={m} onOpen={onOpen} />}
    </li>
  );
}

/** A day's matches grouped by competition, flashscore-style. */
export function MatchdayList({ matches, onOpen }: { matches: MatchdayMatch[]; onOpen?: (m: MatchdayMatch) => void }) {
  const [openKey, setOpenKey] = useState<string | null>(null);
  const groups: { name: string; flag: string; items: MatchdayMatch[] }[] = [];
  for (const m of matches) {
    const g = groups.find(x => x.name === (m.league_name || m.league));
    if (g) g.items.push(m);
    else groups.push({ name: m.league_name || m.league, flag: m.flag, items: [m] });
  }
  return (
    <div className="space-y-3">
      {groups.map(g => {
        const settled = g.items.filter(m => m.grades?.tip);
        const right = settled.filter(m => won(m.grades!.tip.verdict)).length;
        return (
          <section key={g.name} className="card overflow-hidden">
            <header className="flex items-center gap-2 px-3 sm:px-4 py-2 bg-surface-sunken/60 border-b border-n-800">
              <CompetitionBadge name={g.name} fallbackEmoji={g.flag} size={16} className="text-sm" />
              <span className="text-[12px] font-bold uppercase tracking-[0.06em] text-n-200 truncate">{g.name}</span>
              <span className="ml-auto text-[11px] text-n-500 tnum shrink-0">
                {settled.length ? `${right}/${settled.length} tips right` : `${g.items.length} ${g.items.length === 1 ? "match" : "matches"}`}
              </span>
            </header>
            <ul className="divide-y divide-n-800">
              {g.items.map(m => (
                <Row key={m.key} m={m} open={openKey === m.key} onOpen={onOpen}
                  onToggle={() => setOpenKey(openKey === m.key ? null : m.key)} />
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
