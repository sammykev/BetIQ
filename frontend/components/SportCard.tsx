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

export function SportCard({ prediction: p, onClick }: Props) {
  const assets = getSportAssets(p.home, p.away, p.sport);
  const conf   = Math.round(p.goals_confidence * 100);

  // Background position: for player faces show top (face area), for logos center
  const imgPos  = assets.isPlayerFace ? "top center" : "center";
  const imgSize = assets.isPlayerFace ? "cover" : "65%";

  const homeDisplayTime = p.time && p.time !== "TBD" ? ` · ${localTime(p.date, p.time)}` : "";

  return (
    <div
      onClick={onClick}
      className={clsx(
        "relative overflow-hidden rounded-xl border border-white/10 flex flex-col card-glow transition-all duration-200",
        onClick && "cursor-pointer hover:brightness-110 active:scale-[0.98]"
      )}
    >
      {/* ── Background ── */}
      <div className="absolute inset-0 flex">
        <div className="flex-1" style={{ background: assets.homeColor }} />
        <div className="flex-1" style={{ background: assets.awayColor }} />
      </div>

      {assets.homeImage && (
        <div className="absolute inset-0" style={{
          backgroundImage: `url(${assets.homeImage})`,
          backgroundSize: imgSize,
          backgroundPosition: assets.isPlayerFace ? "25% top" : "30% center",
          backgroundRepeat: "no-repeat",
          maskImage: "linear-gradient(to right, black 0%, black 20%, transparent 65%)",
          WebkitMaskImage: "linear-gradient(to right, black 0%, black 20%, transparent 65%)",
        }} />
      )}
      {assets.awayImage && (
        <div className="absolute inset-0" style={{
          backgroundImage: `url(${assets.awayImage})`,
          backgroundSize: imgSize,
          backgroundPosition: assets.isPlayerFace ? "75% top" : "70% center",
          backgroundRepeat: "no-repeat",
          maskImage: "linear-gradient(to left, black 0%, black 20%, transparent 65%)",
          WebkitMaskImage: "linear-gradient(to left, black 0%, black 20%, transparent 65%)",
        }} />
      )}
      <div className="absolute inset-0 bg-black/55" />

      {/* ── Content ── */}
      <div className="relative z-10 p-4 flex flex-col gap-3">

        {/* Header */}
        <div className="flex items-center justify-between">
          <span className="text-[11px] font-semibold text-white/70 truncate max-w-[70%]">
            {p.flag} {p.league_name}
            {p.surface ? ` · ${p.surface}` : ""}
          </span>
          <span className="text-[10px] text-white/50 shrink-0">{p.date}{homeDisplayTime}</span>
        </div>

        {/* Players / Teams */}
        <div className="space-y-1">
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm font-black text-white drop-shadow-md leading-tight flex-1 truncate">{p.home}</span>
            {p.odds_home ? (
              <span className="text-xs font-bold bg-white/15 text-white px-2 py-0.5 rounded-lg shrink-0">{p.odds_home}</span>
            ) : null}
          </div>
          <p className="text-[10px] text-white/40 uppercase tracking-widest font-medium text-center">vs</p>
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm font-black text-white drop-shadow-md leading-tight flex-1 truncate">{p.away}</span>
            {p.odds_away ? (
              <span className="text-xs font-bold bg-white/15 text-white px-2 py-0.5 rounded-lg shrink-0">{p.odds_away}</span>
            ) : null}
          </div>
        </div>

        {/* Win probability bars */}
        <div className="space-y-1.5">
          {[
            { label: p.home, prob: p.p_home, color: assets.homeColor },
            { label: p.away, prob: p.p_away, color: assets.awayColor },
          ].map(({ label, prob }) => (
            <div key={label} className="space-y-0.5">
              <div className="flex justify-between text-[10px]">
                <span className="text-white/50 truncate max-w-[70%]">{label}</span>
                <span className="text-white font-semibold">{Math.round(prob * 100)}%</span>
              </div>
              <div className="h-1.5 bg-black/30 rounded-full overflow-hidden">
                <div className="h-full bg-white/70 rounded-full" style={{ width: `${Math.round(prob * 100)}%` }} />
              </div>
            </div>
          ))}
        </div>

        {/* Best pick */}
        <div className="bg-black/30 border border-white/15 rounded-xl px-3 py-2.5 flex items-center justify-between gap-3">
          <div>
            <p className="text-[10px] text-white/50 uppercase tracking-wide font-semibold">Best pick</p>
            <p className="text-sm font-bold text-white">{p.tip_1x2}</p>
            {p.tip_goals && (
              <p className="text-[10px] text-green-300 mt-0.5">{p.tip_goals}</p>
            )}
          </div>
          <div className="text-right shrink-0">
            <p className="text-[10px] text-white/50">Confidence</p>
            <p className={clsx("text-xl font-black", conf >= 65 ? "text-green-400" : "text-white")}>{conf}%</p>
          </div>
        </div>
      </div>
    </div>
  );
}
