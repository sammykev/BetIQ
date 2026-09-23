"use client";

import { useUser } from "@clerk/nextjs";
import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import clsx from "clsx";
import {
  Star, Ticket, BarChart2, User, Link2, Trophy,
  TrendingUp, TrendingDown, Minus, Plus, Check,
  Copy, Crown, RefreshCw, Loader2, Share2,
} from "lucide-react";
import { MatchCard } from "@/components/MatchCard";
import { AppShell } from "@/components/shell/AppShell";
import { PageHeader } from "@/components/shell/PageHeader";

const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

type Tab = "overview" | "saved" | "bets" | "codes" | "referral" | "account";

interface Bet {
  home: string; away: string; date: string;
  tip: string; stake: number; odds: number;
  result: "won" | "lost" | "void" | "pending";
  payout: number; logged_at: string;
}

interface Code {
  code: string; games: { game: string; tip: string; odds: string }[];
  total_odds: number; date: string; saved_at: string;
}

interface Stats {
  won: number; lost: number; void: number;
  total_stake: number; total_return: number; roi: number;
  accuracy: number; streak: number; streak_type: string | null;
  saved_count: number; codes_count: number;
}

/** GET a JSON endpoint; anything that isn't a 2xx JSON body comes back as null. */
async function getJson(url: string): Promise<unknown> {
  try {
    const r = await fetch(url);
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}
const asList = <T,>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);

function StatCard({ label, value, color = "text-white", sub }: { label: string; value: React.ReactNode; color?: string; sub?: string }) {
  return (
    <div className="card px-4 py-3.5">
      <p className="eyebrow">{label}</p>
      <p className={clsx("font-display font-extrabold text-4xl leading-none mt-1.5 tnum", color)}>{value}</p>
      {sub && <p className="text-[11px] text-zinc-500 mt-1 capitalize">{sub}</p>}
    </div>
  );
}

function EmptyState({ icon, title, body }: { icon: React.ReactNode; title: string; body: string }) {
  return (
    <div className="card border-dashed text-center py-14 px-6 space-y-2">
      <div className="mx-auto w-11 h-11 rounded-2xl bg-zinc-800/70 flex items-center justify-center text-zinc-400">{icon}</div>
      <p className="font-display font-bold text-xl uppercase tracking-wide text-white pt-1">{title}</p>
      <p className="text-sm text-zinc-400">{body}</p>
    </div>
  );
}

const naira = (n: number) => `₦${Math.abs(n).toLocaleString()}`;

