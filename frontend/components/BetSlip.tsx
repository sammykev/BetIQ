"use client";

import { useEffect, useRef, useState } from "react";
import clsx from "clsx";
import {
  Ticket, X, Trash2, Loader2, Copy, Check, ExternalLink, AlertTriangle, CircleSlash,
} from "lucide-react";
import { useUser } from "@clerk/nextjs";
import { useBetSlip } from "@/lib/useBetSlip";
import { useAuthedFetch } from "@/lib/useAuthedFetch";
import {
  bookableOnSportybet, combinedProbability, matchKey, slipAsText, type SlipSelection,
} from "@/lib/slip";

const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

type Platform = "sportybet" | "other";

interface PickResult {
  key: string;
  status: "booked" | "not_found" | "unsupported";
  reason?: string;
  odds: number | null;
}
interface ConvertResult {
  code: string | null;
  share_url: string | null;
  total_odds: number | null;
  picks: PickResult[];
  error: string | null;
}

/** Top-bar button with the slip count. */
export function SlipButton() {
  const { items, setOpen } = useBetSlip();
  const [bump, setBump] = useState(false);
  const prev = useRef(items.length);

  useEffect(() => {
    if (items.length > prev.current) {
      setBump(true);
      const t = setTimeout(() => setBump(false), 350);
      prev.current = items.length;
      return () => clearTimeout(t);
    }
    prev.current = items.length;
  }, [items.length]);

  return (
    <button
      onClick={() => setOpen(true)}
      aria-label={`Bet slip, ${items.length} ${items.length === 1 ? "pick" : "picks"}`}
      className={clsx(
        "relative inline-flex items-center gap-1.5 h-9 rounded-lg px-2.5 text-sm font-semibold transition-all",
        items.length ? "bg-brand-400 text-ink hover:bg-brand-300" : "text-n-300 hover:text-n-0 hover:bg-n-800",
        bump && "scale-110"
      )}
    >
      <Ticket size={16} />
      <span className="hidden sm:inline">Slip</span>
      {items.length > 0 && <span className="tnum font-display font-extrabold text-base leading-none">{items.length}</span>}
    </button>
  );
}

function SlipRow({ s, result, onRemove }: { s: SlipSelection; result?: PickResult; onRemove: () => void }) {
  const blocked = result && result.status !== "booked";
  return (
    <li className={clsx("rounded-xl border px-3 py-2.5", blocked ? "border-amber-400/40 bg-amber-400/[0.05]" : "border-n-800 bg-surface-raised")}>
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-[11px] text-n-500 truncate">{s.home} vs {s.away}{s.league ? ` · ${s.league}` : ""}</p>
          <p className="text-sm font-semibold text-n-0 truncate">{s.label}</p>
          <p className="text-[11px] text-n-400">{s.marketName}</p>
        </div>
        <div className="text-right shrink-0">
          {result?.odds ? (
            <p className="font-mono text-sm font-bold text-n-0">{result.odds.toFixed(2)}</p>
          ) : typeof s.prob === "number" ? (
            <p className="tnum text-sm font-bold text-n-200">{Math.round(s.prob * 100)}%</p>
          ) : null}
          {typeof s.prob === "number" && result?.odds && <p className="tnum text-[10px] text-n-500">{Math.round(s.prob * 100)}% model</p>}
        </div>
        <button onClick={onRemove} aria-label={`Remove ${s.home} vs ${s.away}`}
          className="p-1 -mr-1 text-n-500 hover:text-danger transition-colors shrink-0">
          <X size={15} />
        </button>
      </div>
      {blocked && (
        <p className="mt-1.5 flex items-center gap-1.5 text-[11px] text-warn">
          <CircleSlash size={11} /> {result.reason}
        </p>
      )}
    </li>
  );
}

function CopyButton({ text, label, primary }: { text: string; label: string; primary?: boolean }) {
  const [done, setDone] = useState(false);
  return (
    <button
      onClick={async () => {
        try { await navigator.clipboard.writeText(text); setDone(true); setTimeout(() => setDone(false), 2000); } catch { /* ignore */ }
      }}
      className={clsx(primary ? "btn-primary" : "btn-secondary", "!text-sm")}
    >
      {done ? <Check size={14} /> : <Copy size={14} />}
      {done ? "Copied" : label}
    </button>
  );
}

