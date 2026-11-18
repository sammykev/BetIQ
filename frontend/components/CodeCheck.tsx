"use client";

import { useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { AlertTriangle, ArrowRight, Check, CheckCircle2, Copy, ExternalLink, Flame, Loader2, Scale, ScanSearch, Shield, ShieldCheck,
  SlidersHorizontal, Ticket, Wand2 } from "lucide-react";
import { useBetSlip } from "@/lib/useBetSlip";
import { useAuthedFetch } from "@/lib/useAuthedFetch";
import { useUser } from "@clerk/nextjs";
import type { SlipSelection } from "@/lib/slip";

// Paste a SportyBet booking code: backend/code_check.py scores every leg
// with the model, suggests better picks, and "Refine" turns it into three
// tickets (safe, conservative, risky), each bookable as a new code.

const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

interface Leg {
  home: string; away: string; date: string; time: string; league: string;
  market: string; pick: string; odds: number | null; active: boolean;
  our_prob: number | null; implied: number | null; value: number | null;
  /** The chance counted for the leg: our model's, or SportyBet's odds less their margin */
  chance?: number | null; chance_from?: "model" | "odds";
  verdict: "strong" | "fair" | "risky" | "not_modelled"; note?: string;
  suggestion: { market_name: string; label: string; prob: number; odds: number; odds_source: string } | null;
}
interface Pick {
  home: string; away: string; date: string; time: string; league: string;
  market: string; market_name: string; code: string; label: string;
  prob: number | null; odds: number | null; odds_source: string;
  sb?: SlipSelection["sb"];
  sport?: SlipSelection["sport"];
}
interface Slip { picks: Pick[]; games: number; total_odds: number | null; win_chance: number | null;
  unmodelled: number; estimated_prices: number; target_odds?: number;
  from_odds?: number; target_chance?: number; left_out?: string[] }
interface Report {
  code: string; legs: Leg[];
  original: { games: number; total_odds: number | null; win_chance: number | null; unmodelled: number; from_odds?: number };
  same_odds: Slip | null; safest: Slip | null;
  tickets?: { safe: Slip | null; conservative: Slip | null; risky: Slip | null } | null;
  /** Why a ticket couldn't be made with these settings */
  ticket_notes?: Partial<Record<"safe" | "conservative" | "risky", string>>;
  settings?: Settings;
  kept_strong?: number;
  /** How each kind of refined ticket has done so far (code_check.record) */
  record?: Record<"safe" | "conservative" | "risky",
    { aim: number; landed: number; lost: number; open: number; said: number | null; rate: number | null }> | null;
}

const TICKETS = [
  { id: "safe", name: "Safe", icon: Shield, tone: "text-accent", least: 0.5,
    blurb: "A 50% chance of winning: the highest odds we can find on your games at that chance." },
  { id: "conservative", name: "Conservative", icon: Scale, tone: "text-info", least: 0.25,
    blurb: "A 25% chance of winning, for bigger odds: other markets on each game where they pay more." },
  { id: "risky", name: "Risky", icon: Flame, tone: "text-warn", least: 0.125,
    blurb: "A 12.5% chance of winning: longer prices on each game for the biggest payout at that chance." },
] as const;
type TicketId = (typeof TICKETS)[number]["id"];

/** The refine settings (code_check.settings_of) */
interface Settings {
  strictness: "strict" | "balanced" | "loose";
  min_odds: number | null; max_odds: number | null; max_leg_odds: number | null;
  keep_all: boolean; keep_strong: number | null; main_markets: boolean;
}
const DEFAULT_SETTINGS: Settings = { strictness: "balanced", min_odds: null, max_odds: null, max_leg_odds: null,
  keep_all: false, keep_strong: null, main_markets: false };
const STRICTNESS: { id: Settings["strictness"]; name: string; chances: [number, number, number]; note: string }[] = [
  { id: "strict", name: "Strict", chances: [0.6, 0.3, 0.15], note: "Surer tickets, smaller odds" },
  { id: "balanced", name: "Balanced", chances: [0.5, 0.25, 0.125], note: "50%, 25% and 12.5%" },
  { id: "loose", name: "Loose", chances: [0.4, 0.2, 0.1], note: "Bigger odds, less sure" },
];
const leastFor = (s: Settings | undefined, id: TicketId) =>
  (STRICTNESS.find(x => x.id === (s?.strictness ?? "balanced")) ?? STRICTNESS[1]).chances[TICKETS.findIndex(t => t.id === id)];
const LEG_CAPS = [null, 1.5, 2, 3, 5] as const;

/** "Refine settings": how strict, the odds range and what may change. */
function RefineSettings({ value, onApply, busy }: { value: Settings; onApply: (s: Settings) => void; busy: boolean }) {
  const [s, setS] = useState<Settings>(value);
  const [minT, setMinT] = useState(value.min_odds ? String(value.min_odds) : "");
  const [maxT, setMaxT] = useState(value.max_odds ? String(value.max_odds) : "");
  const num = (t: string) => { const v = parseFloat(t); return Number.isFinite(v) && v > 1 ? v : null; };
  const toggle = (k: "keep_all" | "main_markets", label: string, help: string) => (
    <label className="flex items-start gap-2.5 text-sm text-n-200 cursor-pointer">
      <input type="checkbox" checked={s[k]} onChange={e => setS({ ...s, [k]: e.target.checked })}
        className="mt-0.5 size-4 accent-[rgb(var(--accent))]" />
      <span>{label}<span className="block text-[11px] text-n-500">{help}</span></span>
    </label>
  );
  return (
    <div className="space-y-4">
      <div>
        <p className="eyebrow">How strict</p>
        <div className="grid grid-cols-3 gap-2 mt-2" role="radiogroup" aria-label="How strict">
          {STRICTNESS.map(x => (
            <button key={x.id} type="button" role="radio" aria-checked={s.strictness === x.id}
              onClick={() => setS({ ...s, strictness: x.id })}
              className={clsx("rounded-xl px-2 py-2 text-left", s.strictness === x.id ? "card-selected bg-surface" : "bg-surface-sunken")}>
              <p className="text-sm font-semibold text-n-0">{x.name}</p>
              <p className="text-[11px] text-n-400 tnum">{x.chances.map(target).join(" · ")}</p>
            </button>
          ))}
        </div>
        <p className="text-[11px] text-n-500 mt-1.5">The chance each ticket (safe · conservative · risky) wins at least.</p>
      </div>
      <div>
        <p className="eyebrow">Total odds</p>
        <div className="flex items-center gap-2 mt-2">
          <input inputMode="decimal" value={minT} onChange={e => setMinT(e.target.value.replace(/[^0-9.]/g, ""))}
            placeholder="Any" aria-label="Lowest total odds"
            className="w-24 rounded-xl bg-surface-sunken border border-n-800 px-3 py-2 tnum text-n-0 outline-none focus:border-accent" />
          <span className="text-sm text-n-400">to</span>
          <input inputMode="decimal" value={maxT} onChange={e => setMaxT(e.target.value.replace(/[^0-9.]/g, ""))}
            placeholder="Any" aria-label="Highest total odds"
            className="w-24 rounded-xl bg-surface-sunken border border-n-800 px-3 py-2 tnum text-n-0 outline-none focus:border-accent" />
          <span className="text-sm text-n-400">x</span>
        </div>
        <p className="text-[11px] text-n-500 mt-1.5">A ticket above the highest is held down to it; one that can&apos;t reach the lowest at its chance isn&apos;t made.</p>
      </div>
      <div>
        <p className="eyebrow">Most odds a game</p>
        <div className="flex flex-wrap gap-1.5 mt-2" role="radiogroup" aria-label="Most odds a game">
          {LEG_CAPS.map(c => (
            <button key={String(c)} type="button" role="radio" aria-checked={s.max_leg_odds === c}
              onClick={() => setS({ ...s, max_leg_odds: c })}
              className={clsx("chip tnum", s.max_leg_odds === c ? "chip-active" : "chip-idle")}>
              {c === null ? "Any" : `${c.toFixed(c % 1 ? 1 : 0)}x`}
            </button>
          ))}
        </div>
      </div>
      <div className="space-y-3">
        {toggle("keep_all", "Keep all my games", "Don't drop a game to reach the chance; no ticket if they can't all fit.")}
        <label className="flex items-start gap-2.5 text-sm text-n-200 cursor-pointer">
          <input type="checkbox" checked={s.keep_strong !== null} onChange={e => setS({ ...s, keep_strong: e.target.checked ? 0.75 : null })}
            className="mt-0.5 size-4 accent-[rgb(var(--accent))]" />
          <span>Keep my strong picks<span className="block text-[11px] text-n-500">Picks we rate 75%+ stay as you chose them; only the others change.</span></span>
        </label>
        {toggle("main_markets", "Main markets only", "Results, double chance, goals, handicaps and winners: no corners, cards or quarters.")}
      </div>
      <div className="flex flex-wrap gap-2">
        <button type="button" disabled={busy}
          onClick={() => onApply({ ...s, min_odds: num(minT), max_odds: num(maxT) })}
          className="flex-1 min-w-[10rem] inline-flex items-center justify-center gap-2 rounded-xl bg-brand-400 hover:bg-brand-300 text-ink font-bold px-4 py-2.5 disabled:opacity-50">
          {busy ? <Loader2 size={15} className="animate-spin" /> : <Wand2 size={15} />} Refine again
        </button>
        <button type="button" onClick={() => { setS(DEFAULT_SETTINGS); setMinT(""); setMaxT(""); }}
          className="rounded-xl [box-shadow:var(--ring-control)] text-n-200 font-semibold px-4 py-2.5">Reset</button>
      </div>
    </div>
  );
}

const odds = (x: number | null | undefined) => (x ? x.toFixed(2) : "—");
const pct = (x: number | null | undefined) =>
  x === null || x === undefined ? "—" : x >= 0.1 ? `${Math.round(x * 100)}%` : x >= 0.001 ? `${(x * 100).toFixed(1)}%` : "<0.1%";

/** A ticket's target chance: 50%, 25%, 12.5% */
const target = (x: number | null | undefined) => (x == null ? "—" : `${+(x * 100).toFixed(1)}%`);

const VERDICT: Record<Leg["verdict"], { label: string; cls: string }> = {
  strong: { label: "Strong", cls: "text-accent border-accent/30 bg-accent/10" },
  fair: { label: "Fair", cls: "text-info border-info/30 bg-info/10" },
  risky: { label: "Risky", cls: "text-danger border-danger/30 bg-danger/10" },
  not_modelled: { label: "From the odds", cls: "text-n-400 border-n-800 bg-surface-sunken" },
};

function toSelection(p: Pick): SlipSelection {
  return { home: p.home, away: p.away, date: p.date, time: p.time, league: p.league,
           market: p.market, marketName: p.market_name, code: p.code, label: p.label, prob: p.prob,
           ...(p.sb ? { sb: p.sb } : {}), ...(p.sport ? { sport: p.sport } : {}) };
}

function ImprovedSlip({ title, blurb, slip, original }: { title: string; blurb: string; slip: Slip; original: Report["original"] }) {
  const betSlip = useBetSlip();
  const authFetch = useAuthedFetch();
  const { user } = useUser();
  const [booking, setBooking] = useState(false);
  const [code, setCode] = useState<{ code: string | null; url: string | null; error: string | null } | null>(null);
  const [copied, setCopied] = useState(false);
  const selections = slip.picks.map(toSelection);

  const book = async () => {
    setBooking(true); setCode(null);
    try {
      const r = await authFetch(`${API}/api/booking/convert`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ platform: "sportybet", selections, source: "code_check", uid: user?.id }) });
      const d = await r.json();
      setCode({ code: d.code ?? null, url: d.share_url ?? null, error: d.code ? null : d.error ?? "SportyBet didn't return a code." });
    } catch { setCode({ code: null, url: null, error: "Couldn't reach the server. Try again." }); }
    setBooking(false);
  };

  const better = slip.win_chance !== null && original.win_chance !== null ? slip.win_chance / original.win_chance : null;

  return (
    <div className="card p-4 sm:p-5 space-y-4">
      <div>
        <p className="font-semibold text-n-0">{title}</p>
        <p className="text-xs text-n-400">{blurb}</p>
      </div>
      <div className="grid grid-cols-3 gap-2 text-center">
        <div className="rounded-xl bg-surface-sunken px-2 py-2.5"><p className="eyebrow">Total odds</p>
          <p className="font-display font-extrabold text-2xl text-n-0 tnum">{odds(slip.total_odds)}x</p></div>
        <div className="rounded-xl bg-surface-sunken px-2 py-2.5"><p className="eyebrow">Games</p>
          <p className="font-display font-extrabold text-2xl text-n-0 tnum">{slip.games}</p></div>
        <div className="rounded-xl bg-surface-sunken px-2 py-2.5"><p className="eyebrow">All win</p>
          <p className="font-display font-extrabold text-2xl text-accent tnum">{pct(slip.win_chance)}</p></div>
      </div>
      {better !== null && better > 1.05 && (
        <p className="text-xs text-accent flex items-center gap-1.5"><Check size={13} />
          {better >= 2 ? `${better.toFixed(1)}×` : `${Math.round((better - 1) * 100)}%`} more likely to win than your code</p>
      )}
      {(slip.left_out?.length ?? 0) > 0 && (
        <p className="text-xs text-n-400 text-pretty">
          Left out to keep the chance at {target(slip.target_chance)}: {slip.left_out!.join(", ")}.
          Even their surest picks would pull it under.
        </p>
      )}
      {(slip.from_odds ?? 0) > 0 && (
        <p className="text-xs text-n-500 text-pretty">
          {slip.from_odds} leg{slip.from_odds === 1 ? "" : "s"} on games we don&apos;t price, counted at SportyBet&apos;s odds less their margin.
        </p>
      )}
      <ul className="divide-y divide-n-800 text-sm">
        {slip.picks.map(p => (
          <li key={`${p.home}${p.away}${p.date}`} className="py-2 flex items-center gap-3">
            <div className="min-w-0 flex-1">
              <p className="text-n-0 font-semibold truncate">{p.home} vs {p.away}</p>
              <p className="text-xs text-n-400 truncate">{p.market_name}: <span className="text-n-200">{p.label}</span></p>
            </div>
            <div className="text-right shrink-0">
              <p className="font-bold text-n-0 tnum">{odds(p.odds)}</p>
              <p className="text-[11px] text-n-500 tnum">{p.prob === null ? "kept" : pct(p.prob)}
                {p.odds_source === "estimated" ? " · est." : p.odds_source === "odds_only" ? " · from odds" : ""}</p>
            </div>
          </li>
        ))}
      </ul>
      {code?.code ? (
        <div className="rounded-xl bg-surface-sunken p-3 [box-shadow:inset_0_0_0_1px_rgb(var(--accent)/0.4)] flex flex-wrap items-center gap-3">
          <p className="font-mono text-2xl font-bold tracking-[0.2em] text-n-0">{code.code}</p>
          <button onClick={async () => { try { await navigator.clipboard.writeText(code.code!); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* ignore */ } }}
            className="inline-flex items-center gap-1.5 rounded-lg bg-brand-400 text-ink font-bold px-3 py-1.5 text-sm">
            {copied ? <Check size={14} /> : <Copy size={14} />} {copied ? "Copied" : "Copy"}</button>
          {code.url && <a href={code.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-sm text-n-200">
            <ExternalLink size={14} /> Open on SportyBet</a>}
          <a href={`/optimizer?code=${encodeURIComponent(code.code)}`} className="inline-flex items-center gap-1.5 text-sm text-n-200">
            <Wand2 size={14} /> Refine this code</a>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <button onClick={book} disabled={booking}
            className="flex-1 min-w-[10rem] inline-flex items-center justify-center gap-2 rounded-xl bg-brand-400 hover:bg-brand-300 text-ink font-bold px-4 py-2.5 disabled:opacity-50">
            {booking ? <Loader2 size={15} className="animate-spin" /> : <Ticket size={15} />} Get new code
          </button>
          <button onClick={() => { betSlip.clear(); betSlip.addMany(selections); betSlip.setOpen(true); }}
            className="flex-1 min-w-[10rem] rounded-xl [box-shadow:var(--ring-control)] text-n-200 font-semibold px-4 py-2.5">Put in my bet slip</button>
        </div>
      )}
      {code?.error && <p className="text-xs text-danger">{code.error}</p>}
    </div>
  );
}

