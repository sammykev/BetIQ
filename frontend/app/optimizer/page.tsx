"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import clsx from "clsx";
import { useUser } from "@clerk/nextjs";
import { Check, ChevronDown, Copy, Loader2, Sparkles, Ticket, AlertTriangle, ExternalLink, Link2 } from "lucide-react";
import { AppShell } from "@/components/shell/AppShell";
import { PageHeader } from "@/components/shell/PageHeader";
import { CodeCheck } from "@/components/CodeCheck";
import { useBetSlip } from "@/lib/useBetSlip";
import { useAuthedFetch } from "@/lib/useAuthedFetch";
import { useAccess } from "@/lib/access";
import { FAMILIES as BB_FAMILIES } from "@/lib/basketball";
import { RK_FAMILIES } from "@/lib/racket";
import { FeatureGate, Unavailable } from "@/components/FeatureGate";
import type { SlipSelection } from "@/lib/slip";
import { Tabs } from "@/components/ui/tabs";
import { CompetitionBadge } from "@/components/CompetitionBadge";

const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

// backend/optimizer.py: the slip with the best win chance whose total odds
// land in the target range.

interface OptPick {
  home: string; away: string; date: string; time: string; league: string;
  market: string; market_name: string; code: string; label: string;
  prob: number; odds: number; odds_source: "sportybet" | "bookmaker" | "estimated";
  /** Basketball: SportyBet's own ids for the line */
  sb?: { eventId: string; marketId: string; specifier: string; outcomeId: string } | null;
  sport?: "football" | "basketball" | "tennis" | "table_tennis";
  bookable?: boolean;  // a SportyBet code can take it (match listed, market confirmed)
}
interface OptResult {
  picks?: OptPick[]; games?: number; total_odds?: number; win_chance?: number;
  within_target?: boolean; estimated_prices?: number; matches_considered?: number; bookable_picks?: number;
  target?: [number, number]; target_odds?: number; error?: string;
  /** Each pick checked on SportyBet's match page as the slip was made */
  live_check?: { checked: number; unchecked: number; repriced: number;
                 removed: { home: string; away: string; label: string; reason: string }[] };
}
interface BookResult { code: string | null; share_url: string | null; total_odds: number | null; error: string | null;
  price_changes?: number;
  picks: { key: string; status: string; reason?: string; odds?: number | null; shown_odds?: number }[] }

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
  { id: "scorer", label: "Anytime goalscorer", ids: ["anytime_scorer"] },
  { id: "corners", label: "Corners", ids: ["corners_ou"], needs: ["corners_ou"] },
  { id: "team_corners", label: "Team corners", ids: ["home_corners_ou", "away_corners_ou", "corners_1x2"],
    needs: ["home_corners_ou", "away_corners_ou", "corners_1x2"] },
  { id: "cards", label: "Cards", ids: ["cards_ou"], needs: ["cards_ou"] },
  { id: "shots", label: "Shots", ids: ["shots_ou", "home_shots_ou", "away_shots_ou"],
    needs: ["shots_ou", "home_shots_ou", "away_shots_ou"] },
  { id: "sot", label: "Shots on target", ids: ["sot_ou", "home_sot_ou", "away_sot_ou"],
    needs: ["sot_ou", "home_sot_ou", "away_sot_ou"] },
];
// Cards are SportyBet's "Total bookings": yellow 1, red 2