/** The slip drawer: picks, combined probability, and booking. */
export function SlipDrawer() {
  const { items, remove, clear, open, setOpen } = useBetSlip();
  const [platform, setPlatform] = useState<Platform>("sportybet");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ConvertResult | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const panel = useRef<HTMLDivElement>(null);
  const { user } = useUser();
  const authFetch = useAuthedFetch();

  // Any change to the slip makes an old booking code stale
  const signature = items.map(s => `${matchKey(s)}:${s.market}:${s.code}`).join("|");
  useEffect(() => { setResult(null); setFailed(null); }, [signature, platform]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    panel.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [open, setOpen]);

  if (!open) return null;

  const prob = combinedProbability(items);
  const byKey = new Map((result?.picks ?? []).map(p => [p.key, p]));
  const notBookable = items.filter(s => !bookableOnSportybet(s)).length;

  const book = async () => {
    setBusy(true); setFailed(null); setResult(null);
    try {
      const res = await fetch(`${API}/api/booking/convert`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ platform: "sportybet", selections: items }),
      });
      const data = await res.json();
      if (!res.ok || !Array.isArray(data?.picks)) throw new Error(data?.detail || "failed");
      setResult(data);
      // Keep it in the user's code history (Dashboard → Codes), like the chat's codes
      if (data.code && user?.id) {
        const booked = new Map((data.picks as PickResult[]).map(p => [p.key, p]));
        authFetch(`${API}/api/user/codes`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            uid: user.id,
            entry: {
              code: data.code, total_odds: data.total_odds, date: new Date().toISOString().slice(0, 10),
              games: items.filter(s => booked.get(matchKey(s))?.status === "booked").map(s => ({
                game: `${s.home} vs ${s.away}`, tip: s.label, odds: booked.get(matchKey(s))?.odds ?? null,
              })),
            },
          }),
        }).catch(() => {});
      }
    } catch (e) {
      setFailed(e instanceof Error && e.message !== "failed" ? e.message : "Couldn't reach the booking service. Try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-labelledby="slip-title">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-[2px] animate-fade-in" onClick={() => setOpen(false)} />
      <div
        ref={panel}
        tabIndex={-1}
        className="absolute inset-x-0 bottom-0 max-h-[88vh] sm:inset-y-0 sm:left-auto sm:right-0 sm:max-h-none sm:w-[420px] flex flex-col bg-surface border-t sm:border-t-0 sm:border-l border-n-800 rounded-t-2xl sm:rounded-none shadow-pop outline-none animate-slide-up"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-n-800">
          <div>
            <p className="eyebrow">Booking cart</p>
            <h2 id="slip-title" className="font-display font-extrabold uppercase text-2xl leading-none text-n-0 mt-0.5">
              Bet slip <span className="text-accent tnum">{items.length}</span>
            </h2>
          </div>
          <div className="flex items-center gap-1">
            {items.length > 0 && (
              <button onClick={clear} className="inline-flex items-center gap-1 text-xs font-semibold text-n-400 hover:text-danger px-2 py-1.5 rounded-lg transition-colors">
                <Trash2 size={13} /> Clear
              </button>
            )}
            <button onClick={() => setOpen(false)} aria-label="Close bet slip" className="p-2 rounded-lg text-n-400 hover:text-n-0 hover:bg-n-800 transition-colors">
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Picks */}
        <div className="flex-1 overflow-y-auto px-4 py-4">
          {items.length === 0 ? (
            <div className="text-center py-14 px-6">
              <Ticket size={28} className="mx-auto text-n-600" />
              <p className="font-display font-bold uppercase text-lg text-n-0 mt-3">Your slip is empty</p>
              <p className="text-sm text-n-400 mt-1">Tap <span className="font-semibold text-n-200">+ Slip</span> on a prediction, or any market on a match page, to add it here.</p>
            </div>
          ) : (
            <ul className="space-y-2">
              {items.map(s => (
                <SlipRow key={matchKey(s)} s={s} result={byKey.get(matchKey(s))} onRemove={() => remove(matchKey(s))} />
              ))}
            </ul>
          )}
        </div>

        {/* Book */}
        {items.length > 0 && (
          <div className="border-t border-n-800 px-5 py-4 space-y-3 bg-surface-raised">
            <div className="flex items-end justify-between gap-3">
              <div>
                <p className="eyebrow">All {items.length} win</p>
                <p className="font-display font-extrabold text-3xl leading-none text-n-0 tnum mt-0.5">
                  {prob === null ? "–" : `${(prob * 100).toFixed(prob < 0.1 ? 1 : 0)}%`}
                </p>
                <p className="text-[10px] text-n-500 mt-0.5">Model estimate, matches treated as independent</p>
              </div>
              {result?.total_odds && (
                <div className="text-right">
                  <p className="eyebrow">Code odds</p>
                  <p className="font-display font-extrabold text-3xl leading-none text-accent tnum mt-0.5">{result.total_odds.toFixed(2)}</p>
                </div>
              )}
            </div>

            <div role="radiogroup" aria-label="Bookmaker" className="grid grid-cols-2 gap-1 p-1 rounded-xl bg-surface-sunken">
              {([["sportybet", "SportyBet"], ["other", "Other bookmaker"]] as const).map(([id, label]) => (
                <button key={id} role="radio" aria-checked={platform === id} onClick={() => setPlatform(id)}
                  className={clsx("rounded-lg py-2 text-sm font-semibold transition-colors",
                    platform === id ? "bg-surface text-n-0 shadow-sm" : "text-n-400 hover:text-n-200")}>
                  {label}
                </button>
              ))}
            </div>

            {platform === "other" ? (
              <div className="space-y-2">
                <p className="text-xs text-n-400">
                  Bet9ja, 1xBet, BetKing and others don&apos;t offer booking codes to outside apps yet. Copy your picks and add them in the bookmaker&apos;s app.
                </p>
                <CopyButton text={slipAsText(items)} label="Copy picks" primary />
              </div>
            ) : result?.code ? (
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-2 rounded-xl border border-brand-400/50 bg-brand-400/10 pl-4 pr-2 py-2">
                  <div>
                    <p className="eyebrow">SportyBet code</p>
                    <p className="font-mono font-bold text-xl tracking-[0.18em] text-n-0">{result.code}</p>
                  </div>
                  <CopyButton text={result.code} label="Copy" primary />
                </div>
                {result.share_url && (
                  <a href={result.share_url} target="_blank" rel="noopener noreferrer" className="btn-secondary w-full !text-sm justify-center">
                    <ExternalLink size={14} /> Open on SportyBet
                  </a>
                )}
                {result.picks.some(p => p.status !== "booked") && (
                  <p className="text-[11px] text-warn">Highlighted picks aren&apos;t in this code — add them on SportyBet by hand.</p>
                )}
              </div>
            ) : (
              <div className="space-y-2">
                {notBookable > 0 && (
                  <p className="text-[11px] text-warn flex items-start gap-1.5">
                    <AlertTriangle size={12} className="mt-px shrink-0" />
                    {notBookable === 1 ? "1 pick is" : `${notBookable} picks are`} in a market SportyBet codes can&apos;t include; they&apos;ll be left out.
                  </p>
                )}
                {(failed || result?.error) && <p className="text-xs text-danger">{failed || result?.error}</p>}
                <button onClick={book} disabled={busy || notBookable === items.length} className="btn-primary w-full justify-center">
                  {busy ? <Loader2 size={15} className="animate-spin" /> : <Ticket size={15} />}
                  {busy ? "Booking on SportyBet…" : "Get SportyBet code"}
                </button>
              </div>
            )}
            <p className="text-[10px] text-n-500 text-center">18+ · Odds can change before you place the bet. Bet responsibly.</p>
          </div>
        )}
      </div>
    </div>
  );
}
