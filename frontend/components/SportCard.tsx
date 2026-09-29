"use client";

import { useState } from "react";
import { getSportAssets, sportDbSport } from "@/lib/sportsAssets";
import { useTeamLogo } from "@/lib/useTeamLogo";
import { CompetitionBadge } from "./CompetitionBadge";
import { kickoff } from "@/lib/matchTime";
import clsx from "clsx";
import { FormDots } from "./BasketballCard";

interface SpreadLine {
  point: number | null;
  odds: number;
}

export interface SportPrediction {
  home: string;
  away: string;
  date: string;
  time: string;
  sport: string;
  league_name: string;
  flag: string;
  tip_1x2: string;
  tip_code: string;
  tip_goals?: string;
  goals_confidence: number;
  p_home: number;
  p_away: number;
  surface?: string;
  odds_home?: number;
  odds_away?: number;
  total_line?: number;
  spread_home?: SpreadLine | null;
  spread_away?: SpreadLine | null;
  /** "safe": model backs the market favorite with real conviction.
   *  "upset": model picks the market's underdog to win outright. */
  pick_type?: "safe" | "upset" | null;
  /** Tennis: each player's last 5, oldest to newest ("WWLWL") */
  home_form?: string; away_form?: string;
}

function spreadLabel(name: string, spread: SpreadLine): string {
  if (spread.point == null) return name;
  return `${name} ${spread.point > 0 ? "+" : ""}${spread.point}`;
}

interface Props {
  prediction: SportPrediction;
  onClick?: () => void;
}

function InitialsAvatar({ color, name }: { color: string; name: string }) {
  return (
    <span
      className="inline-flex items-center justify-center w-[30px] h-[30px] rounded-full text-white font-bold text-[11px] shrink-0 ring-1 ring-white/10"
      style={{ background: color }}
    >
      {name.slice(0, 2).toUpperCase()}
    </span>
  );
}

function Avatar({ staticImage, color, name, face, sport }: {
  staticImage?: string; color: string; name: string; face: boolean; sport: string;
}) {
  const [broken, setBroken] = useState(false);
  // Only basketball uses the generic team-logo lookup — it covers any club in
  // any league (EuroLeague, NCAA, NBL, NBA Summer League, ...) without a
  // hardcoded list. Tennis/table-tennis use curated player photo maps only.
  const dynamicImage = useTeamLogo(name, sport === "basketball" && !staticImage);
  const image = staticImage || dynamicImage;

  if (image && !broken) {
    return (
      <span className="relative inline-flex w-[30px] h-[30px] rounded-full bg-n-800 ring-1 ring-n-700/80 overflow-hidden shrink-0">
        <img
          src={image}
          alt={name}
          className={clsx("w-full h-full", face ? "object-cover object-top" : "object-contain p-1")}
          onError={() => setBroken(true)}
        />
      </span>
    );
  }
  return <InitialsAvatar color={color} name={name} />;
}

