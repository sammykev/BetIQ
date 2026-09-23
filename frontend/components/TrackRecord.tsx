"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import { fetchModelMetrics, type ModelMetrics, type PickTierRow } from "@/lib/api";

// How the model did on matches it hadn't seen: a monthly walk-forward
// backtest (backend/backtest.py). Admin dashboard only — the endpoint needs
// the admin secret. Renders nothing until metrics exist.

const pct = (x: number | null | undefined, digits = 0) =>
  x === null || x === undefined ? "–" : `${(x * 100).toFixed(digits)}%`;
const signedPct = (x: number | null) =>
  x === null ? "–" : `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x * 100).toFixed(1)}%`;
const count = (n: number) => n.toLocaleString("en-US");

function monthYear(d: string) {
  return new Date(d + "T12:00:00").toLocaleDateString("en-US", { month: "short", year: "numeric" });
}

function Tile({ label, value, detail, tone = "text-n-0" }: { label: string; value: string; detail: string; tone?: string }) {
  return (
    <div className="rounded-xl bg-surface-sunken px-4 py-3">
      <p className="eyebrow truncate">{label}</p>
      <p className={clsx("font-display font-extrabold text-3xl leading-none mt-1 tnum", tone)}>{value}</p>
      <p className="text-[11px] text-n-400 mt-1.5 leading-snug">{detail}</p>
    </div>
  );
}

const TIER_TONE = { strong: "text-accent", lean: "text-warn", weak: "text-n-500", all: "text-n-300" } as const;

function TierRows({ title, rows }: { title: string; rows: PickTierRow[] }) {
  const shown = rows.filter(r => r.tier !== "all" && r.n > 0);
  if (!shown.length) return null;
  return (
    <>
      <tr><td colSpan={3} className="pt-3 pb-1 text-[10px] font-bold uppercase tracking-wider text-n-500">{title}</td></tr>
      {shown.map(r => (
        <tr key={r.tier} className="border-t border-n-800">
          <td className={clsx("py-1.5 font-semibold capitalize", TIER_TONE[r.tier])}>{r.tier}</td>
          <td className="py-1.5 text-right tnum text-n-400">{count(r.n)}</td>
          <td className="py-1.5 text-right tnum">
            <span className="font-bold text-n-0">{pct(r.hit_rate)}</span>
            <span className="text-n-500"> / {pct(r.avg_prob)}</span>
          </td>
        </tr>
      ))}
    </>
  );
}