export default function DashboardPage() {
  const { user, isLoaded } = useUser();
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("overview");

  const [stats,  setStats]  = useState<Stats | null>(null);
  const [saves,  setSaves]  = useState<any[]>([]);
  const [bets,   setBets]   = useState<Bet[]>([]);
  const [codes,  setCodes]  = useState<Code[]>([]);
  const [refStats, setRefStats] = useState<{ code: string; count: number; link: string } | null>(null);
  const [leaderboard, setLeaderboard] = useState<{ uid: string; wins: number }[]>([]);

  const [betForm, setBetForm] = useState({ home: "", away: "", tip: "", stake: "", odds: "", result: "won" as "won"|"lost"|"void" });
  const [betLoading, setBetLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [copiedCode, setCopiedCode] = useState<string | null>(null);
  const [loadingTab, setLoadingTab] = useState(false);

  const uid = user?.id ?? "";

  const fetchAll = useCallback(async () => {
    if (!uid) return;
    setLoadingTab(true);
    try {
      const [s, sv, b, c, ref, lb] = await Promise.all([
        getJson(`${API}/api/user/stats?uid=${uid}`),
        getJson(`${API}/api/user/saves?uid=${uid}`),
        getJson(`${API}/api/user/bets?uid=${uid}`),
        getJson(`${API}/api/user/codes?uid=${uid}`),
        getJson(`${API}/api/referral/stats?uid=${uid}`),
        getJson(`${API}/api/leaderboard`),
      ]);
      // Offline / error replies arrive as objects like {"error": "offline"} —
      // only accept the shapes each view actually renders.
      setStats(s && typeof (s as Stats).accuracy === "number" ? (s as Stats) : null);
      setSaves(asList(sv));
      setBets(asList<Bet>(b));
      setCodes(asList<Code>(c));
      setRefStats(ref && typeof (ref as { link?: unknown }).link === "string" ? (ref as { code: string; count: number; link: string }) : null);
      setLeaderboard(asList(lb));
    } finally {
      setLoadingTab(false);
    }
  }, [uid]);

  useEffect(() => {
    if (isLoaded && !user) router.push("/");
  }, [isLoaded, user, router]);

  useEffect(() => { if (uid) fetchAll(); }, [uid, fetchAll]);

  const logBet = async () => {
    if (!betForm.home || !betForm.stake || !uid) return;
    setBetLoading(true);
    const payout = betForm.result === "won"
      ? parseFloat(betForm.stake) * parseFloat(betForm.odds || "1")
      : betForm.result === "void" ? parseFloat(betForm.stake) : 0;
    try {
      await fetch(`${API}/api/user/bets`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ uid, bet: { ...betForm, stake: parseFloat(betForm.stake), odds: parseFloat(betForm.odds || "1"), payout } }),
      });
      setBetForm({ home: "", away: "", tip: "", stake: "", odds: "", result: "won" });
      await fetchAll();
    } catch { /* silently fail */ }
    finally { setBetLoading(false); }
  };

  const copyRef = () => {
    if (!refStats) return;
    navigator.clipboard.writeText(refStats.link);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const copyBooking = (code: string) => {
    navigator.clipboard.writeText(code);
    setCopiedCode(code);
    setTimeout(() => setCopiedCode(null), 2000);
  };

  const isPremium = (user?.publicMetadata as any)?.subscription === "premium" &&
    new Date((user?.publicMetadata as any)?.subscription_expires ?? 0) > new Date();

  if (!isLoaded) return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="w-8 h-8 border-2 border-brand-400 border-t-transparent rounded-full animate-spin" />
    </div>
  );

  const TABS: { id: Tab; label: string; count?: number; icon: React.ReactNode }[] = [
    { id: "overview",  label: "Overview",  icon: <BarChart2 size={14} /> },
    { id: "saved",     label: "Saved",     count: saves.length, icon: <Star size={14} /> },
    { id: "bets",      label: "My bets",   count: bets.length,  icon: <TrendingUp size={14} /> },
    { id: "codes",     label: "Codes",     count: codes.length, icon: <Ticket size={14} /> },
    { id: "referral",  label: "Referrals", icon: <Link2 size={14} /> },
    { id: "account",   label: "Account",   icon: <User size={14} /> },
  ];

  const refreshAction = (
    <button onClick={fetchAll} className="btn-secondary !px-3 !py-1.5 !text-xs !rounded-lg">
      <RefreshCw size={12} className={clsx(loadingTab && "animate-spin")} />
      <span className="hidden sm:inline">Refresh</span>
    </button>
  );

  const inputCls = "bg-surface-sunken border border-zinc-800 text-white rounded-lg px-3 py-2.5 text-sm outline-none focus:border-brand-400/70 focus:ring-2 focus:ring-brand-400/15 transition-all placeholder:text-zinc-500";
  const profit = stats ? stats.total_return - stats.total_stake : 0;

  return (
    <AppShell actions={refreshAction} onUpgrade={() => router.push("/")}>
      <div className="space-y-6 animate-fade-in">
        <PageHeader
          eyebrow={`Welcome back${user?.firstName ? `, ${user.firstName}` : ""}`}
          title="Dashboard"
          description="Your record, saved picks, bets and booking codes."
          right={isPremium && (
            <span className="inline-flex items-center gap-1.5 font-display font-bold text-sm uppercase tracking-wider text-amber-300 bg-amber-400/10 border border-amber-400/30 px-3 py-1 rounded-lg">
              <Crown size={13} /> Premium
            </span>
          )}
        />

        {/* Tabs */}
        <div role="tablist" className="flex gap-6 border-b border-zinc-800 overflow-x-auto -mx-4 px-4 sm:mx-0 sm:px-0">
          {TABS.map(t => {
            const active = tab === t.id;
            return (
              <button key={t.id} role="tab" aria-selected={active} onClick={() => setTab(t.id)}
                className={clsx(
                  "relative shrink-0 inline-flex items-center gap-1.5 pb-3 font-display font-bold text-[16px] uppercase tracking-[0.06em] transition-colors",
                  active ? "text-white" : "text-zinc-500 hover:text-zinc-300"
                )}>
                <span className={active ? "text-brand-400" : undefined}>{t.icon}</span>
                {t.label}
                {t.count != null && t.count > 0 && <span className="font-sans text-[11px] font-bold text-zinc-500 tnum">{t.count}</span>}
                {active && <span className="absolute left-0 right-0 -bottom-px h-[2px] bg-brand-400 rounded-full" />}
              </button>
            );
          })}
        </div>

        {/* ── Overview ── */}
        {tab === "overview" && (
          <div className="grid lg:grid-cols-[1fr_340px] gap-4 items-start">
            <div className="space-y-3">
              {stats ? (
                <>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <StatCard label="Accuracy" value={`${stats.accuracy}%`}
                      color={stats.accuracy >= 60 ? "text-brand-400" : stats.accuracy >= 45 ? "text-amber-300" : "text-rose-400"} />
                    <StatCard label="ROI" value={`${stats.roi > 0 ? "+" : ""}${stats.roi}%`}
                      color={stats.roi > 0 ? "text-brand-400" : "text-rose-400"} />
                    <StatCard label="Streak" value={stats.streak || "—"}
                      color={stats.streak_type === "won" ? "text-brand-400" : stats.streak_type === "lost" ? "text-rose-400" : "text-zinc-400"}
                      sub={stats.streak_type ? `${stats.streak_type} streak` : undefined} />
                    <StatCard label="Profit / loss" value={`${profit < 0 ? "−" : "+"}${naira(profit)}`}
                      color={profit >= 0 ? "text-brand-400" : "text-rose-400"} />
                  </div>
                  <div className="card px-4 py-4">
                    <div className="flex items-center justify-between">
                      <p className="eyebrow">Settled bets</p>
                      <p className="text-xs text-zinc-500 tnum">{stats.won + stats.lost + stats.void} total</p>
                    </div>
                    {/* Won / lost / void split */}
                    <div className="flex h-2 rounded-full overflow-hidden gap-0.5 mt-3 bg-zinc-800">
                      {[
                        { n: stats.won, cls: "bg-brand-400" },
                        { n: stats.lost, cls: "bg-rose-500/80" },
                        { n: stats.void, cls: "bg-zinc-600" },
                      ].map(({ n, cls }, i) => n > 0 && (
                        <div key={i} className={cls} style={{ flexGrow: n }} />
                      ))}
                    </div>
                    <div className="flex gap-5 mt-3 text-sm">
                      <span className="text-zinc-400"><span className="font-display font-bold text-xl text-brand-400 tnum mr-1">{stats.won}</span>won</span>
                      <span className="text-zinc-400"><span className="font-display font-bold text-xl text-rose-400 tnum mr-1">{stats.lost}</span>lost</span>
                      <span className="text-zinc-400"><span className="font-display font-bold text-xl text-zinc-300 tnum mr-1">{stats.void}</span>void</span>
                    </div>
                  </div>
                </>
              ) : loadingTab ? (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  {Array.from({ length: 4 }).map((_, i) => (
                    <div key={i} className="card px-4 py-4 space-y-3"><div className="skeleton h-3 w-16" /><div className="skeleton h-8 w-20 !rounded-lg" /></div>
                  ))}
                </div>
              ) : (
                <EmptyState icon={<BarChart2 size={20} />} title="No stats yet" body="Log your bets on the My bets tab and your record appears here." />
              )}
            </div>

            {/* Leaderboard */}
            <div className="card p-4">
              <div className="flex items-center gap-2 mb-2">
                <Trophy size={15} className="text-amber-300" />
                <h2 className="font-display font-bold text-lg uppercase tracking-[0.06em] text-white">Top predictors</h2>
              </div>
              {leaderboard.length === 0
                ? <p className="text-zinc-400 text-sm py-2">No entries yet. Log your winning bets to appear here.</p>
                : <ol className="divide-y divide-zinc-800/70">
                    {leaderboard.slice(0, 10).map((e, i) => {
                      const you = e.uid === uid;
                      return (
                        <li key={e.uid} className={clsx("flex items-center gap-3 py-2.5", you && "bg-brand-400/[0.07] -mx-2 px-2 rounded-lg")}>
                          <span className={clsx("font-display font-extrabold text-xl w-6 text-center tnum",
                            i === 0 ? "text-amber-300" : i === 1 ? "text-zinc-300" : i === 2 ? "text-orange-400" : "text-zinc-600")}>
                            {i + 1}
                          </span>
                          <span className={clsx("flex-1 text-sm", you ? "text-white font-semibold" : "text-zinc-400 font-mono text-xs")}>
                            {you ? "You" : `#${e.uid.slice(-6)}`}
                          </span>
                          <span className="font-display font-bold text-lg text-brand-400 tnum">{e.wins}<span className="text-xs text-zinc-500 ml-0.5">W</span></span>
                        </li>
                      );
                    })}
                  </ol>}
            </div>
          </div>
        )}

        {/* ── Saved Picks ── */}
        {tab === "saved" && (
          <div className="space-y-2.5">
            {saves.length === 0
              ? <EmptyState icon={<Star size={20} />} title="No saved picks" body="Tap the star on any prediction card to save it here." />
              : saves.map((p: any, i: number) => (
                <MatchCard key={i} home={p.home} away={p.away}
                  league={p.league_name} flag={p.flag} date={p.date}
                  onClick={() => router.push(`/match?${new URLSearchParams({ home: p.home, away: p.away, date: p.date ?? "" })}`)}>
                  <div className="flex flex-col items-end gap-1">
                    <span className="font-display font-bold text-sm uppercase text-brand-300 bg-brand-400/10 border border-brand-400/30 px-2 py-0.5 rounded-md">{p.tip_1x2}</span>
                    <span className="font-mono text-[11px] text-zinc-500">{Math.round(p.goals_confidence * 100)}% conf.</span>
                  </div>
                </MatchCard>
              ))}
          </div>
        )}

        {/* ── My Bets ── */}
        {tab === "bets" && (
          <div className="grid lg:grid-cols-[360px_1fr] gap-4 items-start">
            {/* Log form */}
            <div className="card p-4 space-y-3 lg:sticky lg:top-24">
              <h3 className="font-display font-bold text-lg uppercase tracking-[0.06em] text-white flex items-center gap-2">
                <Plus size={15} className="text-brand-400" /> Log a bet
              </h3>
              <div className="grid grid-cols-2 gap-2">
                {[
                  { key: "home", placeholder: "Home team" },
                  { key: "away", placeholder: "Away team" },
                  { key: "tip",  placeholder: "Tip (e.g. Home Win)" },
                  { key: "stake", placeholder: "Stake (₦)" },
                  { key: "odds", placeholder: "Odds (e.g. 1.85)" },
                ].map(({ key, placeholder }) => (
                  <input key={key} placeholder={placeholder} aria-label={placeholder} value={(betForm as any)[key]}
                    inputMode={key === "stake" || key === "odds" ? "decimal" : undefined}
                    onChange={e => setBetForm(f => ({ ...f, [key]: e.target.value }))}
                    className={inputCls} />
                ))}
                <select value={betForm.result} aria-label="Result" onChange={e => setBetForm(f => ({ ...f, result: e.target.value as any }))}
                  className={inputCls}>
                  <option value="won">Won</option>
                  <option value="lost">Lost</option>
                  <option value="void">Void</option>
                </select>
              </div>
              <button onClick={logBet} disabled={betLoading || !betForm.home || !betForm.stake}
                className="btn-primary w-full">
                {betLoading ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                {betLoading ? "Logging…" : "Log bet"}
              </button>
            </div>

            {/* Bet history */}
            <div className="space-y-2.5">
              {bets.length === 0
                ? <EmptyState icon={<TrendingUp size={20} />} title="No bets logged" body="Log a bet to start tracking your ROI and streaks." />
                : bets.map((b, i) => {
                  const p = b.result === "won" ? b.payout - b.stake : b.result === "void" ? 0 : -b.stake;
                  return (
                    <MatchCard key={i} home={b.home} away={b.away} date={b.date}
                      className={b.result === "won" ? "!border-brand-400/40" : b.result === "lost" ? "!border-rose-500/40" : ""}>
                      <div className="flex flex-col items-end gap-1 text-xs">
                        <span className={clsx("font-display font-bold text-sm uppercase tracking-wide flex items-center gap-1",
                          b.result === "won" ? "text-brand-400" : b.result === "lost" ? "text-rose-400" : "text-zinc-400")}>
                          {b.result === "won" ? <TrendingUp size={12}/> : b.result === "lost" ? <TrendingDown size={12}/> : <Minus size={12}/>}
                          {b.result}
                        </span>
                        <span className="font-mono text-zinc-400">{b.tip} @ {b.odds}</span>
                        <span className={clsx("font-mono font-bold", p > 0 ? "text-brand-400" : p < 0 ? "text-rose-400" : "text-zinc-400")}>
                          {p > 0 ? "+" : p < 0 ? "−" : ""}{naira(p)}
                        </span>
                      </div>
                    </MatchCard>
                  );
                })}
            </div>
          </div>
        )}

        {/* ── Booking codes ── */}
        {tab === "codes" && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {codes.length === 0
              ? <div className="md:col-span-2"><EmptyState icon={<Ticket size={20} />} title="No booking codes" body="Use the AI assistant to build an accumulator and generate your first code." /></div>
              : codes.map((c, i) => (
                <div key={i} className="card overflow-hidden">
                  <div className="flex items-center justify-between gap-3 p-4 bg-gradient-to-r from-brand-400/[0.08] to-transparent">
                    <div>
                      <p className="eyebrow">Booking code</p>
                      <p className="font-mono text-2xl font-bold text-white tracking-[0.2em] mt-1">{c.code}</p>
                    </div>
                    <div className="text-right">
                      <p className="eyebrow">Total odds</p>
                      <p className="font-display font-extrabold text-3xl text-brand-400 leading-none mt-1 tnum">{c.total_odds}x</p>
                    </div>
                  </div>
                  <div className="px-4 py-2 border-t border-dashed border-zinc-700 divide-y divide-zinc-800/70">
                    {(Array.isArray(c.games) ? c.games : []).map((g, j) => (
                      <div key={j} className="flex items-center gap-2 text-sm py-2">
                        <span className="truncate flex-1 text-zinc-300">{g.game}</span>
                        <span className="text-white font-semibold">{g.tip}</span>
                        <span className="font-mono text-xs text-zinc-500 w-10 text-right">{g.odds}</span>
                      </div>
                    ))}
                  </div>
                  <div className="flex items-center justify-between px-4 py-3 border-t border-zinc-800">
                    <span className="font-mono text-[11px] text-zinc-500">{c.date}</span>
                    <button onClick={() => copyBooking(c.code)} className="btn-primary !px-3 !py-1.5 !text-xs !rounded-lg">
                      {copiedCode === c.code ? <Check size={12} /> : <Copy size={12} />}
                      {copiedCode === c.code ? "Copied" : "Copy code"}
                    </button>
                  </div>
                </div>
              ))}
          </div>
        )}

        {/* ── Referrals ── */}
        {tab === "referral" && (
          <div className="card p-6 space-y-5 max-w-lg">
            <div>
              <span className="inline-flex items-center justify-center w-11 h-11 rounded-xl bg-brand-400/10 text-brand-400 ring-1 ring-brand-400/20">
                <Link2 size={20} />
              </span>
              <h2 className="display text-4xl text-white mt-4">Invite friends</h2>
              <p className="text-zinc-400 text-sm mt-2">
                When a friend signs up and subscribes, you both get <span className="text-brand-400 font-semibold">30 days of premium free</span>.
              </p>
            </div>
            {refStats ? (
              <>
                <div className="bg-surface-sunken border border-zinc-800 rounded-xl px-4 py-3">
                  <p className="eyebrow mb-1">Your referral link</p>
                  <p className="text-brand-300 text-sm font-mono break-all">{refStats.link}</p>
                </div>
                <div className="flex gap-3">
                  <button onClick={copyRef} className="btn-primary flex-1">
                    {copied ? <Check size={14} /> : <Copy size={14} />}
                    {copied ? "Copied!" : "Copy link"}
                  </button>
                  <button onClick={() => {
                    if (navigator.share) navigator.share({ title: "BetIQ", text: "Join BetIQ — AI Football Predictions", url: refStats.link });
                    else window.open(`https://wa.me/?text=${encodeURIComponent(`Join BetIQ — AI Football Predictions 🎯\n${refStats.link}`)}`);
                  }} className="btn-secondary flex-1">
                    <Share2 size={14} /> Share on WhatsApp
                  </button>
                </div>
                <div className="flex items-baseline justify-between border-t border-zinc-800 pt-4">
                  <p className="eyebrow">Friends who signed up</p>
                  <p className="font-display font-extrabold text-4xl text-white leading-none tnum">{refStats.count}</p>
                </div>
              </>
            ) : (
              <p className="text-sm text-zinc-500">Your referral link isn&apos;t available right now. Try refreshing.</p>
            )}
          </div>
        )}

        {/* ── Account ── */}
        {tab === "account" && (
          <div className="grid md:grid-cols-2 gap-4 max-w-3xl">
            <div className="card p-5 space-y-4">
              <p className="eyebrow">Profile</p>
              <div className="flex items-center gap-4">
                {user?.imageUrl
                  ? <img src={user.imageUrl} alt="" className="w-14 h-14 rounded-full ring-2 ring-zinc-800" />
                  : <div className="w-14 h-14 bg-brand-400 rounded-full flex items-center justify-center text-ink text-2xl font-display font-extrabold">{user?.firstName?.[0]}</div>}
                <div className="min-w-0">
                  <p className="text-white font-semibold truncate">{user?.fullName || "—"}</p>
                  <p className="text-zinc-400 text-sm truncate">{user?.emailAddresses[0]?.emailAddress}</p>
                </div>
              </div>
            </div>

            <div className="card p-5 space-y-3">
              <p className="eyebrow flex items-center gap-1.5"><Crown size={12} className="text-amber-300" /> Subscription</p>
              {isPremium ? (
                <div className="space-y-2">
                  <p className="font-display font-extrabold text-3xl uppercase text-amber-300 leading-none">Premium</p>
                  <p className="text-zinc-400 text-sm">
                    Active until {new Date((user?.publicMetadata as any)?.subscription_expires).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" })}
                  </p>
                  <p className="text-zinc-500 text-xs">Renewing extends from your current expiry date.</p>
                </div>
              ) : (
                <div className="space-y-3">
                  <p className="font-display font-extrabold text-3xl uppercase text-white leading-none">Free plan</p>
                  <button onClick={() => router.push("/")}
                    className="w-full bg-amber-400 hover:bg-amber-300 text-ink font-bold py-2.5 rounded-xl text-sm transition-all flex items-center justify-center gap-2 active:scale-[0.98]">
                    <Crown size={14} /> Upgrade to Premium · ₦1,500/month
                  </button>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