/** "Refine": the code as three tickets. All three stay mounted so a code
 * booked on one isn't lost when switching to another. */
function Tickets({ tickets, original, record, notes, settings, onRefine, busy, startOpen }: {
  tickets: NonNullable<Report["tickets"]>; original: Report["original"]; record?: Report["record"];
  notes?: Report["ticket_notes"]; settings: Settings; onRefine: (s: Settings) => void; busy: boolean; startOpen: boolean }) {
  const [open, setOpen] = useState(startOpen);
  const [showSettings, setShowSettings] = useState(false);
  const changed = JSON.stringify(settings) !== JSON.stringify(DEFAULT_SETTINGS);
  const [tab, setTab] = useState<TicketId>("conservative");

  if (!open) {
    return (
      <section className="card p-5 space-y-3 text-center">
        <p className="text-sm text-n-300">Turn your code into three tickets on its games: a 50%, a 25% and a 12.5% chance
          of winning, each at the highest odds we can find for it, with its own SportyBet code. Adjust how in Refine settings.</p>
        <button onClick={() => setOpen(true)}
          className="w-full sm:w-auto inline-flex items-center justify-center gap-2 rounded-xl bg-brand-400 hover:bg-brand-300 text-ink font-bold px-6 py-3">
          <Wand2 size={16} /> Refine into 3 tickets
        </button>
      </section>
    );
  }
  return (
    <section className="space-y-3">
      <div className="card p-4">
        <button type="button" onClick={() => setShowSettings(v => !v)} aria-expanded={showSettings}
          className="w-full flex items-center justify-between gap-2 text-sm font-semibold text-n-0">
          <span className="flex items-center gap-2"><SlidersHorizontal size={15} className="text-accent" /> Refine settings
            {changed && <span className="text-[11px] font-normal text-accent">changed</span>}</span>
          <span className="text-xs text-n-400">{showSettings ? "Hide" : "Show"}</span>
        </button>
        {showSettings && <div className="mt-4"><RefineSettings value={settings} busy={busy} onApply={onRefine} /></div>}
      </div>
      <div className="grid grid-cols-3 gap-2" role="tablist" aria-label="Tickets">
        {TICKETS.map(t => {
          const slip = tickets[t.id];
          const active = tab === t.id;
          if (!slip) {
            return (
              <button key={t.id} role="tab" aria-selected={active} onClick={() => setTab(t.id)}
                className={clsx("card min-w-0 px-2 py-3 sm:p-4 text-left", active ? "card-selected" : "opacity-60")}>
                <p className={clsx("text-xs sm:text-sm font-bold", t.tone)}>{t.name}</p>
                <p className="text-[11px] text-n-400 mt-1">None at {target(leastFor(settings, t.id))}</p>
              </button>
            );
          }
          return (
            <button key={t.id} role="tab" aria-selected={active} onClick={() => setTab(t.id)}
              className={clsx("card min-w-0 px-2 py-3 sm:p-4 text-left transition-[opacity,box-shadow,scale] duration-150 ease-[cubic-bezier(0.2,0,0,1)] active:scale-[0.97]",
                active ? "card-selected" : "opacity-80 hover:opacity-100")}>
              <p className={clsx("flex items-center gap-1.5 text-xs sm:text-sm font-bold", t.tone)}>
                <t.icon size={14} className="shrink-0 hidden sm:block" /> <span className="truncate">{t.name}</span></p>
              <p className="font-display font-extrabold text-xl sm:text-2xl text-n-0 tnum mt-1">{odds(slip.total_odds)}x</p>
              <p className="text-[11px] text-n-400 tnum">{pct(slip.win_chance)} to win</p>
            </button>
          );
        })}
      </div>
      {(() => {
        const rec = record?.[tab];
        const n = rec ? rec.landed + rec.lost : 0;
        return rec && n > 0 && settings.strictness === "balanced" ? (
          <p className="text-xs text-n-400 tnum text-pretty">
            Our {TICKETS.find(t => t.id === tab)!.name.toLowerCase()} tickets so far: landed {rec.landed} of {n}
            {" "}({pct(rec.rate)}), aiming at {target(rec.aim)}.{n < 30 ? " Too few yet to judge." : ""}
          </p>
        ) : null;
      })()}
      {TICKETS.map(t => {
        const slip = tickets[t.id];
        return (
          <div key={t.id} role="tabpanel" hidden={tab !== t.id}>
            {slip ? <ImprovedSlip title={`${t.name} ticket`} original={original} slip={slip}
                blurb={changed ? `At least a ${target(slip.target_chance ?? leastFor(settings, t.id))} chance of winning, within your settings.` : t.blurb} />
              : <p className="card p-4 text-sm text-n-400 text-pretty">{notes?.[t.id] ??
                  `No pick on these games is ${target(leastFor(settings, t.id))} likely on its own, so there's no ${t.name.toLowerCase()} ticket.`}</p>}
          </div>
        );
      })}
    </section>
  );
}