export function TrackRecord({ secret }: { secret: string }) {
  const [m, setM] = useState<ModelMetrics | null>(null);

  useEffect(() => { fetchModelMetrics(secret).then(setM); }, [secret]);
  if (!m || !m.period || !m.matches) return null;

  const tier = (kind: string, t: string) => m.picks.by_tier.find(r => r.kind === kind && r.tier === t);
  const single = tier("single", "all");
  const double = tier("double", "all");
  const goals = m.goals_tips.all;
  const straight = m.betting.all_straight_tips;
  const value = m.betting.value_bets;
  const buckets = m.calibration.filter(b => b.n >= 30);
  const mr = m.match_result;

  return (
    <section className="card p-5 space-y-5" aria-labelledby="track-record-title">
      <div>
        <p className="eyebrow">Model track record</p>
        <h2 id="track-record-title" className="font-display font-extrabold uppercase tracking-wide text-2xl leading-tight text-n-0 mt-0.5">
          Backtest · {monthYear(m.period.from)} – {monthYear(m.period.to)}
        </h2>
        <p className="text-sm text-n-400 mt-1 max-w-2xl">
          {count(m.matches)} matches across {m.leagues.length} leagues. The model was retrained
          every month and predicted each match before seeing its result.
        </p>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Tile label="Straight wins" value={pct(single?.hit_rate)}
          detail={`${count(single?.n ?? 0)} picks · we said ${pct(single?.avg_prob)} on average`} />
        <Tile label="Double chance" value={pct(double?.hit_rate)}
          detail={`${count(double?.n ?? 0)} picks · we said ${pct(double?.avg_prob)} on average`} />
        <Tile label="Goals tips" value={pct(goals.hit_rate)}
          detail={`${count(goals.n)} tips · ${count(goals.push)} pushes refunded`} />
        <Tile label="Return" value={signedPct(straight.roi)}
          tone={straight.roi !== null && straight.roi >= 0 ? "text-accent" : "text-danger"}
          detail={`flat stake on every straight tip at Bet365 odds · value bets ${signedPct(value.roi)} (${count(value.bets)})`} />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Calibration */}
        <div>
          <p className="text-xs font-bold text-n-0">When the model says…</p>
          <p className="text-[11px] text-n-500 mb-3">Bar: how often it happened. Tick: what we predicted.</p>
          <ul className="space-y-2">
            {buckets.map(b => (
              <li key={b.from} className="grid grid-cols-[3.75rem_1fr_3rem] items-center gap-2 text-[11px]">
                <span className="tnum text-n-400">{Math.round(b.from * 100)}–{Math.round(b.to * 100)}%</span>
                <span className="relative h-2 rounded-full bg-n-800" role="img"
                  aria-label={`Predicted ${pct(b.predicted)}, happened ${pct(b.actual)} of ${b.n}`}>
                  <span className="absolute inset-y-0 left-0 rounded-full bg-accent" style={{ width: `${b.actual * 100}%` }} />
                  <span className="absolute -top-0.5 -bottom-0.5 w-0.5 rounded bg-n-0" style={{ left: `calc(${b.predicted * 100}% - 1px)` }} />
                </span>
                <span className="tnum text-right font-semibold text-n-0">{pct(b.actual)}</span>
              </li>
            ))}
          </ul>
        </div>

        {/* Hit rate by tier */}
        <div>
          <p className="text-xs font-bold text-n-0">Hit rate by pick strength</p>
          <p className="text-[11px] text-n-500">How often picks won, next to the probability we gave them.</p>
          <table className="w-full text-xs mt-1">
            <thead>
              <tr className="text-[10px] text-n-500">
                <th className="pt-2 text-left font-medium"><span className="sr-only">Strength</span></th>
                <th className="pt-2 text-right font-medium">Picks</th>
                <th className="pt-2 text-right font-medium">Won / said</th>
              </tr>
            </thead>
            <tbody>
              <TierRows title="Straight wins" rows={m.picks.by_tier.filter(r => r.kind === "single")} />
              <TierRows title="Double chance" rows={m.picks.by_tier.filter(r => r.kind === "double")} />
            </tbody>
          </table>
        </div>
      </div>

      <div className="border-t border-n-800 pt-4 space-y-1.5 text-[11px] text-n-400">
        <p>
          <span className="font-semibold text-n-300">Against the bookmaker:</span>{" "}
          picked the result {pct(mr.model.accuracy, 1)} of the time vs the Bet365 favourite&apos;s {pct(mr.market.accuracy, 1)}.
          Log loss {mr.model.log_loss?.toFixed(3)} vs {mr.market.log_loss?.toFixed(3)} (lower is better).
        </p>
        {m.without_odds && (
          <p>
            <span className="font-semibold text-n-300">Fixtures without odds:</span>{" "}
            picked the result {pct(m.without_odds.match_result.model.accuracy, 1)} of the time, log loss{" "}
            {m.without_odds.match_result.model.log_loss?.toFixed(3)}; straight wins hit {pct(m.without_odds.straight?.hit_rate)}{" "}
            (said {pct(m.without_odds.straight?.avg_prob)}), double chance {pct(m.without_odds.double?.hit_rate)}{" "}
            (said {pct(m.without_odds.double?.avg_prob)}).
          </p>
        )}
        <p className="text-n-500">Refresh with <code>python backtest.py</code> in backend/, then redeploy.</p>
      </div>
    </section>
  );
}
