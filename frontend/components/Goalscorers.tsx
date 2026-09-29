"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import { Check, Plus } from "lucide-react";
import { useBetSlip } from "@/lib/useBetSlip";
import { isSelected, type SlipSelection } from "@/lib/slip";

const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

interface Scorer {
  player: string; label: string; code: string; prob: number; odds: number; edge: number;
  minutes?: number; npxg90?: number; pen_share?: number;
  sb: { eventId: string; marketId: string; specifier: string; outcomeId: string };
}

/** Anytime goalscorers: our chance (his xG, minutes and tonight's expected
 *  goals for his team), SportyBet's price, and a tap into the slip. */
export function Goalscorers({ home, away, date, time, league }: {
  home: string; away: string; date: string; time?: string; league?: string;
}) {
  const [rows, setRows] = useState<Scorer[] | null>(null);
  const [all, setAll] = useState(false);
  const { items, toggle } = useBetSlip();

  useEffect(() => {
    const q = new URLSearchParams({ home, away, ...(date ? { date } : {}) });
    fetch(`${API}/api/props/football?${q}`).then(r => (r.ok ? r.json() : { scorers: [] }))
      .then(d => setRows(Array.isArray(d?.scorers) ? d.scorers : []))
      .catch(() => setRows([]));
  }, [home, away, date]);

  if (!rows || rows.length === 0) return null;
  const shown = all ? rows : rows.slice(0, 8);
  const sel = (s: Scorer): SlipSelection => ({ home, away, date, time, league, market: "anytime_scorer",
    marketName: "Anytime Goalscorer", code: s.code, label: s.label, prob: s.prob, sb: s.sb });
  return (
    <section className="card p-4 sm:p-5">
      <div className="flex items-center gap-2 mb-1">
        <h2 className="font-display font-bold text-lg uppercase tracking-[0.06em] text-n-0">Goalscorers</h2>
        <span className="ml-auto text-[11px] text-n-500">Our chance · SportyBet</span>
      </div>
      <p className="text-[11px] text-n-500 mb-3">
        From each player&apos;s shots quality (xG) and minutes, and our expected goals for his team tonight.
        Void on SportyBet if he doesn&apos;t play.
      </p>
      <div className="space-y-1.5">
        {shown.map(s => {
          const on = isSelected(items, sel(s));
          const value = s.edge >= 0.05;
          return (
            <button key={s.code} type="button" onClick={() => toggle(sel(s))} aria-pressed={on}
              className={clsx("w-full flex items-center gap-2.5 rounded-lg border px-3 py-2 text-left transition-colors",
                on ? "border-accent/50 bg-brand-400/10" : "border-n-800 bg-surface-sunken hover:bg-surface-raised")}>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-semibold text-n-0">{s.player}</span>
                <span className="block text-[11px] text-n-500 tnum">
                  {s.minutes ? `~${s.minutes} min` : ""}{s.npxg90 != null ? ` · ${s.npxg90.toFixed(2)} xG/90` : ""}
                  {s.pen_share && s.pen_share > 0.3 ? " · takes penalties" : ""}
                </span>
              </span>
              {value && <span className="rounded-full border border-accent/30 bg-brand-400/10 px-1.5 text-[10px] font-bold text-accent">Value</span>}
              <span className="tnum font-display font-extrabold text-accent text-[15px] shrink-0">{Math.round(s.prob * 100)}%</span>
              <span className="tnum font-mono text-[12px] text-n-300 w-11 text-right shrink-0">{s.odds.toFixed(2)}</span>
              <span className={clsx("inline-flex items-center justify-center w-5 h-5 rounded-full shrink-0",
                on ? "bg-accent text-ink" : "bg-n-800 text-n-400")}>{on ? <Check size={12} /> : <Plus size={12} />}</span>
            </button>
          );
        })}
      </div>
      {rows.length > 8 && (
        <button type="button" onClick={() => setAll(a => !a)} className="mt-2 text-xs font-semibold text-accent">
          {all ? "Show fewer" : `All ${rows.length} players`}
        </button>
      )}
    </section>
  );
}
