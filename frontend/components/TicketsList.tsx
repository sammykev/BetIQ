"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import { Check, ChevronDown, Clock, Copy, ExternalLink, HelpCircle, Loader2, Ticket as TicketIcon } from "lucide-react";
import { VerdictIcon } from "@/components/MatchdayList";
import { API, type Ticket, type TicketLeg, type TicketSummary } from "@/lib/matchday";

// The account's booking codes, settled leg by leg from the results
// (backend tickets.py): every code made on BetIQ while signed in.

const STATUS: Record<Ticket["status"], { label: string; cls: string }> = {
  won: { label: "Won", cls: "bg-accent/15 text-accent border-accent/30" },
  lost: { label: "Lost", cls: "bg-danger/15 text-danger border-danger/30" },
  void: { label: "Void", cls: "bg-n-800 text-n-300 border-n-700" },
  pending: { label: "Pending", cls: "bg-surface-sunken text-n-300 border-n-800" },
  open: { label: "Awaiting SportyBet", cls: "bg-info/10 text-info border-info/30" },
};
const SOURCE: Record<string, string> = {
  slip: "Bet slip", optimizer: "Optimizer", code_check: "Code check", chat: "AI assistant", match: "Match page", other: "BetIQ",
};

interface OldCode { code: string; games?: { game: string; tip: string; odds: string | number | null }[]; total_odds?: number; date?: string }

function LegRow({ leg }: { leg: TicketLeg }) {
  return (
    <li className="flex items-center gap-2.5 py-2 text-sm">
      <span className="w-5 flex justify-center shrink-0">
        {leg.status === "won" || leg.status === "lost" || leg.status === "void"
          ? <VerdictIcon verdict={leg.status} size={11} />
          : leg.status === "unknown"
            ? <HelpCircle size={14} className="text-n-500" aria-label="We can't settle this market" />
            : <Clock size={14} className="text-n-500" aria-label="Not played yet" />}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-n-200">{leg.home} vs {leg.away}</span>
        <span className="block truncate text-[11px] text-n-500">{leg.marketName ? `${leg.marketName}: ` : ""}<span className="text-n-0 font-semibold">{leg.label || leg.code}</span></span>
      </span>
      <span className="font-mono text-xs text-n-400 tnum shrink-0">{leg.odds ? leg.odds.toFixed(2) : "—"}</span>
    </li>
  );
}

