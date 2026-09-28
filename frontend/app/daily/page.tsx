"use client";

import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";
import { useUser } from "@clerk/nextjs";
import { Check, Copy, ExternalLink, Info, Loader2, Ticket } from "lucide-react";
import { AppShell } from "@/components/shell/AppShell";
import { PageHeader } from "@/components/shell/PageHeader";
import { FeatureGate, Locked, Unavailable } from "@/components/FeatureGate";
import { LegRow, nextRefresh } from "@/components/TicketsList";
import { useAuthedFetch } from "@/lib/useAuthedFetch";
import { API, type LegLive, type LegStatus } from "@/lib/matchday";

// Daily odds (backend daily_slips.py): three slips a day at about 10x, 15x
// and 20x from the day's own matches, only from picks the model rates 70%+,
// each with its honest chance.
// The server makes them once each morning and books each on SportyBet: every
// visitor gets the same slips and booking codes.

interface Pick {
  home: string; away: string; date: string; time?: string; league?: string;
  market: string; market_name?: string; code: string; label?: string;
  prob: number; odds: number; bookable?: boolean; status: LegStatus; live?: LegLive;
}
/** The server's booking of the slip on SportyBet (made with the slip). */
interface Booking {
  code: string | null; share_url: string | null; total_odds?: number | null;
  booked: number; of: number; error?: string | null; at: string;
}
interface Slip {
  target: number; status: "pending" | "won" | "lost" | "void" | "none"; error?: string;
  total_odds?: number; win_chance?: number; games?: number; within_target?: boolean;
  days?: number; bookable?: boolean; picks: Pick[]; booking?: Booking;
}
interface DailyResponse {
  date: string; today: string; built_at: string | null; slips: Slip[];
  record: Record<string, { won: number; lost: number }>; min_prob: number; targets: number[];
  publish_at_utc?: string; retry_minutes?: number;
}
/** Still to play: not settled, and kick-off (UTC date + time) hasn't passed. */
const notStarted = (p: Pick) => p.status === "pending" && !p.live &&
  !(Date.parse(`${p.date}T${p.time || "23:59"}:00Z`) <= Date.now());

const PERKS = [
  "Three slips every morning, at about 10, 15 and 20 odds",
  "Today's matches only, every pick rated 70% or more",
  "A ready SportyBet booking code with every slip",
  "Every slip graded, with a public record",
];
const pct = (x?: number) => (typeof x === "number" ? `${Math.round(x * 100)}%` : "—");
const pctFine = (x?: number) => (typeof x === "number" ? `${(x * 100).toFixed(x < 0.1 ? 1 : 0)}%` : "—");
const STATUS: Record<Slip["status"], { label: string; cls: string }> = {
  pending: { label: "In progress", cls: "bg-surface-sunken text-n-300 border-n-800" },
  won: { label: "Won", cls: "bg-accent/15 text-accent border-accent/30" },
  lost: { label: "Lost", cls: "bg-danger/15 text-danger border-danger/30" },
  void: { label: "Void", cls: "bg-n-800 text-n-300 border-n-700" },
  none: { label: "No slip", cls: "bg-n-800 text-n-400 border-n-700" },
};

function daysBefore(iso: string, n: number): string[] {
  const d = new Date(`${iso}T12:00:00Z`);
  return Array.from({ length: n }, (_, i) => new Date(d.getTime() - i * 864e5).toISOString().slice(0, 10));
}
const dayName = (iso: string, today: string) =>
  iso === today ? "Today" : new Date(`${iso}T12:00:00Z`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });

