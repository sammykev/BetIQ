"use client";

import { useEffect, useState } from "react";
import { TrendingUp, Loader2, RefreshCw, AlertCircle } from "lucide-react";
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
  const color =
    edge >= 15 ? "bg-green-500 text-black" :
    edge >= 10 ? "bg-green-400/20 text-green-300 border border-green-400/30" :
                 "bg-yellow-400/20 text-yellow-300 border border-yellow-400/30";
  return (
    <span className={clsx("text-xs font-black px-2.5 py-0.5 rounded-full", color)}>
      +{edge}% edge
    </span>
  );
}

function ValueBetCard({ v, onClick }: { v: ValueBet; onClick?: () => void }) {
  const barModel   = Math.round(v.model_prob);
  const barImplied = Math.round(v.implied_prob);

  return (
    <div
      onClick={onClick}
      className={clsx(
        "bg-slate-900 border border-slate-700 rounded-xl p-4 flex flex-col gap-3",
        onClick && "cursor-pointer hover:border-green-500/40 hover:bg-slate-800/60 transition-all active:scale-[0.98]"
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-slate-400 truncate">{v.flag} {v.league_name}</span>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-[10px] text-slate-500">{v.date}{v.time && v.time !== "TBD" ? ` · ${v.time}` : ""}</span>
          <EdgeBadge edge={v.edge} />
        </div>
      </div>

      {/* Match */}
      <div>
        <p className="text-sm font-bold text-white leading-snug">{v.home} <span className="text-slate-500 font-normal">vs</span> {v.away}</p>
      </div>

      {/* Value pick */}
      <div className="bg-green-500/10 border border-green-500/25 rounded-lg px-3 py-2.5 flex items-center justify-between gap-3">
        <div>
          <p className="text-[10px] text-green-400/70 uppercase tracking-wide font-semibold mb-0.5">Value Pick</p>
          <p className="text-sm font-bold text-white">{v.value_label}</p>
        </div>
        <div className="text-right shrink-0">
          <p className="text-[10px] text-slate-400 mb-0.5">Odds</p>
          <p className="text-xl font-black text-green-400">{v.value_odds}</p>
        </div>
      </div>

      {/* Probability comparison */}
      <div className="space-y-1.5">
        <div className="flex justify-between text-[10px] text-slate-400">
          <span>BetIQ model</span>
          <span className="text-white font-semibold">{barModel}%</span>
        </div>
        <div className="h-1.5 bg-slate-800 rounded-full overflow-hidden">
          <div className="h-full bg-green-500 rounded-full" style={{ width: `${barModel}%` }} />
        </div>

        <div className="flex justify-between text-[10px] text-slate-400">
          <span>{v.bookie === "sportybet" ? "SportyBet" : "Bookie"} implied</span>
          <span className="text-slate-300 font-semibold">{barImplied}%</span>
        </div>
        <div className="h-1.5 bg-slate-800 rounded-full overflow-hidden">
          <div className="h-full bg-slate-500 rounded-full" style={{ width: `${barImplied}%` }} />
        </div>
      </div>

      <p className="text-[10px] text-slate-600">
        Overround: {v.overround}% · Model sees {v.model_prob - v.implied_prob > 0 ? "+" : ""}{(v.model_prob - v.implied_prob).toFixed(1)}% above fair odds
      </p>
    </div>
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
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <TrendingUp size={16} className="text-green-400" />
          <h2 className="text-sm font-bold text-slate-200">Value Bets</h2>
          {bets.length > 0 && (
            <span className="text-[10px] bg-green-500/20 text-green-300 border border-green-500/30 px-2 py-0.5 rounded-full font-semibold">
              {bets.length} found
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {lastFetch && (
            <span className="text-[10px] text-slate-500">
              Updated {lastFetch.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
            </span>
          )}
          <button
            onClick={load}
            disabled={loading}
            className="p-1.5 rounded-lg hover:bg-slate-800 text-slate-400 hover:text-white transition-colors disabled:opacity-40"
            title="Refresh"
          >
            <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
          </button>
        </div>
      </div>

      {/* Explainer */}
      <div className="bg-slate-800/50 border border-slate-700 rounded-xl p-3 flex gap-2.5">
        <AlertCircle size={14} className="text-blue-400 shrink-0 mt-0.5" />
        <p className="text-[11px] text-slate-400 leading-relaxed">
          Value bets are where BetIQ's model gives a higher probability than what the bookmaker's odds imply.
          A <span className="text-white font-semibold">+5% or more edge</span> suggests the odds are mispriced in your favour.
          Always bet responsibly.
        </p>
      </div>

      {/* Content */}
      {loading ? (
        <div className="flex items-center justify-center gap-2 py-12 text-slate-500">
          <Loader2 size={16} className="animate-spin" />
          <span className="text-sm">Fetching live odds from SportyBet…</span>
        </div>
      ) : error ? (
        <div className="text-center py-12 text-slate-500 space-y-2">
          <p className="text-sm">Couldn't load live odds right now.</p>
          <button onClick={load} className="text-xs text-blue-400 hover:underline">Try again</button>
        </div>
      ) : bets.length === 0 ? (
        <div className="text-center py-12 text-slate-500 space-y-2">
          <TrendingUp size={28} className="mx-auto mb-3 opacity-30" />
          <p className="text-sm">No value bets found right now.</p>
          <p className="text-xs text-slate-600">
            The model's picks are in line with the market, or live odds are unavailable.
          </p>
          <p className="text-xs text-blue-400/70 mt-2">
            Powered by The Odds API · <a href="https://the-odds-api.com" target="_blank" rel="noopener noreferrer" className="underline hover:text-blue-300">the-odds-api.com</a>
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
