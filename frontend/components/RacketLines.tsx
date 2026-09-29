"use client";

import { useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { Check, Info, Loader2, Plus } from "lucide-react";
import { useAuthedFetch } from "@/lib/useAuthedFetch";
import { useBetSlip } from "@/lib/useBetSlip";
import { isSelected } from "@/lib/slip";
import { fetchRKMatch, rkSelection, RK_FAMILIES, RK_MODEL_NOTE, type RacketPrediction, type RKLine } from "@/lib/racket";
import type { RacketSport } from "@/lib/tennis";

/** One line: our chance, SportyBet's price, and a tap to add it to the slip. */
function Row({ p, l }: { p: RacketPrediction; l: RKLine }) {
  const { items, toggle } = useBetSlip();
  const sel = rkSelection(p, l);
  const inSlip = isSelected(items, sel);
  return (
    <button type="button" onClick={() => toggle(sel)}
      className={clsx("w-full flex items-center gap-2.5 rounded-lg border text-left transition-colors px-3 py-2",
        inSlip ? "border-accent/50 bg-brand-400/10" : "border-n-800 bg-surface-sunken hover:bg-surface-raised")}
      aria-pressed={inSlip} aria-label={`${inSlip ? "Remove" : "Add"} ${l.label} ${inSlip ? "from" : "to"} the slip`}>
      <span className="min-w-0 flex-1">
        <span className="block font-semibold text-n-0 text-sm line-clamp-2">{l.label}</span>
        <span className="block text-[11px] text-n-500 truncate">{l.market_name}</span>
      </span>
      <span className="tnum font-display font-extrabold text-accent text-[15px] shrink-0">{Math.round(l.prob * 100)}%</span>
      <span className="tnum font-mono text-[12px] text-n-300 w-10 text-right shrink-0">{l.odds.toFixed(2)}</span>
      <span className={clsx("inline-flex items-center justify-center w-5 h-5 rounded-full shrink-0",
        inSlip ? "bg-accent text-ink" : "bg-n-800 text-n-400")}>
        {inSlip ? <Check size={12} /> : <Plus size={12} />}
      </span>
    </button>
  );
}

/**
 * Every line SportyBet offers on a tennis or table tennis match, priced by
 * our model, by market, with a chance filter; each goes in the slip (and
 * books on SportyBet) as it is.
 */
export function RacketLines({ sport, event, fallback }: { sport: RacketSport; event: string; fallback: RacketPrediction }) {
  const authFetch = useAuthedFetch();
  const [full, setFull] = useState<RacketPrediction | null>(null);
  const [error, setError] = useState(false);
  const [family, setFamily] = useState("all");
  const [minPct, setMinPct] = useState(60);

  useEffect(() => {
    const ctrl = new AbortController();
    fetchRKMatch(authFetch, sport, event, ctrl.signal).then(setFull).catch(() => { if (!ctrl.signal.aborted) setError(true); });
    return () => ctrl.abort();
  }, [authFetch, sport, event]);

  const d = full ?? fallback;
  const lines = useMemo(() => full?.rk_markets ?? fallback.top_lines ?? [], [full, fallback.top_lines]);
  const present = RK_FAMILIES[sport].filter(f => lines.some(l => l.family === f.id));
  const shown = lines.filter(l => (family === "all" || l.family === family) && l.prob * 100 >= minPct);

  return (
    <div className="space-y-4">
      <p className="flex items-start gap-1.5 text-[11px] text-n-500">
        <Info size={12} className="shrink-0 mt-px" /> {RK_MODEL_NOTE[d.model] ?? RK_MODEL_NOTE.market}. Chances are ours; prices are SportyBet&apos;s.
      </p>
      {d.set_scores && (
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(d.set_scores).slice(0, 6).map(([score, v]) => (
            <span key={score} className="text-[11px] text-n-300 bg-surface-sunken border border-n-800 rounded-md px-2 py-0.5 tnum">
              {score} · {Math.round(v * 100)}%
            </span>
          ))}
        </div>
      )}
      <div className="flex gap-1.5 overflow-x-auto [scrollbar-width:none] -mx-1 px-1">
        {[{ id: "all", name: "All" }, ...present].map(f => (
          <button key={f.id} onClick={() => setFamily(f.id)}
            className={clsx("chip shrink-0", family === f.id ? "chip-active" : "chip-idle")}>{f.name}</button>
        ))}
      </div>
      <div className="flex items-center justify-between gap-3 text-xs text-n-400">
        <span>{shown.length} line{shown.length === 1 ? "" : "s"} at {minPct}% or more</span>
        <span className="flex items-center gap-1">
          {[50, 60, 70, 80, 90].map(v => (
            <button key={v} onClick={() => setMinPct(v)}
              className={clsx("rounded-md px-2 py-0.5 font-semibold tnum", minPct === v ? "bg-accent text-ink" : "bg-surface-sunken text-n-300")}>
              {v}%+
            </button>
          ))}
        </span>
      </div>
      {!full && !error ? (
        <div className="flex items-center justify-center gap-2 py-8 text-n-400 text-sm">
          <Loader2 size={16} className="animate-spin" /> Loading every line…
        </div>
      ) : shown.length === 0 ? (
        <p className="text-center text-sm text-n-400 py-6">
          {error ? "Couldn't load this match's lines. Its likeliest ones are on the card." : "No lines at that chance in this market."}
        </p>
      ) : (
        <div className="space-y-4">
          {(family === "all" ? present : present.filter(f => f.id === family)).map(f => {
            const rows = shown.filter(l => l.family === f.id).sort((a, b) => b.prob - a.prob);
            if (!rows.length) return null;
            return (
              <section key={f.id} className="space-y-1.5">
                <h3 className="font-display font-bold text-sm uppercase tracking-[0.06em] text-n-0">{f.name}</h3>
                {rows.map(l => <Row key={`${l.market}:${l.code}`} p={d} l={l} />)}
              </section>
            );
          })}
        </div>
      )}
      <p className="text-center text-[10px] text-n-500 pt-2">
        Tap a line to add it to your slip; it books on SportyBet as it is. 18+ · Bet responsibly.
      </p>
    </div>
  );
}