// Lines a chip can be narrowed to: one entry per backend market + option code
// (optimizer.py _PICKS). Chips without lines take every option.
type Line = { market: string; code: string; label: string };
const ou = (market: string, lines: string[], prefix = ""): Line[] => lines.flatMap(l => [
  { market, code: `O${l.replace(".", "")}`, label: `${prefix}Over ${l}` },
  { market, code: `U${l.replace(".", "")}`, label: `${prefix}Under ${l}` },
]);
const CHIP_LINES: Record<string, Line[]> = {
  goals_ou: ou("goals_ou", ["1.5", "2.5", "3.5"]),
  team_goals: [...ou("home_goals_ou", ["0.5", "1.5", "2.5"], "Home "), ...ou("away_goals_ou", ["0.5", "1.5", "2.5"], "Away ")],
  corners: ou("corners_ou", ["7.5", "8.5", "9.5", "10.5", "11.5"]),
  cards: ou("cards_ou", ["2.5", "3.5", "4.5", "5.5", "6.5"]),
  // The lines the backend prices (optimizer.py): the match's, then each team's
  shots: [...ou("shots_ou", ["20.5", "22.5", "24.5", "26.5", "28.5"], "Match "),
          ...ou("home_shots_ou", ["10.5", "11.5", "12.5", "13.5", "14.5", "15.5"], "Home "),
          ...ou("away_shots_ou", ["8.5", "9.5", "10.5", "11.5", "12.5", "13.5"], "Away ")],
  sot: [...ou("sot_ou", ["6.5", "7.5", "8.5", "9.5", "10.5"], "Match "),
        ...ou("home_sot_ou", ["2.5", "3.5", "4.5", "5.5", "6.5"], "Home "),
        ...ou("away_sot_ou", ["1.5", "2.5", "3.5", "4.5", "5.5"], "Away ")],
};
const lineKey = (l: Line) => `${l.market}:${l.code}`;

