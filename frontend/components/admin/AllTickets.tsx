"use client";

import { Fragment, useEffect, useState } from "react";
import clsx from "clsx";
import { Check, ChevronDown, Copy, ExternalLink, Search } from "lucide-react";
import { Pill, Skeleton, ago, inputClass, num, useAdmin } from "./ui";

// Every booking code made on the site, newest first, with the account that
// made it (backend /api/admin/tickets/all). Tap a code for its picks.

const STATUSES = [["", "All"], ["open", "Open"], ["won", "Won"], ["lost", "Lost"], ["void", "Void"]] as const;
const SOURCES: Record<string, string> = {
  slip: "Bet slip", optimizer: "Optimizer", code_check: "Code check", chat: "AI chat", match: "Match page",
  daily: "Daily odds", other: "Other",
};
const STATUS_TONE: Record<string, "ok" | "danger" | "muted" | "info"> = { won: "ok", lost: "danger", void: "muted", open: "info" };
const LEG_TONE: Record<string, string> = { won: "text-accent", lost: "text-danger", void: "text-n-500" };

interface Leg { home: string; away: string; date?: string; time?: string; label?: string; marketName?: string; odds?: number | null; status?: string }
interface Ticket {
  code: string; created_at: string; source?: string; share_url?: string; status: string; total_odds?: number | null;
  legs: Leg[]; uid: string; name: string; email: string;
}

