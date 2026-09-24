"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import { useUser } from "@clerk/nextjs";
import { Check, Copy, Loader2, Sparkles, Ticket, AlertTriangle, ExternalLink, Link2 } from "lucide-react";
import { AppShell } from "@/components/shell/AppShell";
import { PageHeader } from "@/components/shell/PageHeader";
import { CodeCheck } from "@/components/CodeCheck";
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
  target?: [number, number]; target_odds?: number; error?: string;
}
interface BookResult { code: string | null; share_url: string | null; total_odds: number | null; error: string | null;
  picks: { key: string; status: string; reason?: string }[] }

// Target odds: a slider on a log scale (each step is the same % change),
// quick jumps, and how far off the slip's total may land
const MIN_TARGET = 1.5;
const MAX_TARGET = 100_000;
const SLIDER_STEPS = 1000;
const QUICK = [2, 5, 10, 50, 100, 1000, 10_000];
const TOLERANCES = [0.02, 0.05, 0.1];

const toSlider = (t: number) =>
  Math.round((Math.log(t / MIN_TARGET) / Math.log(MAX_TARGET / MIN_TARGET)) * SLIDER_STEPS);
function fromSlider(pos: number) {
  const t = MIN_TARGET * Math.pow(MAX_TARGET / MIN_TARGET, pos / SLIDER_STEPS);
  // Round to a number people would type: 2.35, 47.5, 1,250, 38,000
  const mag = Math.pow(10, Math.floor(Math.log10(t)) - 2);
  return Math.max(MIN_TARGET, Math.round(t / mag) * mag);
}
function offBy(total: number, target: number) {
  const d = (total / target - 1) * 100;
  return Math.abs(d) < 0.05 ? "exact" : `${d > 0 ? "+" : "−"}${Math.abs(d).toFixed(1)}%`;
}
const short = (x: number) => (x >= 1000 ? `${x / 1000}K` : `${x}x`);
const CONFIDENCE = [0.5, 0.6, 0.7, 0.8];
const DAYS = [{ label: "Today", n: 1 }, { label: "2 days", n: 2 }, { label: "3 days", n: 3 }, { label: "Week", n: 7 }];
// Each chip covers one or more backend markets (optimizer.py _PICKS). `needs`
// are the booking_slip.VERIFIED markets SportyBet must confirm before codes
// can include them.
const MARKETS: { id: string; label: string; ids: string[]; needs?: string[] }[] = [
  { id: "1x2", label: "Result", ids: ["1x2"] },
  { id: "double_chance", label: "Double chance", ids: ["double_chance"] },
  { id: "goals_ou", label: "Goals", ids: ["goals_ou"] },
  { id: "btts", label: "Both score", ids: ["btts"] },
  { id: "team_goals", label: "Team goals", ids: ["home_goals_ou", "away_goals_ou"],
    needs: ["home_goals_ou", "away_goals_ou"] },
  { id: "dc_goals", label: "Double chance + goals", ids: ["dc_goals"], needs: ["dc_goals"] },
  { id: "clean_sheet", label: "Clean sheet / to nil", ids: ["clean_sheet", "win_to_nil"],
    needs: ["clean_sheet:H", "clean_sheet:A", "win_to_nil:H", "win_to_nil:A"] },
  { id: "handicap", label: "Handicap", ids: ["handicap"], needs: ["handicap"] },
  { id: "corners", label: "Corners", ids: ["corners_ou"], needs: ["corners_ou"] },
  { id: "team_corners", label: "Team corners", ids: ["home_corners_ou", "away_corners_ou", "corners_1x2"],
    needs: ["home_corners_ou", "away_corners_ou", "corners_1x2"] },
  { id: "cards", label: "Cards", ids: ["cards_ou"], needs: ["cards_ou"] },
];
// Cards are SportyBet's "Total bookings": yellow 1, red 2

// /api/sportybet/status — the last automatic linking run
interface LinkStatus {
  at: string | null; trigger: string | null; linked: number; predictions: number;
  markets: Record<string, boolean>;
}
const TRIGGERS: Record<string, string> = {
  startup: "after the latest deploy", pipeline: "after new predictions", schedule: "on the 30-minute check",
  manual: "by an admin", international: "after new international fixtures",
};