/** A market chip with a menu of its lines: tick any mix. */
function LineMenu({ label, active, onToggle, lines, picked, setPicked }: {
  label: string; active: boolean; onToggle: () => void; lines: Line[];
  picked: string[]; setPicked: (keys: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const [shift, setShift] = useState(0);
  // Keep the menu on screen: move it left by however much it overflows
  useLayoutEffect(() => {
    if (!open || !menu.current) { setShift(0); return; }
    const r = menu.current.getBoundingClientRect();
    setShift(Math.min(0, window.innerWidth - 12 - (r.right - shift)));
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  const mine = lines.filter(l => picked.includes(lineKey(l)));
  const summary = mine.length === lines.length ? "" : mine.length === 1 ? ` · ${mine[0].label}` : ` · ${mine.length} lines`;
  return (
    <div ref={ref} className="relative">
      <div className={clsx("chip !p-0 flex items-stretch overflow-hidden", active ? "chip-active" : "chip-idle")}>
        <button type="button" onClick={onToggle} className="px-3 py-1.5">{label}{active && summary}</button>
        <button type="button" onClick={() => setOpen(o => !o)} aria-label={`Choose ${label.toLowerCase()} lines`} aria-expanded={open}
          className="px-2 border-l border-n-700/60 flex items-center">
          <ChevronDown size={14} className={clsx("transition-transform", open && "rotate-180")} />
        </button>
      </div>
      {open && (
        <div ref={menu} style={{ transform: `translateX(${shift}px)` }}
          className="absolute z-20 mt-1.5 w-[19rem] max-w-[calc(100vw-1.5rem)] rounded-xl bg-surface p-2 [box-shadow:var(--shadow-pop)]">
          <div className="flex justify-between px-1.5 pb-1.5 text-[11px]">
            <button type="button" className="text-accent font-semibold"
              onClick={() => setPicked([...picked.filter(k => !lines.some(l => lineKey(l) === k)), ...lines.map(lineKey)])}>
              All lines
            </button>
            <button type="button" className="text-n-400" onClick={() => setPicked(picked.filter(k => !lines.some(l => lineKey(l) === k)))}>
              Clear
            </button>
          </div>
          <div className="grid grid-cols-2 gap-1 max-h-72 overflow-y-auto">
            {lines.map(l => {
              const k = lineKey(l), on = picked.includes(k);
              return (
                <label key={k} className={clsx("flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs cursor-pointer whitespace-nowrap",
                  on ? "bg-accent/10 text-n-0" : "text-n-300 hover:bg-surface-sunken")}>
                  <input type="checkbox" checked={on} className="accent-[rgb(var(--accent))]"
                    onChange={() => setPicked(on ? picked.filter(x => x !== k) : [...picked, k])} />
                  {l.label}
                </label>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// /api/sportybet/status — the last automatic linking run
interface LinkStatus {
  at: string | null; trigger: string | null; linked: number; predictions: number;
  markets: Record<string, boolean>;
  coverage?: Record<string, number>;   // market → SportyBet matches pricing it now
  every_minutes?: number;
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
    <button type="button" onClick={onClick} aria-pressed={active} className={clsx("chip", active ? "chip-active" : "chip-idle")}>
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

const BUILD_PERKS = [
  "Build a slip for the exact odds you want, with the best chance of landing",
  "Choose markets, lines, leagues and how sure each pick must be",
  "Book the result on SportyBet in one tap, tracked in your dashboard",
];
const CODE_PERKS = [
  "Paste any SportyBet code: we rate every game with our model",
  "See the risky picks and a safer version of your slip",
];

export default function OptimizerPage() {
  const { user } = useUser();
  const authFetch = useAuthedFetch();
  const access = useAccess();
  // The server said no (its own tier check): offer the upgrade for that mode
  const [locked, setLocked] = useState<null | "build" | "code">(null);
  const slip = useBetSlip();

  const [mode, setMode] = useState<"build" | "code">("build");
  // The modes switched on for this visitor (admin → Access)
  const modes = ([["build", "Build a slip", "optimizer"], ["code", "Check my SportyBet code", "code_check"]] as const)
    .filter(([, , f]) => access.shown(f));
  const active = modes.some(([id]) => id === mode) ? mode : (modes[0]?.[0] ?? "build");
  const [targetOdds, setTargetOdds] = useState(10);
  const [typed, setTyped] = useState("10");
  const [tolerance, setTolerance] = useState(0.05);
  const [minProb, setMinProb] = useState(0.6);
  const [days, setDays] = useState(3);
  const [maxGames, setMaxGames] = useState(30);
  const [markets, setMarkets] = useState<string[]>(MARKETS.map(m => m.id));
  // Any mix of football, basketball, tennis and table tennis (every line
  // SportyBet offers, priced by our model)
  type OptSport = "football" | "basketball" | "tennis" | "table_tennis";
  const [sports, setSports] = useState<OptSport[]>(["football"]);
  const [bbFamilies, setBbFamilies] = useState<string[]>(BB_FAMILIES.map(f => f.id));
  const [tnFamilies, setTnFamilies] = useState<string[]>(RK_FAMILIES.tennis.map(f => f.id));
  const [ttFamilies, setTtFamilies] = useState<string[]>(RK_FAMILIES.table_tennis.map(f => f.id));
  const bbOn = access.shown("sport.basketball");
  const tnOn = access.shown("sport.tennis");
  const ttOn = access.shown("sport.table_tennis");
  const otherSports = bbOn || tnOn || ttOn;
  const sportChips = ([["football", "Football", true], ["basketball", "Basketball", bbOn], ["tennis", "Tennis", tnOn],
                       ["table_tennis", "Table tennis", ttOn]] as const).filter(([, , on]) => on);
  // The sports in play: the ones picked that are open to this account (football if none)
  const inPlay = sports.filter(k => sportChips.some(([c]) => c === k));
  const playing: OptSport[] = inPlay.length ? inPlay : ["football"];
  const allPicked = sportChips.every(([k]) => playing.includes(k));
  // A sport chip toggles; the last one in play stays on
  const toggleSport = (k: OptSport) =>
    setSports(() => playing.includes(k) ? (playing.length > 1 ? playing.filter(x => x !== k) : playing) : [...playing, k]);
  // Markets switched off (admin, or paused by the weekly accuracy review):
  // not offered at all, rather than offered and then left out
  const [off, setOff] = useState<Set<string>>(new Set());
  useEffect(() => {
    fetch(`${API}/api/market-review/paused`).then(r => (r.ok ? r.json() : null))
      .then(d => setOff(new Set((d?.paused ?? []).map((p: { market: string }) => p.market)))).catch(() => {});
  }, []);
  const shownMarkets = MARKETS.map(m => ({ ...m, ids: m.ids.filter(id => !off.has(id)) })).filter(m => m.ids.length > 0);
  const chipLines = (id: string) => CHIP_LINES[id]?.filter(l => !off.has(l.market));
  const bbShown = BB_FAMILIES.filter(f => !off.has(f.id));
  // Every line of every chip that has lines, ticked to start with
  const [lines, setLines] = useState<string[]>(Object.values(CHIP_LINES).flat().map(lineKey));
  // A chip with lines counts as off when none of its lines is ticked
  const chosen = shownMarkets.filter(m => markets.includes(m.id)
    && (!chipLines(m.id) || chipLines(m.id)!.some(l => lines.includes(lineKey(l)))));
  // Backend markets and, for chips narrowed to some lines, their option codes
  const request = () => {
    const ids: string[] = [], codes: Record<string, string[]> = {};
    for (const m of chosen) {
      const ls = chipLines(m.id);
      if (!ls) { ids.push(...m.ids); continue; }
      for (const id of m.ids) {
        const mine = ls.filter(l => l.market === id && lines.includes(lineKey(l)));
        if (!mine.length) continue;
        ids.push(id);
        if (mine.length < ls.filter(l => l.market === id).length) codes[id] = mine.map(l => l.code);
      }
    }
    return { markets: ids, ...(Object.keys(codes).length ? { codes } : {}) };
  };
  // On when SportyBet confirmed at least one of the chip's markets (the
  // server only books the confirmed ones)
  const confirmed = (m: (typeof MARKETS)[number], s: LinkStatus | null) =>
    !m.needs || !s || m.needs.some(k => s.markets?.[k]);
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
  const unconfirmed = shownMarkets.filter(m => !confirmed(m, link));
  const showFootball = !otherSports || playing.includes("football");
  const showBasketball = bbOn && playing.includes("basketball");
  const showTennis = tnOn && playing.includes("tennis");
  const showTT = ttOn && playing.includes("table_tennis");
  const panels = [showFootball, showBasketball, showTennis, showTT].filter(Boolean).length;
  // League bar: the leagues with matches in the chosen days, for the sports in play.
  // None picked = every league; pick one or more to keep the slip to them.
  type OptLeague = { sport: OptSport; id: string; name: string; flag: string; matches: number };
  const [optLeagues, setOptLeagues] = useState<OptLeague[]>([]);
  const [pickedLeagues, setPickedLeagues] = useState<string[]>([]);
  const sportsKey = (otherSports ? playing : ["football"]).join(",");
  useEffect(() => {
    const ctrl = new AbortController();
    fetch(`${API}/api/optimizer/leagues?days=${days}&sports=${sportsKey}`, { signal: ctrl.signal })
      .then(r => (r.ok ? r.json() : null))
      .then((d: { leagues?: OptLeague[] } | null) => {
        const list = d?.leagues ?? [];
        setOptLeagues(list);
        // A league with no matches in the new window drops out of the pick
        setPickedLeagues(ps => ps.filter(id => list.some(l => l.id === id)));
      })
      .catch(() => {});
    return () => ctrl.abort();
  }, [days, sportsKey]);
  const toggleLeague = (id: string) => setPickedLeagues(ps => ps.includes(id) ? ps.filter(x => x !== id) : [...ps, id]);
  const SPORT_EMOJI: Record<string, string> = { football: "⚽", basketball: "🏀", tennis: "🎾", table_tennis: "🏓" };
  const bbChosen = bbShown.filter(f => bbFamilies.includes(f.id));
  const tnShown = RK_FAMILIES.tennis.filter(f => !off.has(f.id));
  const ttShown = RK_FAMILIES.table_tennis.filter(f => !off.has(f.id));
  // At least one market on for each sport in play
  const marketsPicked = (!showFootball || chosen.length > 0) && (!showBasketball || bbChosen.length > 0)
    && (!showTennis || tnShown.some(f => tnFamilies.includes(f.id))) && (!showTT || ttShown.some(f => ttFamilies.includes(f.id)));
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
    market: p.market, marketName: p.market_name, code: p.code, label: p.label, prob: p.prob, odds: p.odds,
    ...(p.sb ? { sb: p.sb } : {}),
  }));

  const run = async (bookable = bookableOnly) => {
    setBusy(true); setResult(null); setBooked(null);
    try {
      const res = await authFetch(`${API}/api/optimizer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_odds: targetOdds, min_odds: lo, max_odds: hi, min_prob: minProb, days, max_games: maxGames,
                               ...request(),
                               sports: otherSports ? playing : ["football"],
                               // Servers from before multi-sport read one sport (or all)
                               sport: !otherSports ? "football" : playing.length === 1 ? playing[0] : "all",
                               bb_markets: bbFamilies.filter(f => !off.has(f)),
                               tn_markets: tnFamilies.filter(f => !off.has(f)), tt_markets: ttFamilies.filter(f => !off.has(f)),
                               ...(pickedLeagues.length ? { leagues: pickedLeagues } : {}),
                               bookable_only: bookable }),
      });
      if (res.status === 401 || res.status === 402) { setLocked("build"); return; }
      const data = await res.json();
      setResult(res.ok ? data : { error: data?.detail || "The optimizer couldn't run. Try again." });
    } catch {
      setResult({ error: "Couldn't reach the server. Try again in a moment." });
    } finally { setBusy(false); }
  };

  // football.com is SportyBet's platform: the same picks, its own code
  const [bookOn, setBookOn] = useState<"sportybet" | "football_com">("sportybet");
  const bookName = bookOn === "football_com" ? "football.com" : "SportyBet";
  const book = async (on: "sportybet" | "football_com" = bookOn) => {
    setBookOn(on);
    setBooking(true); setBooked(null);
    try {
      // Signed in, the server keeps the code as a ticket (Dashboard → Tickets)
      const res = await authFetch(`${API}/api/booking/convert`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ platform: on, selections, source: "optimizer", uid: user?.id }),
      });
      const data = await res.json();
      // The server's own reason when it refused the slip (not a generic "try again")
      if (!res.ok || !Array.isArray(data?.picks)) throw new Error(typeof data?.detail === "string" ? data.detail : "");
      setBooked(data);
    } catch (e) {
      setBooked({ code: null, share_url: null, total_odds: null, picks: [],
                  error: (e as Error)?.message || `${on === "football_com" ? "football.com" : "SportyBet"} didn't return a code. Try again in a minute.` });
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

        {access.ready && modes.length === 0 ? <Unavailable title="Optimizer" /> : <>
        {modes.length > 1 && (
          <Tabs label="Optimizer mode" id="optimizer-mode" value={active} onChange={setMode}
            items={modes.map(([value, label]) => ({ value, label }))} />
        )}

        {active === "code" ? (
          <FeatureGate feature="code_check" title="Check a SportyBet code" perks={CODE_PERKS} locked={locked === "code"}>
            <CodeCheck onLocked={() => setLocked("code")} />
          </FeatureGate>
        ) : <FeatureGate feature="optimizer" title="Optimizer" perks={BUILD_PERKS} locked={locked === "build"}>
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
            {otherSports && (
              <Setting label="Sports (pick one or more)">
                {sportChips.map(([k, l]) => (
                  <Chip key={k} active={playing.includes(k)} onClick={() => toggleSport(k)}>{l}</Chip>
                ))}
                <Chip active={allPicked} onClick={() => setSports(allPicked ? ["football"] : sportChips.map(([k]) => k))}>All</Chip>
              </Setting>
            )}
            <div className="space-y-2">
              <p className="eyebrow">At most {maxGames} games</p>
              <input type="range" min={1} max={30} value={maxGames} onChange={e => setMaxGames(Number(e.target.value))}
                className="w-full accent-[rgb(var(--accent))]" aria-label="Maximum games" />
            </div>
          </div>

          {optLeagues.length > 1 && (
            <div className="space-y-2">
              <p className="eyebrow">Leagues {pickedLeagues.length ? `(${pickedLeagues.length} picked)` : "(all)"}</p>
              <div className="overflow-x-auto pb-1 -mx-4 px-4 sm:mx-0 sm:px-0">
                <div className="flex gap-1.5 min-w-max">
                  <Chip active={!pickedLeagues.length} onClick={() => setPickedLeagues([])}>
                    All leagues <span className="tnum text-[10px] opacity-70">{optLeagues.reduce((a, l) => a + l.matches, 0)}</span>
                  </Chip>
                  {optLeagues.map(l => (
                    <Chip key={`${l.sport}:${l.id}`} active={pickedLeagues.includes(l.id)} onClick={() => toggleLeague(l.id)}>
                      <span className="inline-flex items-center gap-1.5">
                        <CompetitionBadge name={l.name} fallbackEmoji={l.flag || SPORT_EMOJI[l.sport]} size={14} />
                        {l.name} <span className="tnum text-[10px] opacity-70">{l.matches}</span>
                      </span>
                    </Chip>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* Markets: each sport in play side by side */}
          <div className={clsx("grid gap-4", panels > 1 && "lg:grid-cols-2")}>
            {showFootball && (
              <div className="rounded-xl [box-shadow:var(--ring-control)] bg-surface-sunken/40 p-4">
                <Setting label={panels > 1 ? "Football markets" : "Markets"}>
                  <div className="basis-full flex items-center gap-3 text-xs -mt-0.5 mb-0.5">
                    <button type="button" className="font-semibold text-accent" onClick={() => setMarkets(MARKETS.map(m => m.id))}>
                      Select all
                    </button>
                    <button type="button" className="text-n-400 hover:text-n-200" onClick={() => setMarkets([])}>Clear all</button>
                    <span className="text-n-500">{chosen.length} of {shownMarkets.length} on</span>
                  </div>
                  {shownMarkets.map(m => chipLines(m.id) ? (
                    <LineMenu key={m.id} label={m.label} active={markets.includes(m.id)} lines={chipLines(m.id)!} picked={lines}
                      onToggle={() => setMarkets(ms => ms.includes(m.id) ? ms.filter(x => x !== m.id) : [...ms, m.id])}
                      setPicked={keys => {
                        setLines(keys);
                        if (chipLines(m.id)!.some(l => keys.includes(lineKey(l)))) setMarkets(ms => ms.includes(m.id) ? ms : [...ms, m.id]);
                      }} />
                  ) : (
                    <Chip key={m.id} active={markets.includes(m.id)}
                      onClick={() => setMarkets(ms => ms.includes(m.id) ? ms.filter(x => x !== m.id) : [...ms, m.id])}>
                      {m.label}
                    </Chip>
                  ))}
                  {link?.coverage && (() => {
                    // Markets SportyBet only puts up about a day before kick-off: how many matches have them now
                    const show = shownMarkets.filter(m => ["corners", "cards", "shots", "sot"].includes(m.id))
                      .map(m => [m.label, Math.max(0, ...m.ids.map(id => link.coverage?.[id] ?? 0))] as const);
                    return show.length > 0 && (
                      <p className="basis-full text-[11px] text-n-500 tnum">
                        On SportyBet right now: {show.map(([l, n]) => `${l} ${n}`).join(" · ")} matches.
                        These appear about a day before kick-off; we re-check every {link.every_minutes ?? 30} minutes.
                      </p>
                    );
                  })()}
                  {unconfirmed.length > 0 && (
                    <p className="basis-full text-[11px] text-n-500">
                      {unconfirmed.map(m => m.label).join(" & ")}: SportyBet hasn&apos;t confirmed these markets yet, so
                      codes can&apos;t include them. Untick &quot;Only matches SportyBet lists&quot; to use them anyway.
                    </p>
                  )}
                </Setting>
              </div>
            )}
            {showBasketball && (
              <div className="rounded-xl [box-shadow:var(--ring-control)] bg-surface-sunken/40 p-4">
                <Setting label={panels > 1 ? "Basketball markets" : "Markets"}>
                  <div className="basis-full flex items-center gap-3 text-xs -mt-0.5 mb-0.5">
                    <button type="button" className="font-semibold text-accent" onClick={() => setBbFamilies(BB_FAMILIES.map(f => f.id))}>
                      Select all
                    </button>
                    <button type="button" className="text-n-400 hover:text-n-200" onClick={() => setBbFamilies([])}>Clear all</button>
                    <span className="text-n-500">{bbChosen.length} of {bbShown.length} on</span>
                  </div>
                  {bbShown.map(f => (
                    <Chip key={f.id} active={bbFamilies.includes(f.id)}
                      onClick={() => setBbFamilies(fs => fs.includes(f.id) ? fs.filter(x => x !== f.id) : [...fs, f.id])}>
                      {f.name}
                    </Chip>
                  ))}
                  <p className="basis-full text-[11px] text-n-500">
                    Every line SportyBet offers on its basketball matches, at its price, rated by our model: always bookable.
                  </p>
                </Setting>
              </div>
            )}
            {([["tennis", showTennis, tnShown, tnFamilies, setTnFamilies, "Tennis"],
               ["table_tennis", showTT, ttShown, ttFamilies, setTtFamilies, "Table tennis"]] as const).map(
              ([k, show, shown, fams, setFams, name]) => show && (
              <div key={k} className="rounded-xl [box-shadow:var(--ring-control)] bg-surface-sunken/40 p-4">
                <Setting label={panels > 1 ? `${name} markets` : "Markets"}>
                  <div className="basis-full flex items-center gap-3 text-xs -mt-0.5 mb-0.5">
                    <button type="button" className="font-semibold text-accent" onClick={() => setFams(RK_FAMILIES[k].map(f => f.id))}>
                      Select all
                    </button>
                    <button type="button" className="text-n-400 hover:text-n-200" onClick={() => setFams([])}>Clear all</button>
                    <span className="text-n-500">{shown.filter(f => fams.includes(f.id)).length} of {shown.length} on</span>
                  </div>
                  {shown.map(f => (
                    <Chip key={f.id} active={fams.includes(f.id)}
                      onClick={() => setFams(fs => fs.includes(f.id) ? fs.filter(x => x !== f.id) : [...fs, f.id])}>
                      {f.name}
                    </Chip>
                  ))}
                  <p className="basis-full text-[11px] text-n-500">
                    Every line SportyBet offers on its {name.toLowerCase()} matches, at its price, rated by our model: always bookable.
                  </p>
                </Setting>
              </div>
            ))}
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

          {!marketsPicked && <p className="text-xs text-warn">Pick at least one market.</p>}
          <button onClick={() => run()} disabled={busy || !validTarget || !marketsPicked}
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
              <LiveCheckNote check={result.live_check} />

              {booked?.code ? (
                <div className="rounded-xl bg-surface-sunken p-4 space-y-3 [box-shadow:inset_0_0_0_1px_rgb(var(--accent)/0.4)]">
                  <div className="flex items-center justify-between">
                    <p className="eyebrow">Booking code</p>
                    <span className="text-[11px] font-bold text-accent">{bookName}</span>
                  </div>
                  <p className="font-mono text-3xl font-bold tracking-[0.2em] text-n-0">{booked.code}</p>
                  <div className="flex flex-wrap gap-2">
                    <button onClick={() => copy(booked.code!)}
                      className="flex items-center gap-1.5 rounded-lg bg-brand-400 hover:bg-brand-300 text-ink font-bold px-4 py-2 text-sm">
                      {copied ? <Check size={14} /> : <Copy size={14} />} {copied ? "Copied" : "Copy code"}
                    </button>
                    {booked.share_url && (
                      <a href={booked.share_url} target="_blank" rel="noreferrer"
                        className="flex items-center gap-1.5 rounded-lg [box-shadow:var(--ring-control)] text-n-200 px-4 py-2 text-sm">
                        <ExternalLink size={14} /> Open on {bookName}
                      </a>
                    )}
                    <button onClick={() => book(bookOn === "sportybet" ? "football_com" : "sportybet")} disabled={booking}
                      className="flex items-center gap-1.5 rounded-lg [box-shadow:var(--ring-control)] text-n-300 px-4 py-2 text-sm">
                      {booking ? <Loader2 size={14} className="animate-spin" /> : <Ticket size={14} />}
                      Get a {bookOn === "sportybet" ? "football.com" : "SportyBet"} code too
                    </button>
                  </div>
                  {booked.total_odds != null && (
                    <p className="text-xs text-n-300 tnum">
                      Total odds on {bookName}: <span className="font-bold text-n-0">{odds(booked.total_odds)}x</span>
                      {booked.total_odds !== result.total_odds && result.total_odds != null &&
                        <span className="text-n-500"> (the slip above said {odds(result.total_odds)}x)</span>}
                    </p>
                  )}
                  {(booked.price_changes ?? 0) > 0 && (
                    <p className="text-xs text-warn">
                      {booked.price_changes} price{booked.price_changes === 1 ? " has" : "s have"} moved since the slip was made;
                      the code carries {bookName}&apos;s current odds.
                    </p>
                  )}
                  {failedPicks.length > 0 && (
                    <p className="text-xs text-warn">{failedPicks.length} pick{failedPicks.length === 1 ? " wasn't" : "s weren't"} included: {failedPicks[0].reason}</p>
                  )}
                </div>
              ) : result.bookable_picks === 0 ? (
                <div className="space-y-2">
                  <p className="text-xs text-warn flex gap-1.5">
                    <AlertTriangle size={13} className="shrink-0 mt-0.5" />
                    SportyBet doesn&apos;t offer these picks&apos; markets on these matches, so this slip can&apos;t become a
                    SportyBet code. Keep it in your bet slip for a bookmaker that has them, or tick &quot;Only matches
                    SportyBet lists&quot; for a slip SportyBet can book.
                  </p>
                  <button onClick={toSlip}
                    className="w-full rounded-xl [box-shadow:var(--ring-control)] text-n-200 font-semibold px-5 py-3">
                    Put in my bet slip
                  </button>
                </div>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {result.bookable_picks !== undefined && result.bookable_picks < (result.picks?.length ?? 0) && (
                    <p className="w-full text-xs text-warn">
                      A SportyBet code will take {result.bookable_picks} of the {result.picks?.length} picks; the ones
                      marked &quot;not on SportyBet&quot; are left out.
                    </p>
                  )}
                  <button onClick={() => book("sportybet")} disabled={booking}
                    className="flex-1 min-w-[12rem] flex items-center justify-center gap-2 rounded-xl bg-brand-400 hover:bg-brand-300 text-ink font-bold px-5 py-3 disabled:opacity-50">
                    {booking && bookOn === "sportybet" ? <Loader2 size={16} className="animate-spin" /> : <Ticket size={16} />}
                    {booking && bookOn === "sportybet" ? "Booking…" : "Get SportyBet code"}
                  </button>
                  <button onClick={() => book("football_com")} disabled={booking}
                    className="flex-1 min-w-[12rem] flex items-center justify-center gap-2 rounded-xl text-n-0 font-bold px-5 py-3 disabled:opacity-50 [box-shadow:inset_0_0_0_1px_rgb(var(--accent)/0.6)] hover:bg-brand-400/[0.06] transition-[background-color,scale] duration-150 active:scale-[0.96]">
                    {booking && bookOn === "football_com" ? <Loader2 size={16} className="animate-spin" /> : <Ticket size={16} />}
                    {booking && bookOn === "football_com" ? "Booking…" : "Get football.com code"}
                  </button>
                  <button onClick={toSlip}
                    className="flex-1 min-w-[12rem] rounded-xl [box-shadow:var(--ring-control)] text-n-200 font-semibold px-5 py-3">
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
                    <p className="text-xs text-n-400">{p.market_name}: <span className="text-n-200">{p.label}</span>
                      {p.bookable === false && <span className="ml-1.5 text-[10px] font-bold uppercase tracking-wide text-warn">not on SportyBet</span>}
                    </p>
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
        </FeatureGate>}
        </>}
      </div>
    </AppShell>
  );
}


/** What the live check on SportyBet did to the slip: suspended picks taken out, prices brought up to date. */
function LiveCheckNote({ check }: { check?: OptResult["live_check"] }) {
  if (!check || (check.checked === 0 && check.removed.length === 0)) return null;
  const removed = check.removed;
  return (
    <div className="rounded-xl bg-surface-sunken px-3 py-2.5 text-[12px] text-n-300 space-y-1">
      <p className="flex items-center gap-1.5">
        <Check size={13} className="text-accent shrink-0" />
        Checked on SportyBet just now: {check.checked} pick{check.checked === 1 ? "" : "s"} open at the odds shown
        {check.repriced > 0 && ` (${check.repriced} price${check.repriced === 1 ? "" : "s"} updated to SportyBet's current odds)`}.
      </p>
      {removed.length > 0 && (
        <p className="flex items-start gap-1.5 text-warn">
          <AlertTriangle size={13} className="shrink-0 mt-px" />
          <span>
            Left out and replaced: {removed.slice(0, 3).map(x => `${x.home} v ${x.away} (${x.label}: ${x.reason.toLowerCase()})`).join("; ")}
            {removed.length > 3 && ` and ${removed.length - 3} more`}.
          </span>
        </p>
      )}
      {check.unchecked > 0 && (
        <p className="text-n-500">{check.unchecked} pick{check.unchecked === 1 ? "" : "s"} couldn&apos;t be checked right now; booking checks them again.</p>
      )}
    </div>
  );
}
