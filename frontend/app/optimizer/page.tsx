"use client";

import { useState } from "react";
import clsx from "clsx";
import { useUser } from "@clerk/nextjs";
import { Check, Copy, Loader2, Sparkles, Ticket, AlertTriangle, ExternalLink } from "lucide-react";
import { AppShell } from "@/components/shell/AppShell";
import { PageHeader } from "@/components/shell/PageHeader";
import { useBetSlip } from "@/lib/useBetSlip";
import { useAuthedFetch } from "@/lib/useAuthedFetch";
import type { SlipSelection } from "@/lib/slip";

const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

// backend/optimizer.py: the slip with the best win chance whose total odds
// land in the target range.

interface OptPick {
  home: string; away: string; date: string; time: string; league: string;
  market: string; market_name: string; code: string; label: string;
  prob: number; odds: number; odds_source: "sportybet" | "bookmaker" | "estimated";
}
interface OptResult {
  picks?: OptPick[]; games?: number; total_odds?: number; win_chance?: number;
  within_target?: boolean; estimated_prices?: number; matches_considered?: number;
  target?: [number, number]; error?: string;
}
interface BookResult { code: string | null; share_url: string | null; total_odds: number | null; error: string | null;
  picks: { key: string; status: string; reason?: string }[] }

const TARGETS: { label: string; lo: number; hi: number }[] = [
  { label: "2–3x", lo: 2, hi: 3 },
  { label: "5–10x", lo: 5, hi: 10 },
  { label: "20–50x", lo: 20, hi: 50 },
  { label: "100–300x", lo: 100, hi: 300 },
  { label: "1K–5K", lo: 1000, hi: 5000 },
  { label: "5K–15K", lo: 5000, hi: 15000 },
];
const CONFIDENCE = [0.6, 0.7, 0.8];
const DAYS = [{ label: "Today", n: 1 }, { label: "2 days", n: 2 }, { label: "3 days", n: 3 }, { label: "Week", n: 7 }];
const MARKETS = [
  { id: "1x2", label: "Result" },
  { id: "double_chance", label: "Double chance" },
  { id: "goals_ou", label: "Goals" },
];

const odds = (x: number) => x.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pct = (x: number) => (x >= 0.1 ? `${Math.round(x * 100)}%` : x >= 0.001 ? `${(x * 100).toFixed(1)}%` : "<0.1%");

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" onClick={onClick} className={clsx("chip", active ? "chip-active" : "chip-idle")}>
      {children}
    </button>
  );
}

function Setting({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <p className="eyebrow">{label}</p>
      <div className="flex flex-wrap gap-1.5">{children}</div>
    </div>
  );
}

