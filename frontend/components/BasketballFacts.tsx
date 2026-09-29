"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import { BarChart3, History, Loader2, Swords } from "lucide-react";
import { Crest } from "./BasketballCard";
import { useAuthedFetch } from "@/lib/useAuthedFetch";
import { fetchBBFacts, type BasketballPrediction, type BBAverages, type BBFacts, type BBFormRow } from "@/lib/basketball";

const shortDate = (iso: string) =>
  new Date(`${iso}T12:00:00Z`).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "2-digit" });

/** Each team's last results as coloured chips, oldest to newest. */
export function FormChips({ form, size = 18 }: { form?: string; size?: number }) {
  if (!form) return null;
  return (
    <span className="inline-flex gap-0.5" aria-label={`Form ${form}`}>
      {form.split("").map((r, i) => (
        <span key={i} style={{ width: size, height: size, fontSize: size * 0.6 }}
          className={clsx("font-display font-bold rounded flex items-center justify-center",
            r === "W" ? "bg-brand-400 text-ink" : "bg-rose-500 text-white")}>{r}</span>
      ))}
    </span>
  );
}

function Title({ icon, children, right }: { icon: React.ReactNode; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 mb-3">
      <h3 className="heading text-[15px] inline-flex items-center gap-2">
        <span className="text-accent">{icon}</span>{children}
      </h3>
      {right && <div className="ml-auto">{right}</div>}
    </div>
  );
}

