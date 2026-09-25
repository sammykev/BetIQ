"use client";

import { useState } from "react";
import clsx from "clsx";
import { AlertTriangle, ArrowRight, Check, CheckCircle2, Copy, ExternalLink, Flame, Loader2, Scale, ScanSearch, Shield, ShieldCheck,
  Ticket, Wand2 } from "lucide-react";
import { useBetSlip } from "@/lib/useBetSlip";
import type { SlipSelection } from "@/lib/slip";

// Paste a SportyBet booking code: backend/code_check.py scores every leg
// with the model, suggests better picks, and "Refine" turns it into three
// tickets (safe, conservative, risky), each bookable as a new code.

const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

interface Leg {
  home: string; away: string; date: string; time: string; league: string;
  market: string; pick: string; odds: number | null; active: boolean;
  our_prob: number | null; implied: number | null; value: number | null;
  verdict: "strong" | "fair" | "risky" | "not_modelled"; note?: string;
  suggestion: { market_name: string; label: string; prob: number; odds: number; odds_source: string } | null;
}
interface Pick {
  home: string; away: string; date: string; time: string; league: string;
  market: string; market_name: string; code: string; label: string;
  prob: number | null; odds: number | null; odds_source: string;
  sb?: SlipSelection["sb"];
}
interface Slip { picks: Pick[]; games: number; total_odds: number | null; win_chance: number | null;
  unmodelled: number; estimated_prices: number; target_odds?: number }
interface Report {
  code: string; legs: Leg[];
  original: { games: number; total_odds: number | null; win_chance: number | null; unmodelled: number };
  same_odds: Slip | null; safest: Slip | null;
  tickets?: { safe: Slip; conservative: Slip; risky: Slip } | null;
}

const TICKETS = [
  { id: "safe", name: "Safe", icon: Shield, tone: "text-accent",
    blurb: "Each game's likeliest pick (odds 1.15 or more). Small payout, best chance." },
  { id: "conservative", name: "Conservative", icon: Scale, tone: "text-info",
    blurb: "Each game's likeliest pick that still pays 1.40 or more. A balance of payout and chance." },
  { id: "risky", name: "Risky", icon: Flame, tone: "text-warn",
    blurb: "A big payout (your code's odds, or double the conservative ticket's) with the best chance we can find for it." },
] as const;
type TicketId = (typeof TICKETS)[number]["id"];

const odds = (x: number | null | undefined) => (x ? x.toFixed(2) : "—");
const pct = (x: number | null | undefined) =>
  x === null || x === undefined ? "—" : x >= 0.1 ? `${Math.round(x * 100)}%` : x >= 0.001 ? `${(x * 100).toFixed(1)}%` : "<0.1%";

const VERDICT: Record<Leg["verdict"], { label: string; cls: string }> = {
  strong: { label: "Strong", cls: "text-accent border-accent/30 bg-accent/10" },
  fair: { label: "Fair", cls: "text-info border-info/30 bg-info/10" },
  risky: { label: "Risky", cls: "text-danger border-danger/30 bg-danger/10" },
  not_modelled: { label: "Not rated", cls: "text-n-400 border-n-800 bg-surface-sunken" },
};

function toSelection(p: Pick): SlipSelection {
  return { home: p.home, away: p.away, date: p.date, time: p.time, league: p.league,
           market: p.market, marketName: p.market_name, code: p.code, label: p.label, prob: p.prob,
           ...(p.sb ? { sb: p.sb } : {}) };
}

function ImprovedSlip({ title, blurb, slip, original }: { title: string; blurb: string; slip: Slip; original: Report["original"] }) {
  const betSlip = useBetSlip();
  const [booking, setBooking] = useState(false);
  const [code, setCode] = useState<{ code: string | null; url: string | null; error: string | null } | null>(null);
  const [copied, setCopied] = useState(false);
  const selections = slip.picks.map(toSelection);

  const book = async () => {
    setBooking(true); setCode(null);
    try {
      const r = await fetch(`${API}/api/booking/convert`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ platform: "sportybet", selections }) });
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
          {better >= 2 ? `${better.toFixed(1)}×` : `${Math.round((better - 1) * 100)}%`} more likely to win than your code
          {slip.unmodelled > 0 && ` (${slip.unmodelled} leg${slip.unmodelled === 1 ? "" : "s"} we don't rate kept as is)`}</p>
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
              <p className="text-[11px] text-n-500 tnum">{p.prob === null ? "kept" : pct(p.prob)}{p.odds_source === "estimated" ? " · est." : ""}</p>
            </div>
          </li>
        ))}
      </ul>
      {code?.code ? (
        <div className="rounded-xl border border-accent/40 bg-surface-sunken p-3 flex flex-wrap items-center gap-3">
          <p className="font-mono text-2xl font-bold tracking-[0.2em] text-n-0">{code.code}</p>
          <button onClick={async () => { try { await navigator.clipboard.writeText(code.code!); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* ignore */ } }}
            className="inline-flex items-center gap-1.5 rounded-lg bg-brand-400 text-ink font-bold px-3 py-1.5 text-sm">
            {copied ? <Check size={14} /> : <Copy size={14} />} {copied ? "Copied" : "Copy"}</button>
          {code.url && <a href={code.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-sm text-n-200">
            <ExternalLink size={14} /> Open on SportyBet</a>}
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <button onClick={book} disabled={booking}
            className="flex-1 min-w-[10rem] inline-flex items-center justify-center gap-2 rounded-xl bg-brand-400 hover:bg-brand-300 text-ink font-bold px-4 py-2.5 disabled:opacity-50">
            {booking ? <Loader2 size={15} className="animate-spin" /> : <Ticket size={15} />} Get new code
          </button>
          <button onClick={() => { betSlip.clear(); betSlip.addMany(selections); betSlip.setOpen(true); }}
            className="flex-1 min-w-[10rem] rounded-xl border border-n-700 text-n-200 font-semibold px-4 py-2.5">Put in my bet slip</button>
        </div>
      )}
      {code?.error && <p className="text-xs text-danger">{code.error}</p>}
    </div>
  );
}