export function CodeCheck({ onLocked, initialCode }: { onLocked?: () => void; initialCode?: string | null }) {
  const authFetch = useAuthedFetch();
  const [input, setInput] = useState(initialCode ?? "");
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  const [refined, setRefined] = useState(false);   // settings applied: show the tickets straight away

  const check = async (withSettings: Settings = settings, keepReport = false) => {
    const code = input.trim().toUpperCase();
    if (!code) return;
    setBusy(true); setError(null);
    if (!keepReport) setReport(null);
    try {
      const r = await authFetch(`${API}/api/optimizer/code`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, settings: withSettings }) });
      if ((r.status === 401 || r.status === 402) && onLocked) { setBusy(false); onLocked(); return; }
      const d = await r.json().catch(() => null);
      if (!r.ok || !d) throw new Error(d?.detail || (r.status >= 500
        ? "Our server had a problem rating that code. Try again in a minute."
        : "Couldn't check that code."));
      setReport(d);
    } catch (e: any) { setError(e?.message || "Couldn't check that code."); }
    setBusy(false);
  };
  const refine = (s: Settings) => { setSettings(s); setRefined(true); check(s, true); };

  // Opened from a booked code ("Refine" on a ticket or slip): check it straight away
  const started = useRef(false);
  useEffect(() => {
    if (initialCode && !started.current) { started.current = true; check(); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialCode]);

  return (
    <div className="space-y-4">
      <section className="card p-5 space-y-3">
        <div>
          <p className="font-semibold text-n-0 flex items-center gap-2"><ScanSearch size={16} className="text-accent" /> Check a SportyBet code</p>
          <p className="text-xs text-n-400 mt-0.5">Paste any booking code. We rate every game with our model, point out the risky ones,
            and build a version with a better chance of winning.</p>
        </div>
        <form className="flex gap-2" onSubmit={e => { e.preventDefault(); setRefined(false); check(); }}>
          <input value={input} onChange={e => setInput(e.target.value.replace(/[^A-Za-z0-9]/g, "").slice(0, 16))}
            placeholder="e.g. 7XK2P9" autoCapitalize="characters" spellCheck={false}
            className="flex-1 min-w-0 rounded-xl bg-surface-sunken border border-n-800 px-4 py-2.5 font-mono tracking-[0.15em] uppercase text-n-0 outline-none focus:border-accent" />
          <button type="submit" disabled={busy || input.trim().length < 4}
            className="shrink-0 inline-flex items-center gap-2 rounded-xl bg-brand-400 hover:bg-brand-300 text-ink font-bold px-4 sm:px-5 disabled:opacity-50">
            {busy ? <Loader2 size={15} className="animate-spin" /> : <ShieldCheck size={15} />} Check
          </button>
        </form>
        {error && <p className="text-sm text-warn flex items-center gap-2"><AlertTriangle size={14} /> {error}</p>}
      </section>

      {report && (
        <>
          <section className="card p-5 space-y-4">
            <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
              <p className="font-semibold text-n-0">Code <span className="font-mono tracking-[0.15em]">{report.code}</span></p>
              <p className="text-sm text-n-400 tnum">{report.original.games} games · {odds(report.original.total_odds)}x ·{" "}
                <span className="text-n-0 font-semibold">{pct(report.original.win_chance)}</span> chance all win
                {(report.original.from_odds ?? report.original.unmodelled) > 0 &&
                  ` (${report.original.from_odds ?? report.original.unmodelled} from SportyBet's odds)`}</p>
            </div>
            <ul className="divide-y divide-n-800">
              {report.legs.map((l, i) => (
                <li key={i} className="py-3 space-y-1.5">
                  <div className="flex items-start gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-[11px] text-n-500 truncate">{l.league} · {l.date} {l.time}</p>
                      <p className="text-sm text-n-0 font-semibold truncate">{l.home} vs {l.away}</p>
                      <p className="text-xs text-n-400">{l.market}: <span className="text-n-200">{l.pick}</span>{!l.active && <span className="text-danger"> · suspended</span>}</p>
                      <p className="text-[11px] text-n-400 tnum mt-0.5">
                        {l.our_prob !== null ? <>Our chance <span className="text-n-0 font-semibold">{pct(l.our_prob)}</span> · </>
                          : l.chance != null ? <>Chance from the odds <span className="text-n-0 font-semibold">{pct(l.chance)}</span> · </> : null}
                        SportyBet&apos;s odds {odds(l.odds)} (implies {pct(l.implied)})
                      </p>
                    </div>
                    <span className={clsx("shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-semibold", VERDICT[l.verdict].cls)}>{VERDICT[l.verdict].label}</span>
                  </div>
                  {l.note && <p className="text-[11px] text-n-500">{l.note}</p>}
                  {l.value !== null && l.value > 0.05 && <p className="text-[11px] text-accent">Good value: the odds pay more than our chance says they should.</p>}
                  {l.suggestion && (
                    <p className="text-xs rounded-lg bg-surface-sunken px-2.5 py-1.5 text-n-300">
                      <ArrowRight size={12} className="text-accent inline -mt-0.5 mr-1" />
                      Better: <span className="text-n-0 font-semibold">{l.suggestion.label}</span> ({l.suggestion.market_name}),
                      {" "}{pct(l.suggestion.prob)} at {odds(l.suggestion.odds)}{l.suggestion.odds_source === "estimated" ? " (est.)" : ""}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          </section>

          {report.tickets ? (
            <Tickets tickets={report.tickets} original={report.original} record={report.record}
              notes={report.ticket_notes} settings={settings} onRefine={refine} busy={busy} startOpen={refined || !!initialCode} />
          ) : !report.same_odds && !report.safest ? (
            <p className="card p-4 text-sm text-n-400 flex items-center gap-2"><CheckCircle2 size={15} />
              None of these games are ones we predict, so there&apos;s nothing to improve.</p>
          ) : (
            <div className="grid gap-4 lg:grid-cols-2">
              {report.same_odds && (
                <ImprovedSlip title="Same odds, better chance" original={report.original} slip={report.same_odds}
                  blurb={`Keeps the total near ${odds(report.same_odds.target_odds)}x and picks the likeliest markets on your games.`} />
              )}
              {report.safest && (
                <ImprovedSlip title="Safest version" original={report.original} slip={report.safest}
                  blurb="Each game's likeliest pick (odds 1.15 or more). Lower odds, much better chance." />
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