export function AllTickets({ sources }: { sources?: Record<string, number> }) {
  const { get } = useAdmin();
  const [status, setStatus] = useState("");
  const [source, setSource] = useState("");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [data, setData] = useState<{ total: number; tickets: Ticket[] } | null | "error">(null);
  const [open, setOpen] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  // Search as you type, a moment after the last key
  useEffect(() => { const t = setTimeout(() => setQuery(q.trim()), 350); return () => clearTimeout(t); }, [q]);
  useEffect(() => {
    setData(null);
    const params = new URLSearchParams({ limit: "300", ...(status && { status }), ...(source && { source }), ...(query && { q: query }) });
    get(`/api/admin/tickets/all?${params}`).then(d => setData(d ?? "error"));
  }, [get, status, source, query]);

  const copy = async (code: string) => {
    try { await navigator.clipboard.writeText(code); setCopied(code); setTimeout(() => setCopied(null), 1400); } catch { /* ignore */ }
  };

  return (
    <div className="space-y-3 border-t border-n-800 pt-3">
      <div className="flex flex-wrap gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-n-500" />
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="Code, name or email" className={`${inputClass} pl-9`} />
        </div>
        <select value={source} onChange={e => setSource(e.target.value)} aria-label="Source"
          className="rounded-lg bg-surface-sunken border border-n-800 px-2.5 py-2 text-xs text-n-200">
          <option value="">Every source</option>
          {Object.keys(sources ?? SOURCES).map(s => <option key={s} value={s}>{SOURCES[s] ?? s}</option>)}
        </select>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {STATUSES.map(([v, label]) => (
          <button key={v} onClick={() => setStatus(v)} className={status === v ? "chip chip-active" : "chip chip-idle"}>{label}</button>
        ))}
      </div>

      {data === null ? <Skeleton rows={5} /> : data === "error" ? <p className="text-xs text-warn">Couldn&apos;t load the codes.</p> : (
        <>
          <p className="text-[11px] text-n-500">{num(data.total)} code{data.total === 1 ? "" : "s"}{data.total > data.tickets.length ? `, newest ${data.tickets.length} shown` : ""}</p>
          {data.tickets.length === 0 ? <p className="text-xs text-n-400">No codes match.</p> : (
            <div className="overflow-x-auto -mx-4 sm:mx-0">
              <table className="w-full min-w-[640px] text-xs">
                <thead>
                  <tr className="text-left text-n-500 border-b border-n-800">
                    <th className="py-2 px-4 sm:px-2 font-medium">Code</th>
                    <th className="py-2 px-2 font-medium">Made by</th>
                    <th className="py-2 px-2 font-medium">When</th>
                    <th className="py-2 px-2 font-medium">From</th>
                    <th className="py-2 px-2 font-medium text-right">Picks</th>
                    <th className="py-2 px-2 font-medium text-right">Odds</th>
                    <th className="py-2 px-2 font-medium">Status</th>
                    <th className="py-2 px-2" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-n-800">
                  {data.tickets.map(t => {
                    const key = `${t.uid}:${t.code}`;
                    const expanded = open === key;
                    const settled = t.legs.filter(l => l.status && !["pending", "unknown"].includes(l.status)).length;
                    return (
                      <Fragment key={key}>
                        <tr onClick={() => setOpen(expanded ? null : key)} className="cursor-pointer hover:bg-surface-sunken align-top">
                          <td className="py-2 px-4 sm:px-2 font-mono font-bold text-n-0 tracking-wider whitespace-nowrap">{t.code}</td>
                          <td className="py-2 px-2 min-w-0">
                            <p className="text-n-0 truncate max-w-[180px]">{t.name || t.email || "Unknown account"}</p>
                            {t.name && t.email && <p className="text-n-500 truncate max-w-[180px]">{t.email}</p>}
                          </td>
                          <td className="py-2 px-2 text-n-400 whitespace-nowrap" title={t.created_at}>{ago(t.created_at)}</td>
                          <td className="py-2 px-2 text-n-300 whitespace-nowrap">{SOURCES[t.source ?? "other"] ?? t.source}</td>
                          <td className="py-2 px-2 text-right text-n-300 tnum">{t.legs.length}{t.status === "open" && settled ? <span className="text-n-500"> · {settled} in</span> : null}</td>
                          <td className="py-2 px-2 text-right text-n-0 tnum font-semibold">{t.total_odds ? t.total_odds.toFixed(2) : "—"}</td>
                          <td className="py-2 px-2"><Pill tone={STATUS_TONE[t.status] ?? "muted"}>{t.status === "open" ? "Open" : t.status[0].toUpperCase() + t.status.slice(1)}</Pill></td>
                          <td className="py-2 px-2 text-right"><ChevronDown size={14} className={clsx("inline text-n-500 transition-transform", expanded && "rotate-180")} /></td>
                        </tr>
                        {expanded && (
                          <tr className="bg-surface-sunken">
                            <td colSpan={8} className="px-4 sm:px-3 py-3">
                              {/* On a phone the table scrolls sideways: keep the details on screen */}
                              <div className="sticky left-4 max-w-[calc(100vw-4rem)] sm:max-w-none space-y-2">
                              <ul className="space-y-1.5">
                                {t.legs.map((l, i) => (
                                  <li key={i} className="flex items-start justify-between gap-3">
                                    <span className="min-w-0">
                                      <span className="block text-n-0">{l.label || l.marketName}</span>
                                      <span className="block text-[11px] text-n-500">{l.home} v {l.away}{l.date ? ` · ${l.date}${l.time ? ` ${l.time}` : ""}` : ""}</span>
                                    </span>
                                    <span className="shrink-0 text-right tnum">
                                      <span className="text-n-200">{l.odds ? l.odds.toFixed(2) : "—"}</span>
                                      <span className={clsx("block text-[11px] capitalize", LEG_TONE[l.status ?? ""] ?? "text-n-400")}>
                                        {l.status === "unknown" ? "not graded" : l.status ?? "pending"}</span>
                                    </span>
                                  </li>
                                ))}
                              </ul>
                              <div className="flex flex-wrap items-center gap-2 pt-1">
                                <button onClick={e => { e.stopPropagation(); copy(t.code); }}
                                  className="inline-flex items-center gap-1 rounded-lg border border-n-800 bg-surface px-2.5 py-1.5 text-xs text-n-200 hover:text-n-0">
                                  {copied === t.code ? <Check size={12} /> : <Copy size={12} />}{copied === t.code ? "Copied" : "Copy code"}
                                </button>
                                {t.share_url && (
                                  <a href={t.share_url} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}
                                    className="inline-flex items-center gap-1 rounded-lg border border-n-800 bg-surface px-2.5 py-1.5 text-xs text-n-200 hover:text-n-0">
                                    <ExternalLink size={12} /> Open on SportyBet</a>
                                )}
                                <span className="text-[11px] text-n-500">Account {t.uid} · made {new Date(t.created_at).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })}</span>
                              </div>
                              </div>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
