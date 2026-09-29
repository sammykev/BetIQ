"use client";

import { useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { AlertTriangle, ArrowRight, CalendarDays, Loader2, SearchX } from "lucide-react";
import { DateStrip } from "@/components/DateStrip";
import { DaySummaryBar } from "@/components/DaySummaryBar";
import { SportCard, type SportPrediction } from "@/components/SportCard";
import { CompetitionBadge } from "@/components/CompetitionBadge";
import { RacketMatchList } from "@/components/RacketMatchList";
import { useAuthedFetch } from "@/lib/useAuthedFetch";
import { dayLabel, localDateStr } from "@/lib/matchTime";
import { fetchRKMatchday, fetchRKStrip, RK_WORDS, type RacketPrediction, type RKDaySummary,
         type RKMatchdayMatch, type RKMatchdayResponse, type RKStripResponse } from "@/lib/racket";
import type { RacketSport } from "@/lib/tennis";

function Empty({ icon, title, body, action }: { icon: React.ReactNode; title: string; body?: string; action?: React.ReactNode }) {
  return (
    <div className="card border-dashed text-center px-6 py-16 space-y-3">
      <div className="mx-auto w-12 h-12 rounded-2xl bg-n-800/70 flex items-center justify-center text-n-400">{icon}</div>
      <p className="font-display font-bold text-xl uppercase tracking-wide text-n-0">{title}</p>
      {body && <p className="text-sm text-n-400 max-w-sm mx-auto">{body}</p>}
      {action && <div className="pt-2">{action}</div>}
    </div>
  );
}

function Count({ n, active }: { n: number; active: boolean }) {
  return <span className={clsx("tnum text-[10px] px-1.5 py-0.5 rounded-full font-semibold",
    active ? "bg-ink/15 text-ink" : "bg-n-800 text-n-400")}>{n}</span>;
}

function Heading({ title, n }: { title: string; n: number }) {
  return (
    <div className="flex items-baseline gap-3">
      <h2 className="font-display font-extrabold text-2xl uppercase tracking-wide text-n-0">{title}</h2>
      <span className="text-xs font-semibold text-n-500 tnum">{n}</span>
      <span className="flex-1 h-px bg-n-800 self-center" />
    </div>
  );
}

const summaryItems = (s: RKDaySummary, sport: RacketSport) => [
  { name: "Our tips", v: s.tip }, { name: `Total ${RK_WORDS[sport].games}`, v: s.games }, { name: "Best lines", v: s.best },
];

/**
 * The tennis and table tennis tabs, like football's and basketball's: a
 * date strip (7 days back, 14 ahead), past days graded, today's matches
 * under way live and finished, and the matches still to play as cards.
 */
export function RacketDays({ sport, preds, loading, onOpen }: {
  sport: RacketSport; preds: RacketPrediction[]; loading: boolean; onOpen: (p: RacketPrediction) => void;
}) {
  const authFetch = useAuthedFetch();
  const w = RK_WORDS[sport];
  const [strip, setStrip] = useState<RKStripResponse | null>(null);
  const [day, setDay] = useState(() => localDateStr());
  const picked = useRef(false);
  const [md, setMd] = useState<RKMatchdayResponse | null>(null);
  const [mdLoading, setMdLoading] = useState(false);
  const [mdError, setMdError] = useState(false);
  const [league, setLeague] = useState("");

  // The strip; refreshed every minute while games are live
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const load = () => fetchRKStrip(authFetch, sport).then(s => {
      if (!alive) return;
      setStrip(s);
      if (!picked.current) {
        picked.current = true;
        const t = s.days.find(d => d.date === s.today);
        const next = s.days.find(d => d.date > s.today && d.total > 0);
        setDay(t && t.total > 0 ? s.today : next?.date ?? s.today);
      }
      timer = setTimeout(load, s.days.some(d => d.live > 0) ? 60_000 : 5 * 60_000);
    }).catch(() => { if (alive) timer = setTimeout(load, 60_000); });
    load();
    return () => { alive = false; clearTimeout(timer); };
  }, [authFetch, sport]);

  const today = strip?.today ?? localDateStr();
  const isPast = day < today;
  const isToday = day === today;

  // Today and past days: the stored games (live every minute while any are)
  useEffect(() => {
    if (day > today) { setMd(null); return; }
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const ctrl = new AbortController();
    const load = (first: boolean) => {
      if (first) { setMdLoading(true); setMdError(false); }
      fetchRKMatchday(authFetch, sport, day, ctrl.signal)
        .then(d => {
          if (!alive) return;
          setMd(d);
          const under = d.matches.some(m => m.status === "live" || (m.status === "scheduled" && Date.parse(`${m.date}T${m.time}:00Z`) < Date.now()));
          if (under) timer = setTimeout(() => load(false), 45_000);
        })
        .catch(() => { if (alive && first) setMdError(true); })
        .finally(() => { if (alive && first) setMdLoading(false); });
    };
    setMd(null);
    load(true);
    return () => { alive = false; ctrl.abort(); clearTimeout(timer); };
  }, [day, today, authFetch, sport]);

  // Today: matches that have started show as rows (live / finished), the rest as cards
  const underway = (m: RKMatchdayMatch) => m.status !== "scheduled" || Date.parse(`${m.date}T${m.time}:00Z`) <= Date.now();
  const played = isToday ? (md?.matches ?? []).filter(underway) : [];
  const started = new Set(played.map(m => m.id));
  const dayPreds = preds.filter(p => p.date === day && !started.has(p.sportybet_event_id));
  const counts = new Map<string, number>();
  dayPreds.forEach(p => counts.set(p.league, (counts.get(p.league) ?? 0) + 1));
  const leagues = Array.from(counts.entries()).sort((a, b) => b[1] - a[1]);
  const shown = league && counts.has(league) ? dayPreds.filter(p => p.league === league) : dayPreds;
  const nextDay = strip?.days.find(d => d.date > day && d.total > 0)?.date;
  const goTo = (d: string) => { picked.current = true; setDay(d); };

  return (
    <div className="space-y-4">
      {strip ? (
        <DateStrip days={strip.days} today={strip.today} selected={day} onSelect={goTo} />
      ) : (
        <div className="flex gap-1.5 overflow-hidden">{Array.from({ length: 8 }).map((_, i) => <div key={i} className="skeleton !rounded-xl w-[58px] h-[62px] shrink-0" />)}</div>
      )}

      {isPast ? (
        mdLoading ? (
          <div className="card flex items-center justify-center gap-2 py-16 text-sm text-n-400"><Loader2 size={16} className="animate-spin" /> Loading results…</div>
        ) : mdError ? (
          <Empty icon={<AlertTriangle size={20} className="text-danger" />} title="Couldn't load this day"
            body="The server may be waking up. Try again in a moment." />
        ) : !md?.matches.length ? (
          <Empty icon={<SearchX size={20} />} title={`No predictions for ${dayLabel(day)}`}
            body={`We didn't keep ${w.name} predictions for this day.`} />
        ) : (
          <div className="space-y-4">
            <DaySummaryBar summary={md.summary} label={dayLabel(day)} items={summaryItems(md.summary, sport)} link={false} />
            <RacketMatchList matches={md.matches} sport={sport} />
          </div>
        )
      ) : <>
        {played.length > 0 && (
          <section className="space-y-3">
            <Heading title="Live & finished" n={played.length} />
            {md && md.summary.finished > 0 && (
              <DaySummaryBar summary={md.summary} label="Today so far" items={summaryItems(md.summary, sport)} link={false} />
            )}
            <RacketMatchList matches={played} sport={sport} />
          </section>
        )}
        {played.length > 0 && dayPreds.length > 0 && <div className="pt-2"><Heading title="Still to play" n={dayPreds.length} /></div>}

        {loading ? (
          <div className="card flex items-center justify-center gap-2 py-16 text-sm text-n-400"><Loader2 size={16} className="animate-spin" /> Loading matches…</div>
        ) : dayPreds.length === 0 ? (
          played.length > 0 && !nextDay ? null : (
            <Empty icon={<CalendarDays size={20} />}
              title={isToday ? (played.length ? "That's all for today" : "No matches today") : `No matches for ${dayLabel(day)} yet`}
              body={isToday ? "Our next predictions are a tap away." : "Matches appear once SportyBet lists them, up to 14 days ahead."}
              action={nextDay ? (
                <button onClick={() => goTo(nextDay)} className="btn-primary">Go to {dayLabel(nextDay)} <ArrowRight size={14} /></button>
              ) : undefined} />
          )
        ) : <>
          {leagues.length > 1 && (
            // Scrolls sideways with a visible scrollbar, like football's league tabs
            <div className="overflow-x-auto pb-1 -mx-4 px-4 sm:mx-0 sm:px-0">
              <div className="flex gap-1.5 min-w-max">
                <button onClick={() => setLeague("")} className={clsx("chip", !league ? "chip-active" : "chip-idle")}>
                  <span>{w.emoji}</span><span>All tournaments</span><Count n={dayPreds.length} active={!league} />
                </button>
                {leagues.map(([l, n]) => {
                  const p = dayPreds.find(x => x.league === l)!;
                  return (
                    <button key={l} onClick={() => setLeague(l)} className={clsx("chip", league === l ? "chip-active" : "chip-idle")}>
                      <CompetitionBadge name={p.league_name} fallbackEmoji={p.flag} size={14} />
                      <span>{p.league_name}</span><Count n={n} active={league === l} />
                    </button>
                  );
                })}
              </div>
            </div>
          )}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {shown.map(p => (
              <SportCard key={p.sportybet_event_id || `${p.home}-${p.date}`} prediction={p as unknown as SportPrediction}
                onClick={() => onOpen(p)} />
            ))}
          </div>
        </>}
      </>}
    </div>
  );
}