function FormList({ name, logo, rows }: { name: string; logo?: string | null; rows: BBFormRow[] }) {
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-2 pb-2 border-b border-n-800">
        <Crest src={logo} name={name} size={20} />
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
                <p className="text-[13px] text-n-200 truncate">
                  <span className="text-n-500 text-[11px] font-bold mr-1.5">{r.venue === "H" ? "vs" : "at"}</span>{r.opponent}
                </p>
                <p className="text-[11px] text-n-500 truncate">{shortDate(r.date)}{r.comp && <> · {r.comp}</>}</p>
              </div>
              <span className="font-mono font-bold text-sm text-n-0 tnum shrink-0">
                {r.for}-{r.against}{r.ot && <span className="text-[10px] text-n-500 ml-1">OT</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

type Row = { label: string; get: (a: BBAverages) => number | null | undefined; pct?: boolean; better?: "higher" | "lower" };
const pts = (x: number | null | undefined) => x;

function groups(line?: number | null, hcpHome?: number | null): { title: string; rows: Row[] }[] {
  return [
    { title: "Points", rows: [
      { label: "Scored", get: a => a.points.for },
      { label: "Conceded", get: a => a.points.against, better: "lower" },
      { label: "Game total", get: a => a.points.total },
      { label: "Winning margin", get: a => a.points.margin },
      { label: "At home", get: a => a.home_points },
      { label: "Away", get: a => a.away_points },
    ] },
    { title: "Results", rows: [
      { label: "Won", get: a => a.results.won, pct: true },
      { label: "Went to overtime", get: a => a.overtime, pct: true, better: "lower" },
      ...(line != null ? [{ label: `Over ${line}`, get: (a: BBAverages) => a.over_line?.rate, pct: true } as Row] : []),
      ...(hcpHome != null ? [{ label: "Covered tonight's line", get: (a: BBAverages) => a.cover?.rate, pct: true } as Row] : []),
    ] },
    { title: "Halves", rows: [
      { label: "1st half scored", get: a => pts(a.halves.first_for) },
      { label: "1st half conceded", get: a => pts(a.halves.first_against), better: "lower" },
      { label: "2nd half scored", get: a => pts(a.halves.second_for) },
      { label: "2nd half conceded", get: a => pts(a.halves.second_against), better: "lower" },
    ] },
    { title: "Quarters (scored)", rows: [0, 1, 2, 3].map(i => ({ label: `Q${i + 1}`, get: (a: BBAverages) => a.quarters.for[i] })) },
    { title: "Quarters (conceded)", rows: [0, 1, 2, 3].map(i => ({ label: `Q${i + 1}`, get: (a: BBAverages) => a.quarters.against[i], better: "lower" as const })) },
  ];
}

function Averages({ f, p }: { f: BBFacts; p: BasketballPrediction }) {
  const h = f.averages.home, a = f.averages.away;
  if (!h && !a) return null;
  const show = (t: BBAverages | null, r: Row) => {
    const v = t ? r.get(t) : null;
    if (v === null || v === undefined) return "—";
    return r.pct ? `${Math.round(v * 100)}%` : v.toFixed(1);
  };
  const gs = groups(p.total_line, p.handicap_line)
    .map(g => ({ ...g, rows: g.rows.filter(r => (h && r.get(h) != null) || (a && r.get(a) != null)) }))
    .filter(g => g.rows.length);
  const qN = Math.max(h?.quarters.matches ?? 0, a?.quarters.matches ?? 0);
  return (
    <div className="card p-4">
      <Title icon={<BarChart3 size={15} />} right={<span className="text-[11px] text-n-500 tnum">last {f.averages.n}</span>}>
        Team averages
      </Title>
      <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-x-3 pb-2 border-b border-n-800">
        <span className="flex items-center gap-2 min-w-0"><Crest src={p.home_logo} name={p.home} size={18} /><span className="text-xs font-bold text-n-0 truncate">{p.home}</span></span>
        <span />
        <span className="flex items-center gap-2 min-w-0 justify-end"><span className="text-xs font-bold text-n-0 truncate text-right">{p.away}</span><Crest src={p.away_logo} name={p.away} size={18} /></span>
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
        Per game over each team&apos;s last {h?.played ?? a?.played ?? f.averages.n} games in all competitions (overtime included).
        {qN > 0 && ` Halves and quarters from the ${qN} games with quarter scores.`}
        {p.handicap_line != null && ` "Covered" is tonight's line for each side: ${p.home} ${p.handicap_line > 0 ? "+" : ""}${p.handicap_line}.`}
      </p>
    </div>
  );
}

function HeadToHead({ f, p }: { f: BBFacts; p: BasketballPrediction }) {
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
        <span className="text-n-400 truncate"><span className="font-display font-bold text-lg text-n-0 tnum mr-1">{s.won}</span>{p.home} wins</span>
        <span className="text-n-400 truncate text-right"><span className="font-display font-bold text-lg text-n-0 tnum mr-1">{s.lost}</span>{p.away} wins</span>
      </div>
      <p className="text-[11px] text-n-500 mt-1 tnum">
        Average total {s.avg_total} · average margin {s.avg_margin > 0 ? "+" : ""}{s.avg_margin} for {p.home}
      </p>
      <ul className="divide-y divide-n-800/70 mt-2">
        {f.h2h.map((m, i) => (
          <li key={i} className="grid grid-cols-[1fr_auto_1fr] items-center gap-2 py-2">
            <span className={clsx("text-[13px] truncate text-right", m.hs > m.as ? "text-n-0 font-semibold" : "text-n-400")}>{m.home}</span>
            <span className="font-mono font-bold text-sm text-n-0 tnum px-2 py-0.5 rounded bg-n-800/70">{m.hs}-{m.as}{m.ot ? " OT" : ""}</span>
            <span className={clsx("text-[13px] truncate", m.as > m.hs ? "text-n-0 font-semibold" : "text-n-400")}>{m.away}</span>
            <span className="col-span-3 text-center text-[11px] text-n-500 -mt-1">{shortDate(m.date)}{m.comp && <> · {m.comp}</>}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A basketball match's form, averages and meetings, like football's match page. */
export function BasketballFacts({ p }: { p: BasketballPrediction }) {
  const authFetch = useAuthedFetch();
  const [f, setF] = useState<BBFacts | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    const ctrl = new AbortController();
    fetchBBFacts(authFetch, p.sportybet_event_id, ctrl.signal).then(setF).catch(e => { if (e?.name !== "AbortError") setError(true); });
    return () => ctrl.abort();
  }, [p.sportybet_event_id, authFetch]);

  if (error) return <p className="text-center text-sm text-n-400 py-6">Couldn&apos;t load this match&apos;s form. Try again in a moment.</p>;
  if (!f) return <div className="flex items-center justify-center gap-2 py-8 text-n-400 text-sm"><Loader2 size={16} className="animate-spin" /> Loading form…</div>;
  const none = !f.home.length && !f.away.length;
  if (none) return <p className="text-center text-sm text-n-400 py-6">No results on record for these teams yet.</p>;
  return (
    <div className="space-y-4">
      <div className="card p-4">
        <Title icon={<History size={15} />}>Last 5 games</Title>
        <div className="grid sm:grid-cols-2 gap-x-6 gap-y-4">
          <FormList name={p.home} logo={p.home_logo} rows={f.home} />
          <FormList name={p.away} logo={p.away_logo} rows={f.away} />
        </div>
        <p className="text-[11px] text-n-500 mt-3">Scores from each team&apos;s side, newest first.</p>
      </div>
      <Averages f={f} p={p} />
      <HeadToHead f={f} p={p} />
    </div>
  );
}