export function SportCard({ prediction: p, onClick }: Props) {
  const assets = getSportAssets(p.home, p.away, p.sport);
  const conf   = Math.round(p.goals_confidence * 100);
  const homeStronger = p.p_home >= p.p_away;
  const strong = conf >= 65;
  const lean = !strong && conf >= 50;

  return (
    <article
      onClick={onClick}
      className={clsx(onClick ? "card-interactive" : "card", "overflow-hidden flex flex-col")}
    >
      {/* Header strip */}
      <div className="flex items-center justify-between gap-2 px-4 py-2.5 border-b border-n-800/80">
        <div className="flex items-center gap-1.5 min-w-0">
          <CompetitionBadge name={p.league_name} fallbackEmoji={p.flag} size={13} sport={sportDbSport(p.sport)} />
          <span className="eyebrow truncate">
            {p.league_name}{p.surface ? ` · ${p.surface}` : ""}
          </span>
          {p.pick_type === "safe" && (
            <span className="font-display font-bold text-[11px] uppercase tracking-wider text-accent bg-brand-400/10 [box-shadow:inset_0_0_0_1px_rgb(var(--accent)/0.3)] px-1.5 rounded shrink-0">
              Safe
            </span>
          )}
          {p.pick_type === "upset" && (
            <span className="font-display font-bold text-[11px] uppercase tracking-wider text-violet-700 dark:text-violet-300 bg-violet-500/10 [box-shadow:inset_0_0_0_1px_rgb(167_139_250/0.3)] px-1.5 rounded shrink-0">
              Upset
            </span>
          )}
        </div>
        <span className="font-mono text-[11px] text-n-200 uppercase shrink-0">{kickoff(p.date, p.time)}</span>
      </div>

      <div className="flex-1 flex flex-col px-4 pt-3.5 pb-4 gap-3">
        {/* Competitors */}
        <div className="space-y-2.5">
          {[
            { name: p.home, prob: p.p_home, odds: p.odds_home, image: assets.homeImage, color: assets.homeColor, fav: homeStronger, form: p.home_form },
            { name: p.away, prob: p.p_away, odds: p.odds_away, image: assets.awayImage, color: assets.awayColor, fav: !homeStronger, form: p.away_form },
          ].map(({ name, prob, odds, image, color, fav, form }) => (
            <div key={name} className="flex items-center gap-3 min-w-0">
              <Avatar staticImage={image} color={color} name={name} face={assets.isPlayerFace} sport={p.sport} />
              <span className="flex-1 min-w-0">
                <span className={clsx("block truncate text-[15px] leading-tight", fav ? "font-bold text-n-0" : "font-semibold text-n-400")}>
                  {name}
                </span>
                {form && <FormDots form={form} />}
              </span>
              {odds ? <span className="font-mono text-[11px] text-n-500 shrink-0" title="Bookmaker odds">{odds.toFixed(2)}</span> : null}
              <span className={clsx(
                "font-display font-extrabold text-[30px] leading-none w-[3.25rem] text-right shrink-0 tnum",
                fav ? "text-n-0" : "text-n-500"
              )}>
                {Math.round(prob * 100)}<span className="text-[15px] font-bold text-n-500 align-top ml-px">%</span>
              </span>
            </div>
          ))}
        </div>

        {/* Head-to-head split */}
        <div className="flex h-1 rounded-full overflow-hidden gap-0.5">
          <div className={homeStronger ? "bg-accent" : "bg-n-600"} style={{ width: `${Math.round(p.p_home * 100)}%` }} />
          <div className={!homeStronger ? "bg-accent" : "bg-n-600"} style={{ width: `${Math.round(p.p_away * 100)}%` }} />
        </div>

        {/* Best pick ticket */}
        <div className={clsx(
          "mt-auto flex items-center gap-3 rounded-xl px-3.5 py-3",
          strong ? "bg-brand-400 text-ink"
            : lean ? "bg-amber-400/[0.06] [box-shadow:inset_0_0_0_1px_rgb(251_191_36/0.3)] text-n-0"
            : "bg-n-800/60 text-n-0"
        )}>
          <div className="min-w-0 flex-1">
            <p className={clsx("font-display font-bold text-[11px] uppercase tracking-[0.14em]", strong ? "text-ink/60" : "text-n-400")}>Best pick</p>
            <p className="font-display font-extrabold text-xl uppercase leading-tight truncate">{p.tip_1x2}</p>
            {p.tip_goals && (
              <p className={clsx("text-[11px] font-semibold truncate", strong ? "text-ink/70" : "text-n-400")}>+ {p.tip_goals}</p>
            )}
          </div>
          <div className="text-right shrink-0">
            <p className={clsx("font-display font-bold text-[11px] uppercase tracking-[0.14em]", strong ? "text-ink/60" : "text-n-400")}>Confidence</p>
            <p className={clsx("font-display font-extrabold text-[32px] leading-none tnum", strong ? "text-ink" : lean ? "text-warn" : "text-n-300")}>
              {conf}%
            </p>
          </div>
        </div>

        {/* Point spread — informational market line, not a model pick: the
            Elo/market blend estimates win probability, not margin of victory. */}
        {(p.spread_home || p.spread_away) && (
          <p className="flex items-center justify-between gap-3 text-[11px] text-n-500">
            <span className="eyebrow !text-[10px]">Spread</span>
            <span className="font-mono text-n-400 truncate">
              {p.spread_home && spreadLabel(p.home, p.spread_home)}
              {p.spread_home && p.spread_away && "  ·  "}
              {p.spread_away && spreadLabel(p.away, p.spread_away)}
            </span>
          </p>
        )}
      </div>
    </article>
  );
}
