"use client";

import { useEffect, useState } from "react";
import { TrendingUp, RefreshCw, Info, AlertTriangle } from "lucide-react";
import { TeamBadge } from "./PredictionCard";
import { CompetitionBadge } from "./CompetitionBadge";
import { kickoff } from "@/lib/matchTime";
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
  value_market_name?: string;  // SportyBet-priced value bets: which market
}

function ValueBetCard({ v, onClick }: { v: ValueBet; onClick?: () => void }) {
  const model   = Math.round(v.model_prob);
  const implied = Math.round(v.implied_prob);
  const big = v.edge >= 10;

  return (
    <article onClick={onClick} className={clsx("card overflow-hidden flex flex-col", onClick && "card-interactive")}>
      {/* Header strip */}
      <div className="flex items-center justify-between gap-2 px-4 py-2.5 border-b border-n-800/80">
        <span className="flex items-center gap-1.5 min-w-0">
          <CompetitionBadge name={v.league_name} fallbackEmoji={v.flag} size={13} />
          <span className="eyebrow truncate">{v.league_name}</span>
        </span>
        <span className="font-mono text-[11px] text-n-200 uppercase shrink-0">{kickoff(v.date, v.time)}</span>
      </div>

      <div className="flex-1 flex flex-col gap-3.5 p-4">
        {/* Fixture */}
        <div className="space-y-2">
          {[v.home, v.away].map(team => (
            <div key={team} className="flex items-center gap-3 min-w-0">
              <TeamBadge name={team} size={26} />
              <span className="font-semibold text-[15px] text-n-0 truncate">{team}</span>
            </div>
          ))}
        </div>

        {/* Model vs market */}
        <div className="space-y-2">
          {[
            { label: "Model", value: model, cls: "bg-accent", text: "text-accent" },
            { label: "Market", value: implied, cls: "bg-n-600", text: "text-n-400" },
          ].map(({ label, value, cls, text }) => (
            <div key={label} className="flex items-center gap-3">
              <span className="eyebrow w-14 shrink-0">{label}</span>
              <div className="flex-1 h-1.5 bg-n-800 rounded-full overflow-hidden">
                <div className={clsx("h-full rounded-full", cls)} style={{ width: `${value}%` }} />
              </div>
              <span className={clsx("font-mono text-xs font-bold w-9 text-right", text)}>{value}%</span>
            </div>
          ))}
        </div>

        {/* Value ticket */}
        <div className={clsx(
          "relative mt-auto flex items-center gap-3 rounded-xl px-3.5 py-3",
          big ? "bg-brand-400 text-ink" : "bg-brand-400/[0.06] border border-brand-400/30 text-n-0"
        )}>
          <span className="absolute -top-2.5 right-3 font-mono text-[10px] font-bold bg-ink text-brand-400 border border-brand-400/70 rounded-md px-1.5 py-0.5">
            +{v.edge}% EDGE
          </span>
          <div className="min-w-0 flex-1">
            <p className={clsx("font-display font-bold text-[11px] uppercase tracking-[0.14em]", big ? "text-ink/60" : "text-n-400")}>{v.value_market_name ?? "Value pick"}</p>
            <p className="font-display font-extrabold text-xl uppercase leading-tight truncate">{v.value_label}</p>
          </div>
          <div className="text-right shrink-0">
            <p className={clsx("font-display font-bold text-[11px] uppercase tracking-[0.14em]", big ? "text-ink/60" : "text-n-400")}>{v.bookie}</p>
            <p className={clsx("font-display font-extrabold text-[32px] leading-none tnum", big ? "text-ink" : "text-accent")}>
              {v.value_odds.toFixed(2)}
            </p>
          </div>
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

  const best = bets.reduce((m, b) => Math.max(m, b.edge), 0);

  return (
    <div className="space-y-5">
      {/* Summary strip */}
      <div className="flex items-stretch gap-3 flex-wrap">
        <div className="card px-4 py-3 min-w-[120px]">
          <p className="eyebrow">Found</p>
          <p className="font-display font-extrabold text-3xl text-n-0 leading-none mt-1 tnum">{loading ? "–" : bets.length}</p>
        </div>
        <div className="card px-4 py-3 min-w-[120px]">
          <p className="eyebrow">Best edge</p>
          <p className="font-display font-extrabold text-3xl text-accent leading-none mt-1 tnum">{loading || !best ? "–" : `+${best}%`}</p>
        </div>
        <div className="flex-1 min-w-[240px] card px-4 py-3 flex gap-2.5 items-start">
          <Info size={14} className="text-info shrink-0 mt-0.5" />
          <p className="text-xs text-n-400 leading-relaxed">
            A value bet is where the model rates an outcome more likely than the bookmaker&apos;s odds imply.
            An edge of <span className="text-n-0 font-semibold">+5% or more</span> suggests the price is in your favour.
          </p>
        </div>
        <div className="flex items-center gap-3 ml-auto">
          {lastFetch && (
            <span className="text-[11px] text-n-500">
              Updated {lastFetch.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
            </span>
          )}
          <button onClick={load} disabled={loading} className="btn-secondary !px-3 !py-1.5 !text-xs !rounded-lg" title="Refresh">
            <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
            Refresh
          </button>
        </div>
      </div>

      {/* Content */}
      {loading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="card p-4 space-y-3.5">
              <div className="skeleton h-3 w-1/2" />
              <div className="flex items-center gap-3"><div className="skeleton w-[26px] h-[26px] shrink-0" /><div className="skeleton h-4 flex-1" /></div>
              <div className="flex items-center gap-3"><div className="skeleton w-[26px] h-[26px] shrink-0" /><div className="skeleton h-4 flex-1" /></div>
              <div className="skeleton h-1.5 w-full" />
              <div className="skeleton h-[64px] w-full !rounded-xl" />
            </div>
          ))}
        </div>
      ) : error ? (
        <div className="card border-dashed text-center py-16 px-6 space-y-3">
          <AlertTriangle size={22} className="mx-auto text-danger" />
          <p className="font-display font-bold text-xl uppercase text-n-0">Live odds unavailable</p>
          <p className="text-sm text-n-400">We couldn&apos;t load bookmaker prices right now.</p>
          <button onClick={load} className="btn-secondary">Try again</button>
        </div>
      ) : bets.length === 0 ? (
        <div className="card border-dashed text-center py-16 px-6 space-y-3">
          <TrendingUp size={22} className="mx-auto text-n-500" />
          <p className="font-display font-bold text-xl uppercase text-n-0">No value right now</p>
          <p className="text-sm text-n-400 max-w-sm mx-auto">
            The model&apos;s picks are in line with the market, or live odds aren&apos;t available yet.
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
