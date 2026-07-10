"use client";

import { useEffect, useState } from "react";
import { X, Star, Loader2, TrendingUp } from "lucide-react";
import clsx from "clsx";
import { getSportAssets, sportDbSport } from "@/lib/sportsAssets";
import { useTeamLogo } from "@/lib/useTeamLogo";
import { CompetitionBadge } from "./CompetitionBadge";
import type { SportPrediction } from "./SportCard";

const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

interface Outcome {
  name:    string;
  label:   string;
  odds:    number;
  implied: number;
  point:   number | null;
}

interface Market {
  id:       string;
  name:     string;
  icon:     string;
  outcomes: Outcome[];
}

interface EventDetail {
  home:      string;
  away:      string;
  date:      string;
  time:      string;
  sport:     string;
  markets:   Market[];
  best_pick: { label: string; odds: number; confidence: number } | null;
}

function OddsBar({ outcome, isBest }: { outcome: Outcome; isBest: boolean }) {
  const pct = Math.round(outcome.implied * 100);
  return (
    <div className={clsx(
      "flex items-center gap-3 px-3 py-2.5 rounded-xl transition-all border",
      isBest
        ? "bg-brand-50 dark:bg-brand-900/20 border-brand-200 dark:border-brand-800"
        : "bg-zinc-50 dark:bg-zinc-800/60 border-zinc-100 dark:border-zinc-800"
    )}>
      <div className="flex-1 min-w-0">
        <p className={clsx("text-sm font-semibold truncate",
          isBest ? "text-brand-700 dark:text-brand-400" : "text-zinc-700 dark:text-zinc-200")}>
          {outcome.label}
        </p>
        <div className="flex items-center gap-2 mt-1">
          <div className="flex-1 h-1 bg-zinc-200 dark:bg-zinc-700 rounded-full overflow-hidden">
            <div className={clsx("h-full rounded-full", isBest ? "bg-brand-500" : "bg-zinc-400 dark:bg-zinc-500")}
              style={{ width: `${pct}%` }} />
          </div>
          <span className="tnum text-[11px] text-zinc-400 dark:text-zinc-500 shrink-0">{pct}%</span>
        </div>
      </div>
      <div className="text-right shrink-0">
        <span className={clsx("tnum text-lg font-black",
          isBest ? "text-brand-600 dark:text-brand-400" : "text-zinc-700 dark:text-zinc-200")}>
          {outcome.odds}
        </span>
        {isBest && (
          <p className="text-[9px] text-brand-600/70 dark:text-brand-400/70 font-bold uppercase tracking-wide">Best odds</p>
        )}
      </div>
    </div>
  );
}

