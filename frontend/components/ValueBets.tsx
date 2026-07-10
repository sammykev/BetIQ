"use client";

import { useEffect, useState } from "react";
import { TrendingUp, Loader2, RefreshCw, Info } from "lucide-react";
import { TeamBadge } from "./PredictionCard";
import { CompetitionBadge } from "./CompetitionBadge";
import clsx from "clsx";

const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

interface ValueBet {
  home: string;
  away: string;
  date: string;
  time: string;
  league_name: string;
  flag: string;
  value_outcome: string;
  value_label: string;
  value_odds: number;
  model_prob: number;
  implied_prob: number;
  edge: number;
  overround: number;
  bookie: string;
}

function EdgeBadge({ edge }: { edge: number }) {
  const style =
    edge >= 15 ? "bg-brand-600 text-white" :
    edge >= 10 ? "bg-brand-50 dark:bg-brand-900/30 text-brand-700 dark:text-brand-400 border border-brand-200 dark:border-brand-800" :
                 "bg-amber-50 dark:bg-amber-500/10 text-amber-700 dark:text-amber-400 border border-amber-200 dark:border-amber-500/30";
  return (
    <span className={clsx("tnum text-xs font-black px-2.5 py-1 rounded-full shrink-0", style)}>
      +{edge}%
    </span>
  );
}

function ValueBetCard({ v, onClick }: { v: ValueBet; onClick?: () => void }) {
  const barModel   = Math.round(v.model_prob);
  const barImplied = Math.round(v.implied_prob);

  return (
    <article onClick={onClick} className={clsx("card p-4 flex flex-col gap-3", onClick && "card-interactive")}>
      {/* Header */}
      <div className="flex items-center justify-between gap-2">
        <p className="flex items-center gap-1 text-[10px] text-zinc-400 dark:text-zinc-500 font-semibold uppercase tracking-wide truncate">
          <CompetitionBadge name={v.league_name} fallbackEmoji={v.flag} size={11} />
          <span className="truncate">
            {v.league_name} · {v.date}{v.time && v.time !== "TBD" ? ` ${v.time}` : ""}
          </span>
        </p>
        <EdgeBadge edge={v.edge} />
      </div>

      {/* Teams */}
      <div className="flex items-center gap-2">
        <TeamBadge name={v.home} size={24} />
        <span className="font-semibold text-sm text-zinc-900 dark:text-white truncate">{v.home}</span>
        <span className="text-zinc-300 dark:text-zinc-600 text-xs font-medium shrink-0">vs</span>
        <TeamBadge name={v.away} size={24} />
        <span className="font-semibold text-sm text-zinc-900 dark:text-white truncate">{v.away}</span>
      </div>

      {/* Value pick banner */}
      <div className="bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-100 dark:border-zinc-800 rounded-xl px-3 py-2.5 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[10px] text-brand-700 dark:text-brand-400 uppercase tracking-wider font-bold">Value pick</p>
          <p className="text-sm font-bold text-zinc-900 dark:text-white truncate">{v.value_label}</p>
          <p className="text-[10px] text-zinc-400 dark:text-zinc-500 mt-0.5">via {v.bookie}</p>
        </div>
        <p className="tnum text-2xl font-black text-brand-600 dark:text-brand-400 shrink-0">{v.value_odds}</p>
      </div>

      {/* Model vs market bars */}
      <div className="space-y-1.5">
        <div className="flex justify-between text-[11px]">
          <span className="text-zinc-400 dark:text-zinc-500 font-medium">Model probability</span>
          <span className="tnum text-zinc-900 dark:text-white font-bold">{barModel}%</span>
        </div>
        <div className="h-1.5 bg-zinc-100 dark:bg-zinc-800 rounded-full overflow-hidden">
          <div className="h-full bg-brand-500 rounded-full" style={{ width: `${barModel}%` }} />
        </div>
        <div className="flex justify-between text-[11px]">
          <span className="text-zinc-400 dark:text-zinc-500 font-medium">Market implied</span>
          <span className="tnum text-zinc-500 dark:text-zinc-400 font-semibold">{barImplied}%</span>
        </div>
        <div className="h-1.5 bg-zinc-100 dark:bg-zinc-800 rounded-full overflow-hidden">
          <div className="h-full bg-zinc-300 dark:bg-zinc-600 rounded-full" style={{ width: `${barImplied}%` }} />
        </div>
      </div>
    </article>
  );
}

interface Props {
  onMatchClick?: (home: string, away: string) => void;
}

export function ValueBets({ onMatchClick }: Props) {
  const [bets, setBets]     = useState<ValueBet[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]   = useState(false);
  const [lastFetch, setLastFetch] = useState<Date | null>(null);

  const load = async () => {
    setLoading(true);
    setError(false);
    try {
      const res = await fetch(`${API}/api/value-bets`, { cache: "no-store" });
      if (!res.ok) throw new Error();
      const data = await res.json();
      setBets(Array.isArray(data) ? data : []);
      setLastFetch(new Date());
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  return (
    <div className="space-y-4">
      {/* Header row */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          {bets.length > 0 && (
            <span className="tnum text-[11px] bg-brand-50 dark:bg-brand-900/30 text-brand-700 dark:text-brand-400 border border-brand-200 dark:border-brand-800 px-2.5 py-0.5 rounded-full font-bold">
              {bets.length} found
            </span>
          )}
          {lastFetch && (
            <span className="text-[11px] text-zinc-400 dark:text-zinc-500">
              Updated {lastFetch.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
            </span>
          )}
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="btn-secondary !px-3 !py-1.5 !text-xs"
          title="Refresh"
        >
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>

      {/* Explainer */}
      <div className="card px-4 py-3 flex gap-2.5">
        <Info size={14} className="text-sky-500 shrink-0 mt-0.5" />
        <p className="text-xs text-zinc-500 dark:text-zinc-400 leading-relaxed">
          Value bets are where BetIQ&apos;s model gives a higher probability than the bookmaker&apos;s odds imply.
          A <span className="text-zinc-900 dark:text-white font-semibold">+5% or more edge</span> suggests
          the odds are mispriced in your favour. Always bet responsibly.
        </p>
      </div>

      {/* Content */}
      {loading ? (
        <div className="flex items-center justify-center gap-2 py-16 text-zinc-400 dark:text-zinc-500">
          <Loader2 size={16} className="animate-spin" />
          <span className="text-sm font-medium">Fetching live odds…</span>
        </div>
      ) : error ? (
        <div className="text-center py-16 text-zinc-400 dark:text-zinc-500 space-y-2">
          <p className="text-sm font-medium">Couldn&apos;t load live odds right now.</p>
          <button onClick={load} className="text-xs text-sky-600 dark:text-sky-400 hover:underline font-medium">Try again</button>
        </div>
      ) : bets.length === 0 ? (
        <div className="text-center py-16 text-zinc-400 dark:text-zinc-500 space-y-2">
          <TrendingUp size={28} className="mx-auto mb-3 opacity-30" />
          <p className="text-sm font-medium">No value bets found right now.</p>
          <p className="text-xs text-zinc-400 dark:text-zinc-600">
            The model&apos;s picks are in line with the market, or live odds are unavailable.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {bets.map((v, i) => (
            <ValueBetCard
              key={i}
              v={v}
              onClick={onMatchClick ? () => onMatchClick(v.home, v.away) : undefined}
            />
          ))}
        </div>
      )}
    </div>
  );
}