/** "Refine": the code as three tickets. All three stay mounted so a code
 * booked on one isn't lost when switching to another. */
function Tickets({ tickets, original }: { tickets: NonNullable<Report["tickets"]>; original: Report["original"] }) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<TicketId>("conservative");

  if (!open) {
    return (
      <section className="card p-5 space-y-3 text-center">
        <p className="text-sm text-n-300">Turn your code into three tickets on the same games, from safe to risky,
          each with its own SportyBet code.</p>
        <button onClick={() => setOpen(true)}
          className="w-full sm:w-auto inline-flex items-center justify-center gap-2 rounded-xl bg-brand-400 hover:bg-brand-300 text-ink font-bold px-6 py-3">
          <Wand2 size={16} /> Refine into 3 tickets
        </button>
      </section>
    );
  }
  return (
    <section className="space-y-3">
      <div className="grid grid-cols-3 gap-2" role="tablist" aria-label="Tickets">
        {TICKETS.map(t => {
          const slip = tickets[t.id];
          const active = tab === t.id;
          return (
            <button key={t.id} role="tab" aria-selected={active} onClick={() => setTab(t.id)}
              className={clsx("card min-w-0 px-2 py-3 sm:p-4 text-left transition-colors border",
                active ? "border-accent/60 bg-surface" : "border-transparent opacity-80 hover:opacity-100")}>
              <p className={clsx("flex items-center gap-1.5 text-xs sm:text-sm font-bold", t.tone)}>
                <t.icon size={14} className="shrink-0 hidden sm:block" /> <span className="truncate">{t.name}</span></p>
              <p className="font-display font-extrabold text-xl sm:text-2xl text-n-0 tnum mt-1">{odds(slip.total_odds)}x</p>
              <p className="text-[11px] text-n-400 tnum">{pct(slip.win_chance)} to win</p>
            </button>
          );
        })}
      </div>
      {TICKETS.map(t => (
        <div key={t.id} role="tabpanel" hidden={tab !== t.id}>
          <ImprovedSlip title={`${t.name} ticket`} blurb={t.blurb} slip={tickets[t.id]} original={original} />
        </div>
      ))}
    </section>
  );
}

export function CodeCheck() {
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);

  const check = async () => {
    const code = input.trim().toUpperCase();
    if (!code) return;
    setBusy(true); setError(null); setReport(null);
    try {
      const r = await fetch(`${API}/api/optimizer/code`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }) });
      const d = await r.json();
      if (!r.ok) throw new Error(d?.detail || "Couldn't check that code.");
      setReport(d);
    } catch (e: any) { setError(e?.message || "Couldn't check that code."); }
    setBusy(false);
  };

  return (
    <div className="space-y-4">
      <section className="card p-5 space-y-3">
        <div>
          <p className="font-semibold text-n-0 flex items-center gap-2"><ScanSearch size={16} className="text-accent" /> Check a SportyBet code</p>
          <p className="text-xs text-n-400 mt-0.5">Paste any booking code. We rate every game with our model, point out the risky ones,
            and build a version with a better chance of winning.</p>
        </div>
        <form className="flex gap-2" onSubmit={e => { e.preventDefault(); check(); }}>
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
                {report.original.unmodelled > 0 && ` (${report.original.unmodelled} not rated)`}</p>
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
                        {l.our_prob !== null ? <>Our chance <span className="text-n-0 font-semibold">{pct(l.our_prob)}</span> · </> : null}
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
            <Tickets tickets={report.tickets} original={report.original} />
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
