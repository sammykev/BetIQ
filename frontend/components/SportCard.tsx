"use client";

import { getSportAssets } from "@/lib/sportsAssets";
import clsx from "clsx";

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
}

function localTime(date: string, utcTime: string): string {
  try {
    const dt = new Date(`${date}T${utcTime}:00Z`);
    return dt.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
  } catch { return utcTime; }
}

interface Props {
  prediction: SportPrediction;
  onClick?: () => void;
}

function Avatar({ image, color, name, face }: { image?: string; color: string; name: string; face: boolean }) {
  if (image) {
    return (
      <span className="relative inline-flex w-7 h-7 rounded-full bg-zinc-100 dark:bg-zinc-800 ring-1 ring-zinc-200/80 dark:ring-zinc-700 overflow-hidden shrink-0">
        <img
          src={image}
          alt={name}
          className={clsx("w-full h-full", face ? "object-cover object-top" : "object-contain p-1")}
          onError={e => { (e.target as HTMLImageElement).style.display = "none"; }}
        />
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center justify-center w-7 h-7 rounded-full text-white font-bold text-[10px] shrink-0"
      style={{ background: color }}
    >
      {name.slice(0, 2).toUpperCase()}
    </span>
  );
}

export function SportCard({ prediction: p, onClick }: Props) {
  const assets = getSportAssets(p.home, p.away, p.sport);
  const conf   = Math.round(p.goals_confidence * 100);
  const timeStr = p.time && p.time !== "TBD" ? ` · ${localTime(p.date, p.time)}` : "";
  const homeStronger = p.p_home >= p.p_away;

  return (
    <article
      onClick={onClick}
      className={clsx("card p-4 flex flex-col gap-3.5", onClick && "card-interactive")}
    >
      {/* Header */}
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-semibold text-zinc-400 dark:text-zinc-500 uppercase tracking-wide truncate">
          {p.flag} {p.league_name}{p.surface ? ` · ${p.surface}` : ""}
        </span>
        <span className="tnum text-[11px] text-zinc-400 dark:text-zinc-500 font-medium shrink-0">
          {p.date}{timeStr}
        </span>
      </div>

      {/* Competitors with probability rows */}
      <div className="space-y-2.5">
        {[
          { name: p.home, prob: p.p_home, odds: p.odds_home, image: assets.homeImage, color: assets.homeColor, strong: homeStronger },
          { name: p.away, prob: p.p_away, odds: p.odds_away, image: assets.awayImage, color: assets.awayColor, strong: !homeStronger },
        ].map(({ name, prob, odds, image, color, strong }) => (
          <div key={name} className="space-y-1">
            <div className="flex items-center gap-2.5 min-w-0">
              <Avatar image={image} color={color} name={name} face={assets.isPlayerFace} />
              <span className={clsx(
                "flex-1 truncate text-sm",
                strong ? "font-bold text-zinc-900 dark:text-white" : "font-medium text-zinc-600 dark:text-zinc-300"
              )}>
                {name}
              </span>
              {odds ? (
                <span className="tnum text-[11px] font-semibold text-zinc-400 dark:text-zinc-500 bg-zinc-100 dark:bg-zinc-800 px-1.5 py-0.5 rounded-md shrink-0">
                  {odds}
                </span>
              ) : null}
              <span className={clsx(
                "tnum text-sm w-10 text-right shrink-0",
                strong ? "font-bold text-zinc-900 dark:text-white" : "font-medium text-zinc-400 dark:text-zinc-500"
              )}>
                {Math.round(prob * 100)}%
              </span>
            </div>
            <div className="h-1.5 bg-zinc-100 dark:bg-zinc-800 rounded-full overflow-hidden ml-[38px]">
              <div
                className={clsx("h-full rounded-full", strong ? "bg-brand-500" : "bg-zinc-300 dark:bg-zinc-600")}
                style={{ width: `${Math.round(prob * 100)}%` }}
              />
            </div>
          </div>
        ))}
      </div>

      {/* Best pick */}
      <div className="flex items-center justify-between gap-3 bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-100 dark:border-zinc-800 rounded-xl px-3 py-2.5 mt-auto">
        <div className="min-w-0">
          <p className="text-[10px] text-zinc-400 dark:text-zinc-500 uppercase tracking-wider font-semibold">Best pick</p>
          <p className="text-sm font-bold text-zinc-900 dark:text-white truncate">{p.tip_1x2}</p>
          {p.tip_goals && (
            <p className="text-[11px] text-brand-600 dark:text-brand-400 font-medium mt-0.5 truncate">{p.tip_goals}</p>
          )}
        </div>
        <div className="text-right shrink-0">
          <p className="text-[10px] text-zinc-400 dark:text-zinc-500 uppercase tracking-wider font-semibold">Conf.</p>
          <p className={clsx(
            "tnum text-lg font-black leading-tight",
            conf >= 65 ? "text-brand-600 dark:text-brand-400" : conf >= 50 ? "text-amber-500" : "text-zinc-400"
          )}>
            {conf}%
          </p>
        </div>
      </div>
    </article>
  );
}
