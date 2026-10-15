"use client";

import { useState } from "react";
import Link from "next/link";
import clsx from "clsx";
import { Check, Copy } from "lucide-react";
import { LegRow, STATUS } from "@/components/TicketsList";
import type { SharedTicket } from "@/lib/share";

/** A ticket someone shared: its legs as they stand, and its code to copy. */
export function SharedTicketCard({ ticket: t }: { ticket: SharedTicket }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(t.code); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* ignore */ }
  };
  const done = t.legs.filter(l => l.status !== "pending" && l.status !== "unknown").length;
  return (
    <div className="space-y-4">
      <article className={clsx("card overflow-hidden", t.status === "won" && "card-won", t.status === "lost" && "card-lost")}>
        <header className="flex items-start justify-between gap-3 p-4">
          <div className="min-w-0">
            <p className="text-[11px] uppercase tracking-wide text-n-500 font-semibold">Shared BetIQ ticket</p>
            <p className="font-mono text-2xl font-bold text-n-0 tracking-[0.18em]">{t.code}</p>
            <p className="text-[11px] text-n-500 mt-0.5">
              {new Date(t.created_at).toLocaleDateString(undefined, { day: "numeric", month: "short" })} · {done}/{t.legs.length} settled
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
        <footer className="flex items-center justify-end px-4 py-3 border-t border-n-800">
          <button onClick={copy} className="btn-primary !px-3 !py-1.5 !text-xs !rounded-lg">
            {copied ? <Check size={12} /> : <Copy size={12} />}{copied ? "Copied" : "Copy code"}
          </button>
        </footer>
      </article>
      <p className="text-center text-sm text-n-400">
        Build your own on <Link href="/" className="text-accent font-semibold hover:underline">BetIQ</Link>. 18+ · Bet responsibly.
      </p>
    </div>
  );
}