export default function OptimizerPage() {
  const { user } = useUser();
  const authFetch = useAuthedFetch();
  const slip = useBetSlip();

  const [target, setTarget] = useState(TARGETS[1]);
  const [custom, setCustom] = useState({ lo: "", hi: "" });
  const [minProb, setMinProb] = useState(0.6);
  const [days, setDays] = useState(3);
  const [maxGames, setMaxGames] = useState(30);
  const [markets, setMarkets] = useState<string[]>(MARKETS.map(m => m.id));
  const [bookableOnly, setBookableOnly] = useState(false);

  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<OptResult | null>(null);
  const [booking, setBooking] = useState(false);
  const [booked, setBooked] = useState<BookResult | null>(null);
  const [copied, setCopied] = useState(false);

  const usingCustom = custom.lo !== "" || custom.hi !== "";
  const lo = usingCustom ? Number(custom.lo) : target.lo;
  const hi = usingCustom ? Number(custom.hi) : target.hi;
  const validTarget = lo >= 1.01 && hi >= lo;

  const selections: SlipSelection[] = (result?.picks ?? []).map(p => ({
    home: p.home, away: p.away, date: p.date, time: p.time, league: p.league,
    market: p.market, marketName: p.market_name, code: p.code, label: p.label, prob: p.prob,
  }));

  const run = async () => {
    setBusy(true); setResult(null); setBooked(null);
    try {
      const res = await fetch(`${API}/api/optimizer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ min_odds: lo, max_odds: hi, min_prob: minProb, days, max_games: maxGames,
                               markets, bookable_only: bookableOnly }),
      });
      const data = await res.json();
      setResult(res.ok ? data : { error: data?.detail || "The optimizer couldn't run. Try again." });
    } catch {
      setResult({ error: "Couldn't reach the server. Try again in a moment." });
    } finally { setBusy(false); }
  };

  const book = async () => {
    setBooking(true); setBooked(null);
    try {
      const res = await fetch(`${API}/api/booking/convert`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ platform: "sportybet", selections }),
      });
      const data = await res.json();
      if (!res.ok || !Array.isArray(data?.picks)) throw new Error();
      setBooked(data);
      if (data.code && user?.id) {
        authFetch(`${API}/api/user/codes`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ uid: user.id, entry: {
            code: data.code, total_odds: data.total_odds, date: new Date().toISOString().slice(0, 10),
            games: selections.map(s => ({ game: `${s.home} vs ${s.away}`, tip: s.label, odds: null })),
          } }),
        }).catch(() => {});
      }
    } catch {
      setBooked({ code: null, share_url: null, total_odds: null, picks: [],
                  error: "SportyBet didn't return a code. Try again in a minute." });
    } finally { setBooking(false); }
  };

  const toSlip = () => {
    slip.clear();
    slip.addMany(selections);
    slip.setOpen(true);
  };

  const copy = async (code: string) => {
    try { await navigator.clipboard.writeText(code); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* ignore */ }
  };

  const failedPicks = booked?.picks.filter(p => p.status !== "booked") ?? [];

  return (
    <AppShell>
      <div className="space-y-6 animate-fade-in">
        <PageHeader
          eyebrow="Strategy engine"
          title="Optimizer"
          description="Pick a target total. BetIQ searches every upcoming match for the slip that reaches it with the best chance of every pick winning."
        />

        {/* Settings */}
        <section className="card p-5 space-y-5">
          <Setting label="Target odds">
            {TARGETS.map(t => (
              <Chip key={t.label} active={!usingCustom && target.label === t.label}
                onClick={() => { setTarget(t); setCustom({ lo: "", hi: "" }); }}>{t.label}</Chip>
            ))}
            <span className="flex items-center gap-1.5 text-xs text-n-400">
              <input inputMode="decimal" placeholder="min" value={custom.lo}
                onChange={e => setCustom(c => ({ ...c, lo: e.target.value }))}
                className="w-20 rounded-lg bg-surface-sunken border border-n-800 px-2 py-1.5 text-n-0 tnum outline-none focus:border-accent" />
              –
              <input inputMode="decimal" placeholder="max" value={custom.hi}
                onChange={e => setCustom(c => ({ ...c, hi: e.target.value }))}
                className="w-20 rounded-lg bg-surface-sunken border border-n-800 px-2 py-1.5 text-n-0 tnum outline-none focus:border-accent" />
              x
            </span>
          </Setting>

          <div className="grid gap-5 sm:grid-cols-2">
            <Setting label="Each pick at least">
              {CONFIDENCE.map(c => (
                <Chip key={c} active={minProb === c} onClick={() => setMinProb(c)}>{Math.round(c * 100)}% likely</Chip>
              ))}
            </Setting>
            <Setting label="Matches from">
              {DAYS.map(d => <Chip key={d.n} active={days === d.n} onClick={() => setDays(d.n)}>{d.label}</Chip>)}
            </Setting>
            <Setting label="Markets">
              {MARKETS.map(m => (
                <Chip key={m.id} active={markets.includes(m.id)}
                  onClick={() => setMarkets(ms => ms.includes(m.id) ? (ms.length > 1 ? ms.filter(x => x !== m.id) : ms) : [...ms, m.id])}>
                  {m.label}
                </Chip>
              ))}
            </Setting>
            <div className="space-y-2">
              <p className="eyebrow">At most {maxGames} games</p>
              <input type="range" min={1} max={30} value={maxGames} onChange={e => setMaxGames(Number(e.target.value))}
                className="w-full accent-[rgb(var(--accent))]" aria-label="Maximum games" />
            </div>
          </div>

          <label className="flex items-center gap-2 text-sm text-n-300 cursor-pointer select-none">
            <input type="checkbox" checked={bookableOnly} onChange={e => setBookableOnly(e.target.checked)} className="accent-[rgb(var(--accent))]" />
            Only matches already linked to SportyBet (instant booking)
          </label>

          <button onClick={run} disabled={busy || !validTarget}
            className="w-full sm:w-auto flex items-center justify-center gap-2 rounded-xl bg-brand-400 hover:bg-brand-300 text-ink font-bold px-6 py-3 disabled:opacity-50">
            {busy ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
            {busy ? "Optimizing…" : `Optimize for ${odds(lo || 0)}–${odds(hi || 0)}x`}
          </button>
        </section>

        {result?.error && (
          <div className="card p-4 flex items-start gap-2 text-sm text-warn">
            <AlertTriangle size={16} className="shrink-0 mt-0.5" /> {result.error}
          </div>
        )}

        {result?.picks && (
          <section className="space-y-4">
            <div className="card p-5 space-y-4">
              <div className="grid grid-cols-3 gap-3 text-center">
                <div className="rounded-xl bg-surface-sunken px-3 py-3">
                  <p className="eyebrow">Total odds</p>
                  <p className="font-display font-extrabold text-2xl sm:text-3xl text-n-0 tnum mt-1">{odds(result.total_odds!)}x</p>
                </div>
                <div className="rounded-xl bg-surface-sunken px-3 py-3">
                  <p className="eyebrow">Games</p>
                  <p className="font-display font-extrabold text-2xl sm:text-3xl text-n-0 tnum mt-1">{result.games}</p>
                </div>
                <div className="rounded-xl bg-surface-sunken px-3 py-3">
                  <p className="eyebrow">All win</p>
                  <p className="font-display font-extrabold text-2xl sm:text-3xl text-accent tnum mt-1">{pct(result.win_chance!)}</p>
                </div>
              </div>
              <p className={clsx("text-xs flex items-center gap-1.5", result.within_target ? "text-accent" : "text-warn")}>
                {result.within_target ? <Check size={13} /> : <AlertTriangle size={13} />}
                {result.within_target ? "Within" : "Closest to"} target {odds(result.target![0])}–{odds(result.target![1])}x
                · best of {result.matches_considered} matches
              </p>
              <p className="text-[11px] text-n-500">
                &quot;All win&quot; is the model&apos;s chance every pick wins, treating matches as independent.
                {result.estimated_prices! > 0 &&
                  ` ${result.estimated_prices} price${result.estimated_prices === 1 ? " is" : "s are"} estimated from our probabilities; SportyBet's own odds may differ.`}
              </p>

              {booked?.code ? (
                <div className="rounded-xl border border-accent/40 bg-surface-sunken p-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <p className="eyebrow">Booking code</p>
                    <span className="text-[11px] font-bold text-accent">SportyBet</span>
                  </div>
                  <p className="font-mono text-3xl font-bold tracking-[0.2em] text-n-0">{booked.code}</p>
                  <div className="flex flex-wrap gap-2">
                    <button onClick={() => copy(booked.code!)}
                      className="flex items-center gap-1.5 rounded-lg bg-brand-400 hover:bg-brand-300 text-ink font-bold px-4 py-2 text-sm">
                      {copied ? <Check size={14} /> : <Copy size={14} />} {copied ? "Copied" : "Copy code"}
                    </button>
                    {booked.share_url && (
                      <a href={booked.share_url} target="_blank" rel="noreferrer"
                        className="flex items-center gap-1.5 rounded-lg border border-n-700 text-n-200 px-4 py-2 text-sm">
                        <ExternalLink size={14} /> Open on SportyBet
                      </a>
                    )}
                  </div>
                  {failedPicks.length > 0 && (
                    <p className="text-xs text-warn">{failedPicks.length} pick{failedPicks.length === 1 ? " wasn't" : "s weren't"} included: {failedPicks[0].reason}</p>
                  )}
                </div>
              ) : (
                <div className="flex flex-wrap gap-2">
                  <button onClick={book} disabled={booking}
                    className="flex-1 min-w-[12rem] flex items-center justify-center gap-2 rounded-xl bg-brand-400 hover:bg-brand-300 text-ink font-bold px-5 py-3 disabled:opacity-50">
                    {booking ? <Loader2 size={16} className="animate-spin" /> : <Ticket size={16} />}
                    {booking ? "Booking…" : "Get SportyBet code"}
                  </button>
                  <button onClick={toSlip}
                    className="flex-1 min-w-[12rem] rounded-xl border border-n-700 text-n-200 font-semibold px-5 py-3">
                    Put in my bet slip
                  </button>
                </div>
              )}
              {booked?.error && !booked.code && <p className="text-xs text-danger">{booked.error}</p>}
            </div>

            <div className="card divide-y divide-n-800">
              <p className="px-5 py-3 text-sm font-bold text-n-0">Optimized slip</p>
              {result.picks.map(p => (
                <div key={`${p.home}-${p.away}-${p.date}`} className="px-5 py-3 flex items-center gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="text-[11px] text-n-500 truncate">{p.league} · {p.date} {p.time}</p>
                    <p className="text-sm text-n-0 font-semibold truncate">{p.home} vs {p.away}</p>
                    <p className="text-xs text-n-400">{p.market_name}: <span className="text-n-200">{p.label}</span></p>
                  </div>
                  <div className="text-right shrink-0">
                    <p className="text-sm font-bold text-n-0 tnum">{odds(p.odds)}</p>
                    <p className="text-[11px] text-n-500 tnum">
                      {pct(p.prob)} · {p.odds_source === "sportybet" ? "SportyBet" : p.odds_source === "bookmaker" ? "market" : "est."}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}
      </div>
    </AppShell>
  );
}