function ago(iso: string) {
  const min = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  return min < 1 ? "just now" : min < 60 ? `${min} min ago` : `${Math.round(min / 60)}h ago`;
}

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

  const [mode, setMode] = useState<"build" | "code">("build");
  const [targetOdds, setTargetOdds] = useState(10);
  const [typed, setTyped] = useState("10");
  const [tolerance, setTolerance] = useState(0.05);
  const [minProb, setMinProb] = useState(0.6);
  const [days, setDays] = useState(3);
  const [maxGames, setMaxGames] = useState(30);
  const [markets, setMarkets] = useState<string[]>(MARKETS.map(m => m.id));
  const confirmed = (m: (typeof MARKETS)[number], s: LinkStatus | null) =>
    !m.needs || !s || m.needs.every(k => s.markets?.[k]);
  const [link, setLink] = useState<LinkStatus | null>(null);

  useEffect(() => {
    fetch(`${API}/api/sportybet/status`)
      .then(r => (r.ok ? r.json() : null))
      .then((s: LinkStatus | null) => {
        if (!s) return;
        setLink(s);
        // Leave out corners/cards while SportyBet codes can't take them
        setMarkets(ms => ms.filter(id => confirmed(MARKETS.find(m => m.id === id)!, s)));
      })
      .catch(() => {});
  }, []);
  const unconfirmed = MARKETS.filter(m => !confirmed(m, link));
  // On by default: SportyBet lists many matches only days before kick-off,
  // and codes can only include matches it lists
  const [bookableOnly, setBookableOnly] = useState(true);

  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<OptResult | null>(null);
  const [booking, setBooking] = useState(false);
  const [booked, setBooked] = useState<BookResult | null>(null);
  const [copied, setCopied] = useState(false);

  const setTarget = (t: number) => { setTargetOdds(t); setTyped(String(Number(t.toFixed(2)))); };
  const lo = Math.max(1.01, targetOdds * (1 - tolerance));
  const hi = targetOdds * (1 + tolerance);
  const validTarget = targetOdds >= MIN_TARGET && targetOdds <= MAX_TARGET;

  const selections: SlipSelection[] = (result?.picks ?? []).map(p => ({
    home: p.home, away: p.away, date: p.date, time: p.time, league: p.league,
    market: p.market, marketName: p.market_name, code: p.code, label: p.label, prob: p.prob,
  }));

  const run = async (bookable = bookableOnly) => {
    setBusy(true); setResult(null); setBooked(null);
    try {
      const res = await fetch(`${API}/api/optimizer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_odds: targetOdds, min_odds: lo, max_odds: hi, min_prob: minProb, days, max_games: maxGames,
                               markets: MARKETS.filter(m => markets.includes(m.id)).flatMap(m => m.ids),
                               bookable_only: bookable }),
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
          description="Build a slip for the odds you want, or paste a SportyBet code and we'll rate it and make it more likely to win."
        />

        <div className="flex gap-1.5" role="tablist" aria-label="Optimizer mode">
          {([["build", "Build a slip"], ["code", "Check my SportyBet code"]] as const).map(([id, label]) => (
            <button key={id} role="tab" aria-selected={mode === id} onClick={() => setMode(id)}
              className={clsx("chip", mode === id ? "chip-active" : "chip-idle")}>{label}</button>
          ))}
        </div>

        {mode === "code" ? <CodeCheck /> : <>
        {/* Settings */}
        <section className="card p-5 space-y-5">
          <div className="space-y-3">
            <div className="flex items-end justify-between gap-3">
              <div>
                <p className="eyebrow">Target odds</p>
                <p className="font-display font-extrabold text-3xl text-n-0 tnum mt-1">
                  {odds(targetOdds)}<span className="text-n-400 text-xl">x</span>
                </p>
              </div>
              <label className="flex items-center gap-1.5 text-xs text-n-400">
                Exact
                <input inputMode="decimal" value={typed} aria-label="Exact target odds"
                  onChange={e => {
                    setTyped(e.target.value);
                    const v = Number(e.target.value.replace(/,/g, ""));
                    if (v >= MIN_TARGET && v <= MAX_TARGET) setTargetOdds(v);
                  }}
                  className="w-24 rounded-lg bg-surface-sunken border border-n-800 px-2 py-1.5 text-n-0 tnum outline-none focus:border-accent" />
              </label>
            </div>
            <input type="range" min={0} max={SLIDER_STEPS} value={toSlider(targetOdds)}
              onChange={e => setTarget(fromSlider(Number(e.target.value)))}
              className="w-full accent-[rgb(var(--accent))]" aria-label="Target odds" />
            <div className="flex flex-wrap gap-1.5">
              {QUICK.map(q => <Chip key={q} active={targetOdds === q} onClick={() => setTarget(q)}>{short(q)}</Chip>)}
            </div>
            <Setting label="Land within">
              {TOLERANCES.map(t => (
                <Chip key={t} active={tolerance === t} onClick={() => setTolerance(t)}>±{Math.round(t * 100)}%</Chip>
              ))}
              <span className="self-center text-xs text-n-500 tnum">{odds(lo)}–{odds(hi)}x</span>
            </Setting>
          </div>

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
              {unconfirmed.length > 0 && (
                <p className="basis-full text-[11px] text-n-500">
                  {unconfirmed.map(m => m.label).join(" & ")}: SportyBet hasn&apos;t confirmed these markets yet, so
                  codes can&apos;t include them. Untick &quot;Only matches SportyBet lists&quot; to use them anyway.
                </p>
              )}
            </Setting>
            <div className="space-y-2">
              <p className="eyebrow">At most {maxGames} games</p>
              <input type="range" min={1} max={30} value={maxGames} onChange={e => setMaxGames(Number(e.target.value))}
                className="w-full accent-[rgb(var(--accent))]" aria-label="Maximum games" />
            </div>
          </div>

          <label className="flex items-center gap-2 text-sm text-n-300 cursor-pointer select-none">
            <input type="checkbox" checked={bookableOnly} onChange={e => setBookableOnly(e.target.checked)} className="accent-[rgb(var(--accent))]" />
            Only matches SportyBet lists right now (so the code books every pick)
          </label>
          {link?.at && (
            <p className="flex items-center gap-1.5 text-xs text-n-400 -mt-2">
              <Link2 size={13} className="text-accent shrink-0" />
              <span>
                <span className="text-n-0 font-semibold tnum">{link.linked}</span> of {link.predictions} upcoming
                matches bookable on SportyBet · checked {ago(link.at)}
                {link.trigger && TRIGGERS[link.trigger] ? ` ${TRIGGERS[link.trigger]}` : ""}
              </span>
            </p>
          )}

          <button onClick={() => run()} disabled={busy || !validTarget}
            className="w-full sm:w-auto flex items-center justify-center gap-2 rounded-xl bg-brand-400 hover:bg-brand-300 text-ink font-bold px-6 py-3 disabled:opacity-50">
            {busy ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
            {busy ? "Optimizing…" : `Build a ${odds(targetOdds)}x slip`}
          </button>
        </section>

        {result?.error && (
          <div className="card p-4 flex items-start gap-2 text-sm text-warn">
            <AlertTriangle size={16} className="shrink-0 mt-0.5" />
            <div className="space-y-2">
              <p>{result.error}</p>
              {bookableOnly && result.matches_considered === 0 && /SportyBet/.test(result.error) && (
                <button onClick={() => { setBookableOnly(false); run(false); }}
                  className="chip chip-idle">Try all matches, not just SportyBet-listed ones</button>
              )}
            </div>
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
                {result.within_target ? "On target" : "Closest to target"}: {odds(result.total_odds!)}x vs {odds(result.target_odds ?? targetOdds)}x
                ({offBy(result.total_odds!, result.target_odds ?? targetOdds)}) · best of {result.matches_considered} matches
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
        </>}
      </div>
    </AppShell>
  );
}
