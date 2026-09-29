"use client";

import { useState } from "react";
import clsx from "clsx";
import { Check, ChevronRight, Plus } from "lucide-react";
import { CompetitionBadge } from "./CompetitionBadge";
import { kickoff } from "@/lib/matchTime";
import { useBetSlip } from "@/lib/useBetSlip";
import { isSelected } from "@/lib/slip";
import { lineSelection, type BasketballPrediction, type BBLine } from "@/lib/basketball";

/** A team's crest (Sportradar, by SportyBet's team id), or its initials. */
export function Crest({ src, name, size = 30 }: { src?: string | null; name: string; size?: number }) {
  const [broken, setBroken] = useState(false);
  if (src && !broken) {
    return (
      <span className="relative inline-flex rounded-full bg-white/90 dark:bg-n-800 ring-1 ring-n-700/60 overflow-hidden shrink-0"
        style={{ width: size, height: size }}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={src} alt="" className="w-full h-full object-contain p-[3px]" loading="lazy" onError={() => setBroken(true)} />
      </span>
    );
  }
  return (
    <span className="inline-flex items-center justify-center rounded-full bg-n-700 text-n-0 font-bold shrink-0"
      style={{ width: size, height: size, fontSize: size * 0.34 }}>
      {name.replace(/[^A-Za-z0-9 ]/g, "").split(" ").filter(Boolean).slice(0, 2).map(w => w[0]).join("").toUpperCase()}
    </span>
  );
}

/** One line: our chance, SportyBet's price, and a tap to add it to the slip. */
export function LineRow({ p, l, compact = false }: { p: BasketballPrediction; l: BBLine; compact?: boolean }) {
  const { items, toggle } = useBetSlip();
  const sel = lineSelection(p, l);
  const inSlip = isSelected(items, sel);
  return (
    <button type="button" onClick={e => { e.stopPropagation(); toggle(sel); }}
      className={clsx("w-full flex items-center gap-2.5 rounded-lg border text-left transition-colors",
        compact ? "px-2.5 py-1.5" : "px-3 py-2",
        inSlip ? "border-accent/50 bg-brand-400/10" : "border-n-800 bg-surface-sunken hover:bg-surface-raised")}
      aria-pressed={inSlip} aria-label={`${inSlip ? "Remove" : "Add"} ${l.label} ${inSlip ? "from" : "to"} the slip`}>
      <span className="min-w-0 flex-1">
        <span className={clsx("block font-semibold text-n-0", compact ? "text-[13px] truncate" : "text-sm line-clamp-2")}>{l.label}</span>
        {!compact && <span className="block text-[11px] text-n-500 truncate">{l.market_name}</span>}
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

/** "Spain · Liga ACB"; just the competition for international ones. */
export const leagueTitle = (p: Pick<BasketballPrediction, "country" | "league_name">) =>
  p.country && !/^(international|world)$/i.test(p.country) ? `${p.country} · ${p.league_name}` : p.league_name;

export function BasketballCard({ p, onOpen }: { p: BasketballPrediction; onOpen: () => void }) {
  const homeFav = p.p_home >= p.p_away;
  const conf = Math.round(p.tip_confidence * 100);
  return (
    <article onClick={onOpen} className="card card-interactive overflow-hidden flex flex-col">
      <div className="flex items-center justify-between gap-2 px-4 py-2.5 border-b border-n-800/80">
        <div className="flex items-center gap-1.5 min-w-0">
          <CompetitionBadge name={p.league_name} fallbackEmoji={p.flag} size={13} sport="Basketball" />
          <span className="eyebrow truncate">{leagueTitle(p)}</span>
        </div>
        <span className="font-mono text-[11px] text-n-200 uppercase shrink-0">{kickoff(p.date, p.time)}</span>
      </div>

      <div className="flex-1 flex flex-col px-4 pt-3.5 pb-4 gap-3">
        <div className="space-y-2.5">
          {[{ name: p.home, logo: p.home_logo, prob: p.p_home, odds: p.odds_home, pts: p.exp_home_pts, fav: homeFav },
            { name: p.away, logo: p.away_logo, prob: p.p_away, odds: p.odds_away, pts: p.exp_away_pts, fav: !homeFav }].map(t => (
            <div key={t.name} className="flex items-center gap-3 min-w-0">
              <Crest src={t.logo} name={t.name} />
              <span className={clsx("flex-1 truncate text-[15px]", t.fav ? "font-bold text-n-0" : "font-semibold text-n-400")}>{t.name}</span>
              {t.odds ? <span className="font-mono text-[11px] text-n-500 shrink-0" title="SportyBet's price">{t.odds.toFixed(2)}</span> : null}
              <span className={clsx("font-display font-extrabold text-[26px] leading-none w-[3rem] text-right shrink-0 tnum",
                t.fav ? "text-n-0" : "text-n-500")}>
                {Math.round(t.prob * 100)}<span className="text-[13px] font-bold text-n-500 align-top ml-px">%</span>
              </span>
            </div>
          ))}
        </div>
        <div className="flex h-1 rounded-full overflow-hidden gap-0.5">
          <div className={homeFav ? "bg-accent" : "bg-n-600"} style={{ width: `${Math.round(p.p_home * 100)}%` }} />
          <div className={!homeFav ? "bg-accent" : "bg-n-600"} style={{ width: `${Math.round(p.p_away * 100)}%` }} />
        </div>

        <div className="flex items-center justify-between gap-2 text-[11px]">
          <span className="text-n-400 truncate">Winner: <span className="font-bold text-n-0">{p.tip_1x2}</span> · {conf}%</span>
          <span className="text-n-400 shrink-0 tnum" title="Our expected score">
            Expected <span className="font-bold text-n-0">{Math.round(p.exp_home_pts)}–{Math.round(p.exp_away_pts)}</span>
          </span>
        </div>

        {(p.top_lines ?? []).length > 0 && (
          <div className="space-y-1.5">
            <p className="eyebrow !text-[10px]">Likeliest lines</p>
            {(p.top_lines ?? []).slice(0, 3).map(l => <LineRow key={`${l.market}:${l.code}`} p={p} l={l} compact />)}
          </div>
        )}

        <div className="mt-auto flex items-center justify-between gap-2 pt-1 text-[11px] text-n-500">
          <span className={clsx("rounded-full border px-2 py-0.5 font-semibold",
            p.rated ? "border-accent/30 text-accent bg-brand-400/10" : "border-n-700 text-n-400")}>
            {p.rated ? "Rated teams" : "SportyBet's lines"}
          </span>
          <span className="flex items-center gap-0.5 font-semibold text-n-300">
            {p.lines ? `All ${p.lines} lines` : "Every market"} <ChevronRight size={13} />
          </span>
        </div>
      </div>
    </article>
  );
}
