"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import { BarChart3, History, Loader2, Swords } from "lucide-react";
import { FormChips } from "./BasketballFacts";
import { useAuthedFetch } from "@/lib/useAuthedFetch";
import { fetchTennisFacts, type TAverages, type TFacts, type TFormRow } from "@/lib/tennis";

const shortDate = (iso: string) =>
  new Date(`${iso}T12:00:00Z`).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "2-digit" });

function Title({ icon, children, right }: { icon: React.ReactNode; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 mb-3">
      <h3 className="font-display font-bold text-base uppercase tracking-[0.06em] text-n-0 inline-flex items-center gap-2">
        <span className="text-accent">{icon}</span>{children}
      </h3>
      {right && <div className="ml-auto">{right}</div>}
    </div>
  );
}

function FormList({ name, rows }: { name: string; rows: TFormRow[] }) {
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-2 pb-2 border-b border-n-800">
        <span className="text-xs font-bold text-n-0 truncate">{name}</span>
        {rows.length > 0 && <span className="ml-auto"><FormChips form={rows.map(r => r.outcome).reverse().join("")} /></span>}
      </div>
      {rows.length === 0 ? (
        <p className="text-xs text-n-500 py-3">No recent results on record.</p>
      ) : (
        <ul className="divide-y divide-n-800/70">
          {rows.map((r, i) => (
            <li key={i} className="flex items-center gap-2.5 py-2">
              <span className={clsx("font-display font-bold text-[11px] w-[18px] h-[18px] rounded flex items-center justify-center shrink-0",
                r.outcome === "W" ? "bg-brand-400 text-ink" : "bg-rose-500 text-white")}>{r.outcome}</span>
              <div className="min-w-0 flex-1">
                <p className="text-[13px] text-n-200 truncate"><span className="text-n-500 text-[11px] font-bold mr-1.5">vs</span>{r.opponent}</p>
                <p className="text-[11px] text-n-500 truncate">{shortDate(r.date)} · {r.tournament} · {r.surface}</p>
              </div>
              <span className="text-right shrink-0">
                <span className="block font-mono font-bold text-sm text-n-0 tnum">{r.sets[0]}-{r.sets[1]}</span>
                <span className="block font-mono text-[10px] text-n-500 tnum">{r.score}{r.retired ? " ret." : ""}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

type Row = { label: string; get: (a: TAverages) => number | null | undefined; pct?: boolean; better?: "higher" | "lower"; dp?: number };

function Averages({ f, home, away }: { f: TFacts; home: string; away: string }) {
  const h = f.averages.home, a = f.averages.away;
  if (!h && !a) return null;
  const groups: { title: string; rows: Row[] }[] = [
    { title: "Results", rows: [
      { label: "Won", get: x => x.won, pct: true },
      { label: `Won on ${f.surface.toLowerCase()}`, get: x => x.surface?.won, pct: true },
      { label: "Straight-sets wins", get: x => x.straight_sets_wins, pct: true },
      { label: "Went to a deciding set", get: x => x.deciding_set.rate, pct: true },
      { label: "Won the deciding set", get: x => x.deciding_set.won, pct: true },
    ] },
    { title: "Sets and games (per match)", rows: [
      { label: "Sets won", get: x => x.sets.for, dp: 2 },
      { label: "Sets lost", get: x => x.sets.against, dp: 2, better: "lower" },
      { label: "Games won", get: x => x.games.for },
      { label: "Games lost", get: x => x.games.against, better: "lower" },
      { label: "Total games", get: x => x.games.total },
      { label: "Matches with a tiebreak", get: x => x.tiebreak_matches, pct: true },
    ] },
  ];
  const show = (t: TAverages | null, r: Row) => {
    const v = t ? r.get(t) : null;
    if (v === null || v === undefined) return "—";
    return r.pct ? `${Math.round(v * 100)}%` : v.toFixed(r.dp ?? 1);
  };
  const gs = groups.map(g => ({ ...g, rows: g.rows.filter(r => (h && r.get(h) != null) || (a && r.get(a) != null)) }))
    .filter(g => g.rows.length);
  return (
    <div className="card p-4">
      <Title icon={<BarChart3 size={15} />} right={<span className="text-[11px] text-n-500 tnum">last {f.averages.n}</span>}>
        Player numbers
      </Title>
      <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-x-3 pb-2 border-b border-n-800">
        <span className="text-xs font-bold text-n-0 truncate">{home}</span><span />
        <span className="text-xs font-bold text-n-0 truncate text-right">{away}</span>
      </div>
      {gs.map(g => (
        <div key={g.title} className="pt-2.5">
          <p className="eyebrow text-center mb-1">{g.title}</p>
          <ul>
            {g.rows.map(r => {
              const hv = h ? r.get(h) : null, av = a ? r.get(a) : null;
              const up = r.better !== "lower";
              const hiH = hv != null && av != null && (up ? hv > av : hv < av);
              const hiA = hv != null && av != null && (up ? av > hv : av < hv);
              return (
                <li key={r.label} className="grid grid-cols-[1fr_auto_1fr] items-center gap-x-3 py-1">
                  <span className={clsx("font-mono tnum text-sm", hiH ? "text-n-0 font-bold" : "text-n-300")}>{show(h, r)}</span>
                  <span className="text-[12px] text-n-400 text-center">{r.label}</span>
                  <span className={clsx("font-mono tnum text-sm text-right", hiA ? "text-n-0 font-bold" : "text-n-300")}>{show(a, r)}</span>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
      <p className="text-[11px] text-n-500 mt-3">
        Over each player&apos;s last {Math.max(h?.played ?? 0, a?.played ?? 0) || f.averages.n} singles matches on SportyBet
        {h?.surface || a?.surface ? `; on ${f.surface.toLowerCase()}: ${[h?.surface && `${home} ${h.surface.played}`, a?.surface && `${away} ${a.surface.played}`].filter(Boolean).join(", ")}` : ""}.
        Retirements count as played.
      </p>
    </div>
  );
}

function HeadToHead({ f, home, away }: { f: TFacts; home: string; away: string }) {
  const s = f.summary.h2h;
  if (!f.h2h.length || !s) return null;
  return (
    <div className="card p-4">
      <Title icon={<Swords size={15} />} right={<span className="text-[11px] text-n-500 tnum">last {f.h2h.length}</span>}>Head to head</Title>
      <div className="flex h-2 rounded-full overflow-hidden gap-0.5 bg-n-800">
        {s.won > 0 && <div className="bg-brand-400" style={{ flexGrow: s.won }} />}
        {s.lost > 0 && <div className="bg-rose-500" style={{ flexGrow: s.lost }} />}
      </div>
      <div className="flex justify-between text-xs mt-2 gap-2">
        <span className="text-n-400 truncate"><span className="font-display font-bold text-lg text-n-0 tnum mr-1">{s.won}</span>{home}</span>
        <span className="text-n-400 truncate text-right"><span className="font-display font-bold text-lg text-n-0 tnum mr-1">{s.lost}</span>{away}</span>
      </div>
      <ul className="divide-y divide-n-800/70 mt-2">
        {f.h2h.map((m, i) => (
          <li key={i} className="grid grid-cols-[1fr_auto_1fr] items-center gap-2 py-2">
            <span className={clsx("text-[13px] truncate text-right", m.sets[0] > m.sets[1] ? "text-n-0 font-semibold" : "text-n-400")}>{m.home}</span>
            <span className="font-mono font-bold text-sm text-n-0 tnum px-2 py-0.5 rounded bg-n-800/70">{m.sets[0]}-{m.sets[1]}</span>
            <span className={clsx("text-[13px] truncate", m.sets[1] > m.sets[0] ? "text-n-0 font-semibold" : "text-n-400")}>{m.away}</span>
            <span className="col-span-3 text-center text-[11px] text-n-500 -mt-1 tnum">
              {m.score}{m.retired ? " ret." : ""} · {shortDate(m.date)} · {m.tournament} · {m.surface}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A tennis match's form, player numbers and head to head, like football's and basketball's. */
export function TennisFacts({ m }: { m: { home: string; away: string; date: string; time?: string; league?: string } }) {
  const authFetch = useAuthedFetch();
  const [f, setF] = useState<TFacts | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    const ctrl = new AbortController();
    fetchTennisFacts(authFetch, m, ctrl.signal).then(setF).catch(e => { if (e?.name !== "AbortError") setError(true); });
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [m.home, m.away, m.date, m.time, m.league, authFetch]);

  if (error) return <p className="text-center text-sm text-n-400 py-6">Couldn&apos;t load this match&apos;s form. Try again in a moment.</p>;
  if (!f) return <div className="flex items-center justify-center gap-2 py-8 text-n-400 text-sm"><Loader2 size={16} className="animate-spin" /> Loading form…</div>;
  if (!f.home.length && !f.away.length) {
    return (
      <p className="text-center text-sm text-n-400 py-6">
        No results on record for these players yet{f.results_days ? "" : ": we've only just started collecting tennis results"}.
      </p>
    );
  }
  return (
    <div className="space-y-4">
      <div className="card p-4">
        <Title icon={<History size={15} />}>Last 5 matches</Title>
        <div className="grid sm:grid-cols-2 gap-x-6 gap-y-4">
          <FormList name={m.home} rows={f.home} />
          <FormList name={m.away} rows={f.away} />
        </div>
        <p className="text-[11px] text-n-500 mt-3">Sets and games from each player&apos;s side, newest first.</p>
      </div>
      <Averages f={f} home={m.home} away={m.away} />
      <HeadToHead f={f} home={m.home} away={m.away} />
    </div>
  );
}
