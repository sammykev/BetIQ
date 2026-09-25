"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import clsx from "clsx";
import { useUser } from "@clerk/nextjs";
import { AlertTriangle, ArrowRight, Loader2, Scale, Target, Ticket } from "lucide-react";
import { AppShell } from "@/components/shell/AppShell";
import { PageHeader } from "@/components/shell/PageHeader";
import { CompetitionBadge } from "@/components/CompetitionBadge";
import { ColumnChart } from "@/components/admin/charts";
import { useAuthedFetch } from "@/lib/useAuthedFetch";
import { API, MARKET_LABELS, fetchAccuracy, type Accuracy, type TicketSummary } from "@/lib/matchday";

// The model's track record: every pick we published, graded against the
// final score (backend matchday.py). "We said" is the average probability we
// gave those picks; "it happened" is how often they won. When the two are
// close the probabilities can be trusted as they're shown.

const PERIODS = [7, 30, 90];
const MARKET_ORDER = ["tip", "goals", "favourite", "ou25", "btts", "corners", "bookings"];
const pct = (x?: number | null) => (typeof x === "number" ? `${Math.round(x * 100)}%` : "—");

function Tile({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="card px-4 py-3 min-w-0">
      <p className="eyebrow truncate">{label}</p>
      <p className={clsx("font-display font-extrabold text-3xl sm:text-4xl leading-none mt-1.5 tnum", tone ?? "text-n-0")}>{value}</p>
      {sub && <p className="text-[11px] text-n-500 mt-1.5">{sub}</p>}
    </div>
  );
}

/** How far the hit rate is from what we said: within 5 points reads as honest. */
function Gap({ said, happened }: { said: number | null; happened: number | null }) {
  if (said === null || happened === null) return <span className="text-n-500">—</span>;
  const d = Math.round((happened - said) * 100);
  return (
    <span className={clsx("tnum font-semibold", Math.abs(d) <= 5 ? "text-accent" : d > 0 ? "text-info" : "text-warn")}>
      {d > 0 ? "+" : ""}{d} pts
    </span>
  );
}

function MyTickets() {
  const { user } = useUser();
  const authFetch = useAuthedFetch();
  const [s, setS] = useState<TicketSummary | null>(null);
  useEffect(() => {
    if (!user?.id) return;
    authFetch(`${API}/api/user/tickets?uid=${encodeURIComponent(user.id)}`)
      .then(r => (r.ok ? r.json() : null)).then(d => setS(d?.summary ?? null)).catch(() => {});
  }, [user?.id, authFetch]);
  if (!user) return null;
  return (
    <section className="card p-4 sm:p-5 space-y-3">
      <div className="flex items-center gap-2">
        <Ticket size={16} className="text-accent" />
        <h2 className="font-semibold text-n-0">Your booking codes</h2>
        <Link href="/dashboard?tab=codes" className="ml-auto text-xs font-semibold text-accent inline-flex items-center gap-1 hover:underline">
          All tickets <ArrowRight size={12} />
        </Link>
      </div>
      {!s || s.tickets === 0 ? (
        <p className="text-sm text-n-400">Codes you generate on BetIQ while signed in are tracked here and settled leg by leg as results come in.</p>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <Tile label="Codes" value={`${s.tickets}`} sub={`${s.pending} still open`} />
          <Tile label="Tickets won" value={`${s.won}/${s.won + s.lost}`} sub={s.hit_rate !== null ? `${pct(s.hit_rate)} of settled` : "none settled yet"} />
          <Tile label="Legs won" value={`${s.legs_won}/${s.legs_won + s.legs_lost}`} sub={pct(s.leg_hit_rate)} />
          <Tile label="Tickets lost" value={`${s.lost}`} />
        </div>
      )}
    </section>
  );
}

