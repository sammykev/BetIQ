"use client";

import { Fragment, useEffect, useState } from "react";
import clsx from "clsx";
import { CalendarDays, ChevronDown, ChevronRight, RefreshCw } from "lucide-react";
import { Btn, Card, Pill, Skeleton, Stat, ago, useAdmin } from "../ui";

// Every daily odds ticket (backend /api/admin/daily-slips): the 12am slips,
// the 12pm second ones, and the cut ones they replaced, with codes and picks.

type Pick = { home: string; away: string; date: string; time?: string; market_name?: string; label?: string;
  code: string; prob?: number; odds?: number; status?: string; sport?: string };
type Slip = { target: number; status: string; slot: "12am" | "12pm"; was_cut?: boolean; total_odds?: number;
  win_chance?: number; games?: number; error?: string; picks: Pick[]; second?: string; remade?: string;
  booking?: { code?: string | null; football_com?: { code?: string } | null; error?: string | null };
  replaces?: { code?: string | null; cut_by?: string | null } };
type Day = { date: string; built_at?: string; slips: Slip[] };

const TONE: Record<string, "ok" | "danger" | "warn" | "muted" | "info"> = {
  won: "ok", lost: "danger", pending: "info", void: "muted", none: "muted", open: "warn", unknown: "warn",
};

export function DailyOddsSection() {
  const { get } = useAdmin();
  const [data, setData] = useState<any>(null);
  const [days, setDays] = useState(14);
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async (n = days) => {
    setBusy(true);
    setData(await get(`/api/admin/daily-slips?days=${n}`) ?? { days: [], error: true });
    setBusy(false);
  };
  useEffect(() => { load(days); }, [days]);   // eslint-disable-line react-hooks/exhaustive-deps

  const all: Slip[] = (data?.days ?? []).flatMap((d: Day) => d.slips);
  const settled = all.filter(s => s.status === "won" || s.status === "lost");
  const won = settled.filter(s => s.status === "won").length;

  return (
    <div className="space-y-4">
      <Card title="Daily odds tickets" icon={<CalendarDays size={15} />}
        subtitle={`Every slip, first at 12am and second at ${data?.second_at ?? "12pm Lagos"} when the first was cut`}
        action={<div className="flex gap-1">
          {[7, 14, 30, 90].map(n => <Btn key={n} variant={days === n ? "primary" : "ghost"} onClick={() => setDays(n)}>{n}d</Btn>)}
          <Btn onClick={() => load()} busy={busy}><RefreshCw size={13} /></Btn>
        </div>}>
        {!data ? <Skeleton rows={4} /> : (
          <div className="grid gap-3 sm:grid-cols-4">
            <Stat label="Slips" value={all.filter(s => s.status !== "none").length} />
            <Stat label="Settled" value={settled.length} sub={settled.length ? `${won} won · ${settled.length - won} lost` : undefined} />
            <Stat label="Still running" value={all.filter(s => s.status === "pending").length} />
            <Stat label="Second slips (12pm)" value={all.filter(s => s.slot === "12pm" && s.status !== "none").length} />
          </div>
        )}
        {data?.record && Object.keys(data.record).length > 0 && (
          <p className="mt-3 text-xs text-n-400">All-time record: {Object.entries(data.record as Record<string, any>)
            .sort((a, b) => Number(a[0]) - Number(b[0]))
            .map(([t, r]) => `${t}x ${r.won}/${r.won + r.lost}`).join(" · ")}</p>
        )}
      </Card>

      {(data?.days ?? []).map((d: Day) => (
        <Card key={d.date} title={d.date} subtitle={d.built_at ? `made ${ago(d.built_at)}` : undefined}>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-n-500 text-left">
                <tr><th className="py-1.5 pr-2" /><th className="pr-2">Target</th><th className="pr-2">Slot</th><th className="pr-2">Code</th>
                  <th className="pr-2">Odds</th><th className="pr-2">Chance</th><th className="pr-2">Picks</th><th>Status</th></tr>
              </thead>
              <tbody>
                {d.slips.map((s, i) => {
                  const key = `${d.date}-${i}`;
                  const isOpen = open === key;
                  const graded = s.picks.filter(p => p.status && p.status !== "pending").length;
                  return (
                    <Fragment key={key}>
                      <tr className={clsx("border-t border-n-800", s.picks.length && "cursor-pointer hover:bg-surface-sunken")}
                        onClick={() => s.picks.length && setOpen(isOpen ? null : key)}>
                        <td className="py-1.5 pr-2 text-n-500">{s.picks.length ? (isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />) : null}</td>
                        <td className="pr-2 font-bold text-n-0 tnum">{s.target}x</td>
                        <td className="pr-2">{s.slot}{s.was_cut ? " · cut" : ""}</td>
                        <td className="pr-2 font-mono">{s.booking?.code || (s.status === "none" ? "—" : <span className="text-warn">{s.booking?.error ? "failed" : "none yet"}</span>)}
                          {s.booking?.football_com?.code && <span className="text-n-500"> · fc {s.booking.football_com.code}</span>}</td>
                        <td className="pr-2 tnum">{s.total_odds ? s.total_odds.toFixed(2) : "—"}</td>
                        <td className="pr-2 tnum">{s.win_chance ? `${(s.win_chance * 100).toFixed(1)}%` : "—"}</td>
                        <td className="pr-2 tnum">{s.picks.length ? `${graded}/${s.picks.length}` : "—"}</td>
                        <td><Pill tone={TONE[s.status] ?? "muted"}>{s.status === "none" ? (s.error ? "no slip" : "none") : s.status}</Pill>
                          {s.second === "not needed" && <span className="ml-1 text-n-500">· no 2nd (running at 12pm)</span>}
                          {s.remade === "none" && <span className="ml-1 text-n-500">· no 2nd (no matches)</span>}</td>
                      </tr>
                      {isOpen && (
                        <tr><td colSpan={8} className="pb-3">
                          {s.status === "none" && s.error && <p className="text-n-400 py-1">{s.error}</p>}
                          <ul className="divide-y divide-n-800 rounded-lg bg-surface-sunken">
                            {s.picks.map((p, j) => (
                              <li key={j} className="px-3 py-1.5 flex flex-wrap gap-x-3 items-center">
                                <span className="text-n-500 tnum">{p.date} {p.time ?? ""}</span>
                                <span className="text-n-100">{p.home} v {p.away}</span>
                                <span className="text-n-400">{p.market_name ?? ""}: {p.label ?? p.code}</span>
                                <span className="tnum text-n-400">{p.odds?.toFixed(2)} · {p.prob ? `${Math.round(p.prob * 100)}%` : ""}</span>
                                <Pill tone={TONE[p.status ?? "pending"] ?? "muted"}>{p.status ?? "pending"}</Pill>
                              </li>
                            ))}
                          </ul>
                        </td></tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      ))}
      {data && !(data.days ?? []).length && <Card><p className="text-sm text-n-400">No daily slips in this period.</p></Card>}
    </div>
  );
}