function SlipView({ s, isToday, date, retry, minProb }: { s: Slip; isToday: boolean; date: string; retry: number; minProb: number }) {
  const { user } = useUser();
  const authFetch = useAuthedFetch();
  const [copied, setCopied] = useState(false);
  const [tracking, setTracking] = useState<"idle" | "busy" | "done" | string>("idle");

  if (s.status === "none") {
    return (
      <div className="card p-5 text-sm text-n-300">
        <p className="font-semibold text-n-0 mb-1">No {s.target}x slip {isToday ? "today" : "that day"}</p>
        <p className="text-n-400">{s.error || `Not enough picks at ${pct(minProb)} or more in ${isToday ? "today's" : "that day's"} matches to reach ${s.target}x.`}</p>
      </div>
    );
  }
  const b = s.booking;
  const toPlay = s.picks.filter(notStarted).length;
  const started = s.picks.filter(p => p.status === "pending" && !notStarted(p)).length;
  const settled = s.picks.filter(p => p.status !== "pending").length;
  const copy = async (code: string) => {
    try { await navigator.clipboard.writeText(code); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* ignore */ }
  };
  // Put the code in the account's tickets, graded leg by leg (Dashboard → Tickets)
  const track = async () => {
    setTracking("busy");
    try {
      const r = await authFetch(`${API}/api/daily-slips/track`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date, target: s.target, uid: user?.id }) });
      const d = await r.json().catch(() => null);
      if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : "Couldn't add it. Try again.");
      setTracking("done");
    } catch (e) { setTracking((e as Error).message || "Couldn't add it. Try again."); }
  };

  return (
    <article className={clsx("card overflow-hidden", s.status === "won" && "border-accent/40", s.status === "lost" && "border-danger/30")}>
      <header className="p-4 sm:p-5 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">{s.target}x slip · {s.games} games · {settled}/{s.picks.length} settled</p>
          <p className="font-display font-extrabold text-5xl text-n-0 leading-none tnum mt-1">{s.total_odds?.toFixed(2)}<span className="text-n-400 text-2xl">x</span></p>
        </div>
        <div className="sm:text-right space-y-1">
          <span className={clsx("inline-block rounded-full border px-2 py-0.5 text-[11px] font-bold", STATUS[s.status].cls)}>{STATUS[s.status].label}</span>
          <p className="text-xs text-n-400">Chance the whole slip lands: <span className="font-bold text-n-0 tnum">{pctFine(s.win_chance)}</span></p>
        </div>
      </header>

      <section className="mx-4 sm:mx-5 mb-4 rounded-xl border border-n-800 bg-surface-sunken p-3 sm:p-4 space-y-2">
        <p className="eyebrow flex items-center gap-1.5"><Ticket size={12} /> SportyBet booking code</p>
        {b?.code ? (
          <>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-mono text-3xl font-bold text-n-0 tracking-[0.18em]">{b.code}</span>
              <span className="flex gap-2">
                {b.share_url && <a href={b.share_url} target="_blank" rel="noreferrer" className="btn-secondary !px-3 !py-1.5 !text-xs"><ExternalLink size={12} /> Open</a>}
                <button onClick={() => copy(b.code!)} className="btn-primary !px-3 !py-1.5 !text-xs">
                  {copied ? <Check size={12} /> : <Copy size={12} />}{copied ? "Copied" : "Copy code"}</button>
              </span>
            </div>
            {b.booked < s.picks.length && (
              <p className="text-[11px] text-warn">The code holds {b.booked} of the {s.picks.length} picks: SportyBet wasn&apos;t offering the others when we booked it.</p>
            )}
            {isToday && started > 0 && s.status === "pending" && (
              <p className="text-[11px] text-n-400">{started} match{started > 1 ? "es have" : " has"} kicked off: SportyBet leaves {started > 1 ? "those" : "it"} out if you load the code now.</p>
            )}
            {user && s.status === "pending" && (
              tracking === "done" ? (
                <p className="text-xs text-accent flex items-center gap-1"><Check size={12} /> In your tickets: we&apos;ll grade it as the results come in.</p>
              ) : (
                <div className="flex flex-wrap items-center gap-2">
                  <button onClick={track} disabled={tracking === "busy"} className="btn-secondary !px-3 !py-1.5 !text-xs">
                    {tracking === "busy" ? <Loader2 size={12} className="animate-spin" /> : <Ticket size={12} />} Track in my tickets
                  </button>
                  {tracking !== "idle" && tracking !== "busy" && <span className="text-xs text-danger">{tracking}</span>}
                </div>
              )
            )}
          </>
        ) : isToday && toPlay > 0 ? (
          <p className="text-sm text-n-300">
            <Loader2 size={13} className="inline animate-spin mr-1.5 text-n-400" />
            The code is on its way: SportyBet didn&apos;t take the slip yet, so we try again every {retry} minutes.
            {b?.error && <span className="block text-[11px] text-n-500 mt-1">Last try: {b.error}</span>}
          </p>
        ) : (
          <p className="text-sm text-n-400">No booking code: SportyBet didn&apos;t take this slip before its matches started.</p>
        )}
      </section>

      {(!s.bookable || s.within_target === false || (s.days ?? 1) > 1) && (
        <p className="px-4 sm:px-5 pb-3 text-[11px] text-n-500">
          {[s.within_target === false && `Closest to ${s.target}x the picks allowed`,
            (s.days ?? 1) > 1 && "includes the next day's matches",
            !s.bookable && "some picks weren't on SportyBet yet"].filter(Boolean).join(" · ")}
        </p>
      )}
      <ul className="px-4 sm:px-5 border-t border-dashed border-n-700 divide-y divide-n-800/70">
        {s.picks.map((p, i) => (
          <LegRow key={i} showProb leg={{ ...p, marketName: p.market_name, status: p.status }} />
        ))}
      </ul>
    </article>
  );
}