export function TicketsList({ uid, authFetch }: { uid: string; authFetch: (url: string, init?: RequestInit) => Promise<Response> }) {
  const [data, setData] = useState<{ tickets: Ticket[]; summary: TicketSummary; older: OldCode[] } | null>(null);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    if (!uid) return;
    setLoading(true);
    authFetch(`${API}/api/user/tickets?uid=${encodeURIComponent(uid)}`)
      .then(r => (r.ok ? r.json() : null)).then(setData).catch(() => setData(null)).finally(() => setLoading(false));
  }, [uid, authFetch]);

  const copy = async (code: string) => {
    try { await navigator.clipboard.writeText(code); setCopied(code); setTimeout(() => setCopied(null), 1500); } catch { /* ignore */ }
  };

  if (loading) return <div className="card flex items-center justify-center gap-2 py-12 text-sm text-n-400"><Loader2 size={15} className="animate-spin" /> Loading tickets…</div>;
  const tickets = data?.tickets ?? [];
  const older = data?.older ?? [];
  const s = data?.summary;

  const card = (t: Ticket) => {
    const done = t.legs.filter(l => l.status !== "pending" && l.status !== "unknown").length;
    return (
      <article key={t.code} className={clsx("card overflow-hidden", t.status === "won" && "border-accent/40", t.status === "lost" && "border-danger/30")}>
        <header className="flex items-start justify-between gap-3 p-4">
          <div className="min-w-0">
            <p className="font-mono text-2xl font-bold text-n-0 tracking-[0.18em]">{t.code}</p>
            <p className="text-[11px] text-n-500 mt-0.5">
              {SOURCE[t.source] ?? "BetIQ"} · {new Date(t.created_at).toLocaleDateString(undefined, { day: "numeric", month: "short" })}
              {" · "}{done}/{t.legs.length} settled
            </p>
          </div>
          <div className="text-right shrink-0 space-y-1">
            <span className={clsx("inline-block rounded-full border px-2 py-0.5 text-[11px] font-bold", STATUS[t.status].cls)}>{STATUS[t.status].label}</span>
            {t.total_odds && <p className="font-display font-extrabold text-2xl text-n-0 leading-none tnum">{t.total_odds.toFixed(2)}x</p>}
          </div>
        </header>
        <ul className="px-4 border-t border-dashed border-n-700 divide-y divide-n-800/70">
          {t.legs.map((l, i) => <LegRow key={i} leg={l} />)}
        </ul>
        <footer className="flex items-center justify-between gap-2 px-4 py-3 border-t border-n-800">
          {t.share_url ? (
            <a href={t.share_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-xs text-n-300 hover:text-n-0">
              <ExternalLink size={12} /> Open on SportyBet</a>
          ) : <span />}
          <button onClick={() => copy(t.code)} className="btn-primary !px-3 !py-1.5 !text-xs !rounded-lg">
            {copied === t.code ? <Check size={12} /> : <Copy size={12} />}{copied === t.code ? "Copied" : "Copy code"}
          </button>
        </footer>
      </article>
    );
  };
  // Open tickets up top; settled ones (won / lost / void) in the archive below
  const active = tickets.filter(t => t.status === "pending" || t.status === "open");
  const settled = tickets.filter(t => t.status !== "pending" && t.status !== "open");

  if (!tickets.length && !older.length) {
    return (
      <div className="card border-dashed text-center px-6 py-14 space-y-2">
        <TicketIcon size={20} className="mx-auto text-n-400" />
        <p className="font-display font-bold text-xl uppercase tracking-wide text-n-0">No tickets yet</p>
        <p className="text-sm text-n-400 max-w-sm mx-auto">Every SportyBet code you generate here (bet slip, optimizer, code check) is saved and settled automatically as the matches finish.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {s && s.tickets > 0 && (
        <div className="card px-4 py-3 flex flex-wrap gap-x-6 gap-y-2 text-sm">
          <span><span className="text-n-500">Tickets won </span><span className="font-bold text-n-0 tnum">{s.won}/{s.won + s.lost}</span></span>
          <span><span className="text-n-500">Legs won </span><span className="font-bold text-n-0 tnum">{s.legs_won}/{s.legs_won + s.legs_lost}</span></span>
          <span><span className="text-n-500">Still open </span><span className="font-bold text-n-0 tnum">{s.pending}</span></span>
        </div>
      )}
      {active.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">{active.map(card)}</div>
      ) : (
        <p className="card px-4 py-6 text-center text-sm text-n-400">No open tickets. Settled ones are below.</p>
      )}
      {settled.length > 0 && (
        <details className="group">
          <summary className="card px-4 py-3 cursor-pointer list-none flex items-center justify-between text-sm font-semibold text-n-200">
            <span>Settled tickets ({settled.length}) · {settled.filter(t => t.status === "won").length} won</span>
            <ChevronDown size={16} className="text-n-400 transition-transform group-open:rotate-180" />
          </summary>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">{settled.map(card)}</div>
        </details>
      )}

      {tickets.some(t => t.status === "open") && (
        <p className="text-[11px] text-n-500">&quot;Awaiting SportyBet&quot;: every leg we can settle has won; the rest are markets from a pasted code that only SportyBet can settle.</p>
      )}

      {older.length > 0 && (
        <details className="card p-4">
          <summary className="cursor-pointer text-sm font-semibold text-n-200">Older codes ({older.length}) · saved before tracking</summary>
          <ul className="mt-3 divide-y divide-n-800 text-sm">
            {older.map((c, i) => (
              <li key={`${c.code}${i}`} className="py-2 flex items-center gap-3">
                <span className="font-mono font-bold text-n-0 tracking-[0.15em]">{c.code}</span>
                <span className="text-[11px] text-n-500 truncate flex-1">{(c.games ?? []).map(g => g.game).join(" · ")}</span>
                {c.total_odds ? <span className="tnum text-xs text-n-400">{c.total_odds}x</span> : null}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