export default function TrackRecordPage() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<Accuracy | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    const ctrl = new AbortController();
    setLoading(true); setError(false);
    fetchAccuracy(days, ctrl.signal).then(setData).catch(e => { if (e?.name !== "AbortError") setError(true); })
      .finally(() => setLoading(false));
    return () => ctrl.abort();
  }, [days]);

  const m = data?.markets ?? {};
  const vs = data?.brier.vs_bookmaker;
  const markets = MARKET_ORDER.filter(k => m[k]?.n);

  return (
    <AppShell>
      <div className="space-y-6 animate-fade-in">
        <PageHeader eyebrow="Model accountability" title="Track record"
          description="Every prediction we published, graded against the final score. Browse day by day from the date strip on the home page."
          right={
            <div className="flex gap-1.5" role="group" aria-label="Period">
              {PERIODS.map(p => (
                <button key={p} onClick={() => setDays(p)} aria-pressed={days === p}
                  className={clsx("chip", days === p ? "chip-active" : "chip-idle")}>{p} days</button>
              ))}
            </div>
          } />

        {loading && !data ? (
          <div className="card flex items-center justify-center gap-2 py-16 text-sm text-n-400"><Loader2 size={16} className="animate-spin" /> Loading the record…</div>
        ) : error ? (
          <div className="card p-6 text-sm text-n-400 flex items-center gap-2"><AlertTriangle size={16} className="text-danger" /> Couldn&apos;t load the track record. Try again shortly.</div>
        ) : !data || data.matches === 0 ? (
          <div className="card p-6 space-y-2">
            <p className="font-semibold text-n-0">No graded matches in the last {days} days yet</p>
            <p className="text-sm text-n-400">Matches are graded automatically once they finish. Check back after the next round of fixtures.</p>
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
              <Tile label="Our tips" value={pct(m.tip?.hit_rate)} tone="text-accent"
                sub={m.tip ? `${m.tip.n} picks · we said ${pct(m.tip.avg_prob)}` : undefined} />
              <Tile label="Goals tips" value={pct(m.goals?.hit_rate)}
                sub={m.goals ? `${m.goals.n} picks · we said ${pct(m.goals.avg_prob)}` : undefined} />
              <Tile label="Most likely result" value={pct(m.favourite?.hit_rate)}
                sub={m.favourite ? `${m.favourite.n} matches · we said ${pct(m.favourite.avg_prob)}` : undefined} />
              <Tile label="Brier score"
                value={vs ? vs.model.toFixed(3) : data.brier.model?.toFixed(3) ?? "—"}
                tone={vs ? (vs.model <= vs.bookmaker ? "text-accent" : "text-warn") : undefined}
                sub={vs ? `bookmaker ${vs.bookmaker.toFixed(3)} · ${vs.matches} matches · lower is better` : "1X2 accuracy · lower is better"} />
            </div>

            <section className="card overflow-hidden">
              <header className="px-4 sm:px-5 pt-4 pb-3 flex items-start gap-2">
                <Target size={16} className="text-accent mt-0.5" />
                <div>
                  <h2 className="font-semibold text-n-0">By market</h2>
                  <p className="text-xs text-n-400">What we said the picks&apos; chances were, next to how often they won.</p>
                </div>
              </header>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-[11px] uppercase tracking-[0.06em] text-n-500 border-y border-n-800 bg-surface-sunken/60">
                      <th className="text-left font-semibold px-4 sm:px-5 py-2">Market</th>
                      <th className="hidden sm:table-cell text-right font-semibold px-3 py-2">Picks</th>
                      <th className="text-right font-semibold px-2 sm:px-3 py-2 whitespace-nowrap">We said</th>
                      <th className="text-right font-semibold px-2 sm:px-3 py-2 whitespace-nowrap">Happened</th>
                      <th className="text-right font-semibold px-4 sm:px-5 py-2">Gap</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-n-800">
                    {markets.map(k => (
                      <tr key={k}>
                        <td className="px-4 sm:px-5 py-2.5">
                          <span className="block text-n-0 font-medium">{MARKET_LABELS[k] ?? m[k].name}</span>
                          <span className="block sm:hidden text-[11px] text-n-500 tnum">{m[k].n} picks</span>
                        </td>
                        <td className="hidden sm:table-cell px-3 py-2.5 text-right tnum text-n-300">{m[k].n}</td>
                        <td className="px-2 sm:px-3 py-2.5 text-right tnum text-n-300">{pct(m[k].avg_prob)}</td>
                        <td className="px-2 sm:px-3 py-2.5 text-right tnum text-n-0 font-semibold">{pct(m[k].hit_rate)}</td>
                        <td className="px-4 sm:px-5 py-2.5 text-right whitespace-nowrap"><Gap said={m[k].avg_prob} happened={m[k].hit_rate} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="px-4 sm:px-5 py-3 text-[11px] text-n-500 border-t border-n-800">
                Gap within ±5 points: the probabilities can be taken as shown. Positive: picks won more often than we said.
              </p>
            </section>

            <div className="grid gap-4 lg:grid-cols-2">
              <section className="card overflow-hidden">
                <header className="px-4 sm:px-5 pt-4 pb-3 flex items-start gap-2">
                  <Scale size={16} className="text-accent mt-0.5" />
                  <div>
                    <h2 className="font-semibold text-n-0">Calibration</h2>
                    <p className="text-xs text-n-400">Every graded pick, grouped by the chance we gave it.</p>
                  </div>
                </header>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-[11px] uppercase tracking-[0.06em] text-n-500 border-y border-n-800 bg-surface-sunken/60">
                      <th className="text-left font-semibold px-4 sm:px-5 py-2">We said</th>
                      <th className="text-right font-semibold px-3 py-2">Picks</th>
                      <th className="text-right font-semibold px-3 py-2">Happened</th>
                      <th className="text-right font-semibold px-4 sm:px-5 py-2">Gap</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-n-800">
                    {data.calibration.map(b => (
                      <tr key={b.from}>
                        <td className="px-4 sm:px-5 py-2 tnum text-n-200">{b.from}–{b.to}%</td>
                        <td className="px-3 py-2 text-right tnum text-n-400">{b.n}</td>
                        <td className="px-3 py-2 text-right tnum text-n-0 font-semibold">{pct(b.happened)}</td>
                        <td className="px-4 sm:px-5 py-2 text-right whitespace-nowrap"><Gap said={b.said} happened={b.happened} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>

              <section className="card overflow-hidden">
                <header className="px-4 sm:px-5 pt-4 pb-3">
                  <h2 className="font-semibold text-n-0">By competition</h2>
                  <p className="text-xs text-n-400">How often the most likely result happened.</p>
                </header>
                <ul className="divide-y divide-n-800 border-t border-n-800">
                  {data.leagues.slice(0, 12).map(l => (
                    <li key={l.league} className="flex items-center gap-2 px-4 sm:px-5 py-2 text-sm">
                      <CompetitionBadge name={l.name} fallbackEmoji={l.flag} size={16} />
                      <span className="flex-1 min-w-0 truncate text-n-0">{l.name}</span>
                      <span className="tnum text-n-400 text-xs">{l.hits}/{l.n}</span>
                      <span className="tnum font-semibold text-n-0 w-11 text-right">{pct(l.hit_rate)}</span>
                    </li>
                  ))}
                </ul>
              </section>
            </div>

            {data.daily.length > 1 && (
              <section className="card p-4 sm:p-5 space-y-3">
                <div>
                  <h2 className="font-semibold text-n-0">Most likely result, day by day</h2>
                  <p className="text-xs text-n-400">Share of each day&apos;s matches where our most likely result happened. Hover for the day.</p>
                </div>
                <ColumnChart name="Most likely result hit rate"
                  labels={data.daily.map(d => new Date(`${d.date}T12:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short" }))}
                  values={data.daily.map(d => Math.round(d.favourite_hit * 100))}
                  format={n => `${n}%`} tick={Math.max(1, Math.ceil(data.daily.length / 8))} />
              </section>
            )}
          </>
        )}

        <MyTickets />

        <p className="text-center text-xs text-n-500">
          Predictions are locked at kick-off: what&apos;s graded is exactly what the site showed before the match. 18+ · Gamble responsibly.
        </p>
      </div>
    </AppShell>
  );
}
