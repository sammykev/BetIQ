"use client";

import { useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { Info, Loader2, X } from "lucide-react";
import { CompetitionBadge } from "./CompetitionBadge";
import { Crest, LineRow, leagueTitle } from "./BasketballCard";
import { kickoff } from "@/lib/matchTime";
import { useAuthedFetch } from "@/lib/useAuthedFetch";
import { FAMILIES, MODEL_NOTE, type BasketballPrediction } from "@/lib/basketball";

const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

/** One basketball match: every line SportyBet offers that we priced, by market. */
export function BasketballModal({ p, onClose }: { p: BasketballPrediction; onClose: () => void }) {
  const authFetch = useAuthedFetch();
  const [full, setFull] = useState<BasketballPrediction | null>(null);
  const [error, setError] = useState(false);
  const [family, setFamily] = useState<string>("all");
  const [minPct, setMinPct] = useState(70);

  useEffect(() => {
    authFetch(`${API}/api/basketball/match?event=${encodeURIComponent(p.sportybet_event_id)}`)
      .then(r => (r.ok ? r.json() : Promise.reject()))
      .then(setFull)
      .catch(() => setError(true));
  }, [p.sportybet_event_id, authFetch]);

  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const lines = useMemo(() => full?.bb_markets ?? p.top_lines ?? [], [full, p.top_lines]);
  const present = FAMILIES.filter(f => lines.some(l => l.family === f.id));
  const shown = lines.filter(l => (family === "all" || l.family === family) && l.prob * 100 >= minPct);
  const d = full ?? p;

  return (
    <div className="fixed inset-0 z-50 bg-ink/80 backdrop-blur-sm flex items-start justify-center p-4 overflow-y-auto"
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="w-full max-w-2xl my-8 card !rounded-3xl shadow-pop overflow-hidden animate-scale-in">
        <div className="border-b border-n-800 p-5 sm:p-6">
          <div className="flex items-start justify-between gap-3 mb-5">
            <div className="min-w-0">
              <p className="flex items-center gap-1 text-[11px] font-semibold text-n-400 uppercase tracking-wide">
                <CompetitionBadge name={p.league_name} fallbackEmoji={p.flag} size={12} sport="Basketball" />
                <span className="truncate">{leagueTitle(p)}</span>
              </p>
              <p className="tnum text-[11px] text-n-500 mt-0.5">{kickoff(p.date, p.time)}</p>
            </div>
            <button onClick={onClose} className="p-2 hover:bg-surface-raised rounded-lg" aria-label="Close">
              <X size={16} className="text-n-400" />
            </button>
          </div>
          <div className="flex items-center gap-3 sm:gap-6">
            {[{ name: p.home, logo: p.home_logo, prob: p.p_home, pts: d.exp_home_pts },
              { name: p.away, logo: p.away_logo, prob: p.p_away, pts: d.exp_away_pts }].map((t, i) => (
              <div key={t.name} className={clsx("flex-1 flex flex-col items-center text-center gap-2", i === 1 && "order-3")}>
                <Crest src={t.logo} name={t.name} size={52} />
                <p className="font-display font-extrabold uppercase text-lg sm:text-xl leading-tight text-n-0">{t.name}</p>
                <p className="tnum text-[11px] text-n-400">{Math.round(t.prob * 100)}% to win</p>
              </div>
            ))}
            <div className="order-2 text-center shrink-0">
              <p className="eyebrow !text-[10px]">Expected</p>
              <p className="font-display font-extrabold text-2xl sm:text-3xl text-n-0 tnum leading-none mt-1">
                {Math.round(d.exp_home_pts)}<span className="text-n-500 mx-1">–</span>{Math.round(d.exp_away_pts)}
              </p>
            </div>
          </div>
          <p className="mt-4 flex items-start gap-1.5 text-[11px] text-n-500">
            <Info size={12} className="shrink-0 mt-px" /> {MODEL_NOTE[d.model]}. Chances are ours; prices are SportyBet&apos;s.
          </p>
        </div>

        <div className="p-5 space-y-4">
          <div className="flex gap-1.5 overflow-x-auto [scrollbar-width:none] -mx-1 px-1">
            {[{ id: "all", name: "All" }, ...present].map(f => (
              <button key={f.id} onClick={() => setFamily(f.id)}
                className={clsx("chip shrink-0", family === f.id ? "chip-active" : "chip-idle")}>{f.name}</button>
            ))}
          </div>
          <div className="flex items-center justify-between gap-3 text-xs text-n-400">
            <span>{shown.length} line{shown.length === 1 ? "" : "s"} at {minPct}% or more</span>
            <span className="flex items-center gap-1">
              {[50, 70, 80, 90].map(v => (
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
                    {rows.map(l => <LineRow key={`${l.market}:${l.code}`} p={d} l={l} />)}
                  </section>
                );
              })}
            </div>
          )}
          <p className="text-center text-[10px] text-n-500 pt-2">
            Tap a line to add it to your slip; it books on SportyBet as it is. 18+ · Bet responsibly.
          </p>
        </div>
      </div>
    </div>
  );
}