function Daily() {
  const authFetch = useAuthedFetch();
  const [day, setDay] = useState<string>("");
  const [data, setData] = useState<DailyResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pick, setPick] = useState(0);

  const load = useCallback((d: string) =>
    authFetch(`${API}/api/daily-slips${d ? `?date=${d}` : ""}`).then(async r => {
      if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail || "failed");
      return r.json() as Promise<DailyResponse>;
    }), [authFetch]);

  useEffect(() => {
    setError(null);
    load(day).then(d => { setData(d); setPick(p => Math.min(p, Math.max(0, d.slips.length - 1))); })
      .catch(e => setError(e.message === "failed" ? "Couldn't load the slips. Try again in a moment." : e.message));
  }, [day, load]);

  // Scores while picks are being played
  useEffect(() => {
    if (!data || data.date !== data.today) return;
    const wait = nextRefresh(data.slips.map(s => ({ status: s.status === "pending" ? "pending" : "won", legs: s.picks.map(p => ({ ...p, status: p.status })) })));
    if (wait === null) return;
    const t = setTimeout(() => { if (document.visibilityState !== "hidden") load(day).then(setData).catch(() => {}); else setData(d => (d ? { ...d } : d)); }, wait);
    return () => clearTimeout(t);
  }, [data, day, load]);

  // The server's own check disagreed with what the page knew
  if (error === "feature_off") return <Unavailable title="Daily odds" />;
  if (error && /_required$|^Sign in/.test(error)) return <Locked feature="daily_slips" title="Daily odds" perks={PERKS} />;
  if (error) return <p className="card p-6 text-center text-sm text-n-400">{error}</p>;
  if (!data) return <div className="card flex items-center justify-center gap-2 py-16 text-sm text-n-400"><Loader2 size={15} className="animate-spin" /> Building today&apos;s slips…</div>;

  const isToday = data.date === data.today;
  const s = data.slips[pick];
  // When the morning job makes them, in the visitor's own time
  const publishAt = new Date(`${data.today}T${data.publish_at_utc ?? "06:05"}:00Z`)
    .toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return (
    <div className="space-y-5">
      <div className="flex gap-1.5 overflow-x-auto [scrollbar-width:none] -mx-1 px-1">
        {daysBefore(data.today, 7).map(d => (
          <button key={d} onClick={() => setDay(d === data.today ? "" : d)}
            className={clsx("chip shrink-0", (d === data.date) ? "chip-active" : "chip-idle")}>{dayName(d, data.today)}</button>
        ))}
      </div>

      {data.slips.length === 0 ? (
        <p className="card p-6 text-center text-sm text-n-400">{isToday
          ? `Today's slips and booking codes come out at ${publishAt} each morning. Check back then, or look at yesterday's.`
          : "No slips were made that day."}</p>
      ) : (
        <>
          <div className="grid grid-cols-3 gap-2 sm:gap-3" role="tablist" aria-label="Slips">
            {data.slips.map((x, i) => {
              const rec = data.record[String(x.target)];
              return (
                <button key={x.target} role="tab" aria-selected={pick === i} onClick={() => setPick(i)}
                  className={clsx("card p-3 sm:p-4 text-left transition-colors", pick === i ? "!border-brand-400/60 bg-brand-400/[0.06]" : "hover:bg-surface-raised/60")}>
                  <p className="eyebrow">{x.target}x</p>
                  <p className="font-display font-extrabold text-2xl sm:text-3xl text-n-0 tnum leading-none mt-1">
                    {x.status === "none" ? "—" : `${x.total_odds?.toFixed(2)}x`}</p>
                  <p className="text-[11px] text-n-400 mt-1.5 tnum">
                    {x.status === "none" ? "Not today" : <>{x.games} games · {pctFine(x.win_chance)}</>}</p>
                  <span className={clsx("inline-block mt-2 rounded-full border px-1.5 py-px text-[10px] font-bold", STATUS[x.status].cls)}>{STATUS[x.status].label}</span>
                  {rec && rec.won + rec.lost > 0 && <p className="text-[10px] text-n-500 mt-1.5 tnum">Record {rec.won}/{rec.won + rec.lost}</p>}
                </button>
              );
            })}
          </div>
          {s && <SlipView key={`${data.date}-${s.target}`} s={s} isToday={isToday} date={data.date} retry={data.retry_minutes ?? 15} minProb={data.min_prob} />}
        </>
      )}

      <div className="rounded-xl border border-n-800 bg-surface-sunken px-4 py-3 text-xs text-n-400 flex gap-2.5">
        <Info size={14} className="text-n-500 shrink-0 mt-0.5" />
        <p>Every pick is one our model rates {pct(data.min_prob)} or more, but a slip only wins if all of them do, so its
          own chance is much lower. That&apos;s the percentage on each slip. Slips are made once each morning, booked on
          SportyBet for you, and graded at full time; the record counts every one. 18+ · Bet responsibly.</p>
      </div>
    </div>
  );
}

export default function DailyPage() {
  return (
    <AppShell>
      <div className="space-y-6 animate-fade-in">
        <PageHeader eyebrow="Every morning" title="Daily odds"
          description="Three slips a day at about 10, 15 and 20 odds, from that day's matches only and picks our model rates 70% or more, each with its SportyBet booking code." />
        <FeatureGate feature="daily_slips" title="Daily odds" perks={PERKS}>
          <Daily />
        </FeatureGate>
      </div>
    </AppShell>
  );
}