function Avatar({ staticImage, color, name, face, size = 44, sport }: {
  staticImage?: string; color: string; name: string; face: boolean; size?: number; sport: string;
}) {
  const [broken, setBroken] = useState(false);
  const dynamicImage = useTeamLogo(name, sport === "basketball" && !staticImage);
  const image = staticImage || dynamicImage;

  if (image && !broken) {
    return (
      <span
        className="relative inline-flex rounded-full bg-zinc-100 dark:bg-zinc-800 ring-1 ring-zinc-200/80 dark:ring-zinc-700 overflow-hidden shrink-0"
        style={{ width: size, height: size }}
      >
        <img
          src={image}
          alt={name}
          className={clsx("w-full h-full", face ? "object-cover object-top" : "object-contain p-1.5")}
          onError={() => setBroken(true)}
        />
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center justify-center rounded-full text-white font-bold shrink-0"
      style={{ width: size, height: size, background: color, fontSize: size * 0.32 }}
    >
      {name.slice(0, 2).toUpperCase()}
    </span>
  );
}

interface Props {
  prediction: SportPrediction;
  onClose: () => void;
}

export function SportModal({ prediction: p, onClose }: Props) {
  const [detail, setDetail]   = useState<EventDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(false);

  const assets = getSportAssets(p.home, p.away, p.sport);

  useEffect(() => {
    const params = new URLSearchParams({ home: p.home, away: p.away, date: p.date });
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 20000);  // 20s timeout
    fetch(`${API}/api/sports/${p.sport}/event?${params}`, { signal: ctrl.signal })
      .then(r => { if (!r.ok) throw new Error(r.status.toString()); return r.json(); })
      .then(d => setDetail(d))
      .catch(() => setError(true))
      .finally(() => { setLoading(false); clearTimeout(timer); });
  }, [p.home, p.away, p.date, p.sport]);

  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const sportLabel: Record<string, string> = {
    basketball: "Basketball", tennis: "Lawn Tennis", table_tennis: "Table Tennis",
  };

  return (
    <div className="fixed inset-0 z-50 bg-zinc-950/60 backdrop-blur-sm flex items-start justify-center p-4 overflow-y-auto"
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="w-full max-w-2xl my-8 card !rounded-3xl shadow-pop overflow-hidden animate-scale-in">

        {/* Header */}
        <div className="border-b border-zinc-100 dark:border-zinc-800 p-5 sm:p-6">
          <div className="flex items-start justify-between gap-3 mb-5">
            <div>
              <p className="flex items-center gap-1 text-[11px] font-semibold text-zinc-400 dark:text-zinc-500 uppercase tracking-wide">
                <CompetitionBadge name={p.league_name} fallbackEmoji={p.flag} size={12} sport={sportDbSport(p.sport)} />
                <span>
                  {p.league_name} · {sportLabel[p.sport] || p.sport}
                  {p.surface ? ` · ${p.surface}` : ""}
                </span>
              </p>
              <p className="tnum text-[11px] text-zinc-400 dark:text-zinc-500 mt-0.5">
                {p.date}{p.time && p.time !== "TBD" ? ` · ${p.time}` : ""}
              </p>
            </div>
            <button onClick={onClose}
              className="p-2 hover:bg-zinc-100 dark:hover:bg-zinc-800 rounded-lg transition-colors">
              <X size={16} className="text-zinc-400" />
            </button>
          </div>

          <div className="flex items-center gap-4 sm:gap-8">
            <div className="flex-1 flex flex-col items-center text-center gap-2">
              <Avatar staticImage={assets.homeImage} color={assets.homeColor} name={p.home} face={assets.isPlayerFace} sport={p.sport} />
              <div>
                <p className="text-base font-bold text-zinc-900 dark:text-white leading-tight">{p.home}</p>
                <p className="tnum text-[11px] text-zinc-400 dark:text-zinc-500">{Math.round(p.p_home * 100)}% win prob</p>
              </div>
            </div>
            <div className="text-zinc-300 dark:text-zinc-600 font-black">vs</div>
            <div className="flex-1 flex flex-col items-center text-center gap-2">
              <Avatar staticImage={assets.awayImage} color={assets.awayColor} name={p.away} face={assets.isPlayerFace} sport={p.sport} />
              <div>
                <p className="text-base font-bold text-zinc-900 dark:text-white leading-tight">{p.away}</p>
                <p className="tnum text-[11px] text-zinc-400 dark:text-zinc-500">{Math.round(p.p_away * 100)}% win prob</p>
              </div>
            </div>
          </div>
        </div>

        {/* Body */}
        <div className="p-5 space-y-5">

          {/* Best pick banner */}
          {(detail?.best_pick || p.tip_1x2) && (
            <div className="bg-brand-50/60 dark:bg-brand-900/15 border border-brand-200 dark:border-brand-800 rounded-2xl p-4 flex items-center gap-4">
              <div className="w-10 h-10 bg-brand-100 dark:bg-brand-900/40 rounded-full flex items-center justify-center shrink-0">
                <Star size={18} className="text-brand-600 dark:text-brand-400" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-[10px] text-brand-700 dark:text-brand-400 font-bold uppercase tracking-wider mb-0.5">Best pick</p>
                <p className="text-zinc-900 dark:text-white font-bold text-base">
                  {detail?.best_pick?.label || p.tip_1x2}
                </p>
                <p className="tnum text-xs text-zinc-500 dark:text-zinc-400">
                  {Math.round((detail?.best_pick?.confidence || p.goals_confidence) * 100)}% confidence
                </p>
              </div>
              {(detail?.best_pick?.odds) && (
                <p className="tnum text-2xl font-black text-brand-600 dark:text-brand-400 shrink-0">{detail.best_pick.odds}</p>
              )}
            </div>
          )}

          {/* Markets */}
          {loading ? (
            <div className="flex items-center justify-center gap-2 py-10 text-zinc-400 dark:text-zinc-500">
              <Loader2 size={18} className="animate-spin" />
              <span className="text-sm font-medium">Loading live markets…</span>
            </div>
          ) : error ? (
            <div className="text-center py-8 space-y-3">
              <TrendingUp size={28} className="text-zinc-300 dark:text-zinc-600 mx-auto" />
              <p className="text-zinc-500 dark:text-zinc-400 text-sm font-medium">Live markets unavailable for this match.</p>
              <p className="text-zinc-400 dark:text-zinc-600 text-xs max-w-md mx-auto">
                This can happen when no active tournament is running for {p.sport === "tennis" ? "tennis" : p.sport === "table_tennis" ? "table tennis" : "this sport"}.
                The Odds API only covers major tournaments when they&apos;re in progress.
              </p>
              {/* Show what we DO have from the prediction card */}
              <div className="mt-4 bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-100 dark:border-zinc-800 rounded-2xl p-4 text-left space-y-2">
                <p className="text-[10px] text-zinc-400 dark:text-zinc-500 font-bold uppercase tracking-wider">Our model prediction</p>
                <div className="flex justify-between text-sm">
                  <span className="text-zinc-900 dark:text-white font-semibold">{p.home}</span>
                  <span className="tnum text-brand-600 dark:text-brand-400 font-black">{Math.round(p.p_home * 100)}%</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-zinc-900 dark:text-white font-semibold">{p.away}</span>
                  <span className="tnum text-brand-600 dark:text-brand-400 font-black">{Math.round(p.p_away * 100)}%</span>
                </div>
                <div className="pt-2 border-t border-zinc-100 dark:border-zinc-800">
                  <p className="text-xs text-zinc-500 dark:text-zinc-400">Best pick: <span className="text-zinc-900 dark:text-white font-bold">{p.tip_1x2}</span></p>
                  <p className="text-xs text-zinc-500 dark:text-zinc-400">Confidence: <span className="tnum text-brand-600 dark:text-brand-400 font-bold">{Math.round(p.goals_confidence * 100)}%</span></p>
                  {p.odds_home && <p className="tnum text-xs text-zinc-500 dark:text-zinc-400 mt-1">
                    Odds: <span className="text-zinc-900 dark:text-white">{p.home} {p.odds_home}</span> · <span className="text-zinc-900 dark:text-white">{p.away} {p.odds_away}</span>
                  </p>}
                </div>
              </div>
            </div>
          ) : detail?.markets.map(market => {
            const bestOdds = Math.max(...market.outcomes.map(o => o.implied));
            return (
              <div key={market.id} className="space-y-2">
                <div className="flex items-center gap-2">
                  <h3 className="text-sm font-bold text-zinc-800 dark:text-zinc-200">{market.name}</h3>
                  <span className="ml-auto text-[10px] text-zinc-400 dark:text-zinc-600">Best available odds</span>
                </div>
                <div className="space-y-2">
                  {market.outcomes.map(o => (
                    <OddsBar key={o.name} outcome={o} isBest={o.implied === bestOdds} />
                  ))}
                </div>
              </div>
            );
          })}

          <p className="text-center text-[10px] text-zinc-400 dark:text-zinc-600 pt-2">
            Odds sourced from The Odds API · Best available across EU bookmakers · Always verify before betting
          </p>
        </div>
      </div>
    </div>
  );
}
