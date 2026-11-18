"use client";

import { useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { Info, Loader2 } from "lucide-react";
import { AppShell } from "@/components/shell/AppShell";
import { PageHeader } from "@/components/shell/PageHeader";
import { FeatureGate, Locked, Unavailable } from "@/components/FeatureGate";
import { useAuthedFetch } from "@/lib/useAuthedFetch";
import { API } from "@/lib/matchday";

// SportyBet's weekly jackpot (backend jackpot.py, refreshed Thursday and
// Friday): each match's chances (our model's where we have it, else
// SportyBet's odds without the margin), the best ticket for each number of
// lines, each prize's chance, and how past weeks went.

type Outcome = "1" | "X" | "2";
interface Match {
  index: number; home: string; away: string; kickoff?: number; odds: (number | null)[];
  p: number[]; source: "model" | "odds" | "none"; pick: Outcome;
}
interface Ticket { lines: number; picks: string[]; prizes: Record<string, number>; one_in: number | null }
interface Current {
  periodNumber: string; deadline?: number; maxWinnings?: number; currency?: string; games: number;
  matches: Match[]; tickets: Ticket[]; model_matches: number;
}
interface Week { period: string; results: string | null; favourites_right: number | null; ours_right: number | null }
interface JackpotResponse {
  current: Current | null; banner?: { firstPrizeCorrect?: number; secondPrizeCorrect?: number; thirdPrizeCorrect?: number };
  at: string | null;
  record: { weeks: Week[]; favourites_avg: number | null; favourites_best: number | null; ours_avg: number | null;
    ours_weeks: number; weeks_scored: number };
}

const OUTCOMES: Outcome[] = ["1", "X", "2"];
const PERKS = [
  "This week's 12 matches with our chance for home, draw and away",
  "The best ticket for 1 to 192 lines: extra outcomes where they help most",
  "Each ticket's chance of 12, 11 and 10 right",
];

const pct = (x: number) =>
  x >= 0.1 ? `${(x * 100).toFixed(0)}%` : x >= 0.01 ? `${(x * 100).toFixed(1)}%` : `${(x * 100).toFixed(2)}%`;
const naira = (n?: number, cur = "NGN") =>
  n == null ? "" : `${cur === "NGN" ? "₦" : `${cur} `}${n.toLocaleString("en-US")}`;
const when = (ms?: number) =>
  ms ? new Date(ms).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";

function left(ms?: number): string {
  if (!ms) return "";
  const s = Math.max(0, ms - Date.now()) / 1000;
  if (s <= 0) return "closed";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h left` : h ? `${h}h ${m}m left` : `${m}m left`;
}

function Jackpot() {
  const authFetch = useAuthedFetch();
  const [data, setData] = useState<JackpotResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lines, setLines] = useState(1);

  useEffect(() => {
    authFetch(`${API}/api/jackpot`).then(async r => {
      if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail || "failed");
      return r.json() as Promise<JackpotResponse>;
    }).then(setData).catch(e => setError(e.message === "failed" ? "Couldn't load the jackpot. Try again in a moment." : e.message));
  }, [authFetch]);

  const cur = data?.current;
  const ticket = useMemo(() => cur?.tickets.find(t => t.lines === lines) ?? cur?.tickets[0], [cur, lines]);

  if (error === "feature_off") return <Unavailable title="Jackpot" />;
  if (error && /_required$|^Sign in/.test(error)) return <Locked feature="jackpot" title="Jackpot" perks={PERKS} />;
  if (error) return <p className="card p-6 text-sm text-n-400">{error}</p>;
  if (!data) {
    return <div className="card flex items-center justify-center gap-2 py-16 text-sm text-n-400"><Loader2 size={15} className="animate-spin" /> Loading…</div>;
  }
  if (!cur || !ticket) return <p className="card p-6 text-sm text-n-400">This week&apos;s jackpot isn&apos;t in yet. It&apos;s read on Thursday and Friday.</p>;

  const prizes = [data.banner?.firstPrizeCorrect ?? 12, data.banner?.secondPrizeCorrect ?? 11, data.banner?.thirdPrizeCorrect ?? 10];
  const rec = data.record;

  return (
    <div className="space-y-6">
      <section className="card p-5 grid gap-4 sm:grid-cols-3">
        <div>
          <p className="eyebrow">Round {cur.periodNumber}</p>
          <p className="heading text-2xl mt-1 tnum">{naira(cur.maxWinnings, cur.currency)}</p>
          <p className="text-xs text-n-400 mt-1">top prize · {cur.games} matches</p>
        </div>
        <div>
          <p className="eyebrow">Closes</p>
          <p className="text-base text-n-100 mt-1">{when(cur.deadline)}</p>
          <p className="text-xs text-n-400 mt-1">{left(cur.deadline)}</p>
        </div>
        <div>
          <p className="eyebrow">Our chances</p>
          <p className="text-base text-n-100 mt-1 tnum">{cur.model_matches} of {cur.games} matches</p>
          <p className="text-xs text-n-400 mt-1">the rest from SportyBet&apos;s odds without its margin</p>
        </div>
      </section>

      <section className="card p-5 space-y-4">
        <div>
          <p className="eyebrow">Lines on the ticket</p>
          <div className="flex flex-wrap gap-1.5 mt-2" role="group" aria-label="Lines on the ticket">
            {cur.tickets.map(t => (
              <button key={t.lines} type="button" aria-pressed={t.lines === ticket.lines} onClick={() => setLines(t.lines)}
                className={clsx("chip tnum", t.lines === ticket.lines ? "chip-active" : "chip-idle")}>
                {t.lines}
              </button>
            ))}
          </div>
          <p className="text-xs text-n-400 mt-2 text-pretty">
            Each extra outcome doubles or triples the lines, so it goes on the closest matches first.
          </p>
        </div>
        <div className="grid grid-cols-3 gap-3">
          {prizes.map((k, i) => (
            <div key={k} className="rounded-xl bg-surface-sunken p-3">
              <p className="text-xs text-n-400">{i === 0 ? "All" : ""} {k} right</p>
              <p className="heading text-lg mt-1 tnum">{pct(ticket.prizes[String(k)] ?? 0)}</p>
              {i === 0 && ticket.one_in && <p className="text-[11px] text-n-500 tnum">1 in {ticket.one_in.toLocaleString("en-US")}</p>}
            </div>
          ))}
        </div>
      </section>

      <section className="card divide-y divide-n-800/60">
        {cur.matches.map((m, i) => {
          const covered = ticket.picks[i] ?? m.pick;
          return (
            <div key={m.index} className="p-4 space-y-2">
              <div className="flex items-baseline justify-between gap-3">
                <p className="text-sm text-n-100 min-w-0 truncate">
                  <span className="text-n-500 tnum mr-2">{m.index}</span>{m.home} v {m.away}
                </p>
                <p className="text-[11px] text-n-500 shrink-0">{when(m.kickoff)}</p>
              </div>
              <div className="grid grid-cols-3 gap-2">
                {OUTCOMES.map((o, k) => (
                  <div key={o} className={clsx("rounded-lg px-2.5 py-2 text-center",
                    covered.includes(o) ? "bg-accent/15 text-n-0 ring-1 ring-accent/40" : "bg-surface-sunken text-n-300")}>
                    <p className="text-[11px] text-n-400">{o === "1" ? "Home" : o === "X" ? "Draw" : "Away"}</p>
                    <p className="text-sm font-semibold tnum">{pct(m.p[k])}</p>
                    {m.odds[k] != null && <p className="text-[10.5px] text-n-500 tnum">{m.odds[k]}</p>}
                  </div>
                ))}
              </div>
              <p className="text-[11px] text-n-500">{m.source === "model" ? "Our model" : "SportyBet's odds"}</p>
            </div>
          );
        })}
      </section>

      <section className="card p-5 space-y-3">
        <p className="eyebrow">How past weeks went</p>
        {rec.weeks_scored ? (
          <>
            <p className="text-sm text-n-200 text-pretty">
              Picking every favourite got <span className="font-semibold tnum">{rec.favourites_avg}</span> of 12 right on
              average over {rec.weeks_scored} weeks (best {rec.favourites_best}).
              {rec.ours_weeks ? <> Our picks: <span className="font-semibold tnum">{rec.ours_avg}</span> over {rec.ours_weeks} week{rec.ours_weeks === 1 ? "" : "s"}.</>
                : <> Our own picks are recorded from this week on.</>}
            </p>
            <ul className="text-xs text-n-300 space-y-1">
              {rec.weeks.slice(0, 12).map(w => (
                <li key={w.period} className="flex justify-between gap-3 tnum">
                  <span>{w.period.slice(0, 4)}-{w.period.slice(4, 6)}-{w.period.slice(6)}</span>
                  <span className="font-mono tracking-wider text-n-400">{w.results}</span>
                  <span>favourites {w.favourites_right ?? "—"}{w.ours_right != null ? ` · ours ${w.ours_right}` : ""}</span>
                </li>
              ))}
            </ul>
          </>
        ) : <p className="text-sm text-n-400">No past weeks scored yet.</p>}
      </section>

      <p className="flex gap-2 text-xs text-n-400 text-pretty">
        <Info size={14} className="shrink-0 mt-0.5" aria-hidden="true" />
        SportyBet picks closely matched games: no past week&apos;s favourites reached 10 right. These are the best tickets
        for the lines you buy, not a likely win. Stake only what you&apos;re happy to lose.
      </p>
    </div>
  );
}

export default function JackpotPage() {
  return (
    <AppShell>
      <div className="space-y-6 animate-fade-in">
        <PageHeader eyebrow="SportyBet weekly" title="Jackpot"
          description="This week's 12 matches with our chance of each result, and the best ticket for the number of lines you buy." />
        <FeatureGate feature="jackpot" title="Jackpot" perks={PERKS}>
          <Jackpot />
        </FeatureGate>
      </div>
    </AppShell>
  );
}
