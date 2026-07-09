"use client";

import { useUser } from "@clerk/nextjs";
import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import clsx from "clsx";
import {
  Star, Ticket, BarChart2, User, Link2, Trophy,
  TrendingUp, TrendingDown, Minus, Plus, Check,
  Copy, Crown, RefreshCw, Loader2,
} from "lucide-react";
import { MatchCard } from "@/components/MatchCard";
import { AppShell } from "@/components/shell/AppShell";

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

function StatCard({ label, value, color = "text-zinc-900 dark:text-white", sub }: { label: string; value: React.ReactNode; color?: string; sub?: string }) {
  return (
    <div className="card px-4 py-3.5 space-y-0.5">
      <p className="text-zinc-400 dark:text-zinc-500 text-[11px] font-medium">{label}</p>
      <p className={clsx("tnum font-black text-2xl leading-tight", color)}>{value}</p>
      {sub && <p className="text-zinc-400 dark:text-zinc-600 text-[10px]">{sub}</p>}
    </div>
  );
}

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
  const [loadingTab, setLoadingTab] = useState(false);

  const uid = user?.id ?? "";

  const fetchAll = useCallback(async () => {
    if (!uid) return;
    setLoadingTab(true);
    try {
      const [s, sv, b, c, ref, lb] = await Promise.all([
        fetch(`${API}/api/user/stats?uid=${uid}`).then(r => r.json()),
        fetch(`${API}/api/user/saves?uid=${uid}`).then(r => r.json()),
        fetch(`${API}/api/user/bets?uid=${uid}`).then(r => r.json()),
        fetch(`${API}/api/user/codes?uid=${uid}`).then(r => r.json()),
        fetch(`${API}/api/referral/stats?uid=${uid}`).then(r => r.json()),
        fetch(`${API}/api/leaderboard`).then(r => r.json()),
      ]);
      setStats(s); setSaves(sv); setBets(b); setCodes(c);
      setRefStats(ref); setLeaderboard(lb);
    } catch { /* silently fail */ }
    finally { setLoadingTab(false); }
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

  const isPremium = (user?.publicMetadata as any)?.subscription === "premium" &&
    new Date((user?.publicMetadata as any)?.subscription_expires ?? 0) > new Date();

  if (!isLoaded) return (
    <div className="min-h-screen bg-[#fafafa] dark:bg-[#09090b] flex items-center justify-center">
      <div className="w-8 h-8 border-2 border-brand-600 border-t-transparent rounded-full animate-spin" />
    </div>
  );

  const TABS: { id: Tab; label: string; icon: React.ReactNode }[] = [
    { id: "overview",  label: "Overview",   icon: <BarChart2 size={14} /> },
    { id: "saved",     label: `Saved (${saves.length})`, icon: <Star size={14} /> },
    { id: "bets",      label: `My Bets (${bets.length})`, icon: <TrendingUp size={14} /> },
    { id: "codes",     label: `Codes (${codes.length})`, icon: <Ticket size={14} /> },
    { id: "referral",  label: "Referrals",  icon: <Link2 size={14} /> },
    { id: "account",   label: "Account",    icon: <User size={14} /> },
  ];

  const refreshAction = (
    <button onClick={fetchAll} className="btn-secondary !px-3 !py-1.5 !text-xs">
      <RefreshCw size={12} className={clsx(loadingTab && "animate-spin")} />
      <span className="hidden sm:inline">Refresh</span>
    </button>
  );

  return (
    <AppShell actions={refreshAction} onUpgrade={() => router.push("/")}>
      <div className="space-y-6 animate-fade-in">
        {/* Heading */}
        <div className="flex items-end justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-2xl sm:text-3xl font-black tracking-tight text-zinc-900 dark:text-white">
              My Dashboard
            </h1>
            <p className="text-sm text-zinc-400 dark:text-zinc-500 mt-1">
              {user?.firstName || user?.emailAddresses[0]?.emailAddress}
            </p>
          </div>
          {isPremium && (
            <span className="inline-flex items-center gap-1 text-[11px] bg-amber-50 dark:bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-200 dark:border-amber-500/30 px-2.5 py-1 rounded-full font-bold">
              <Crown size={11} /> Premium
            </span>
          )}
        </div>

        {/* Tabs */}
        <div className="flex overflow-x-auto gap-2 pb-1 -mx-1 px-1">
          {TABS.map(t => (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={clsx("chip shrink-0", tab === t.id ? "chip-active" : "chip-idle")}>
              {t.icon}{t.label}
            </button>
          ))}
        </div>

        {/* ── Overview ── */}
        {tab === "overview" && (
          <div className="space-y-4">
            {stats ? (
              <>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  <StatCard label="Accuracy" value={`${stats.accuracy}%`}
                    color={stats.accuracy >= 60 ? "text-brand-600 dark:text-brand-400" : stats.accuracy >= 45 ? "text-amber-500" : "text-rose-500"} />
                  <StatCard label="ROI" value={`${stats.roi > 0 ? "+" : ""}${stats.roi}%`}
                    color={stats.roi > 0 ? "text-brand-600 dark:text-brand-400" : "text-rose-500"} />
                  <StatCard label="Streak" value={stats.streak || "—"}
                    color={stats.streak_type === "won" ? "text-brand-600 dark:text-brand-400" : stats.streak_type === "lost" ? "text-rose-500" : "text-zinc-500"}
                    sub={stats.streak_type ? `${stats.streak_type} streak` : undefined} />
                  <StatCard label="Profit / Loss" value={`₦${(stats.total_return - stats.total_stake).toLocaleString()}`}
                    color={stats.total_return >= stats.total_stake ? "text-brand-600 dark:text-brand-400" : "text-rose-500"} />
                </div>
                <div className="grid grid-cols-3 gap-3">
                  <StatCard label="Won" value={stats.won} color="text-brand-600 dark:text-brand-400" />
                  <StatCard label="Lost" value={stats.lost} color="text-rose-500" />
                  <StatCard label="Void" value={stats.void} color="text-zinc-500" />
                </div>
              </>
            ) : <div className="h-24 card animate-pulse" />}

            {/* Leaderboard */}
            <div className="card p-5 space-y-3">
              <div className="flex items-center gap-2">
                <Trophy size={15} className="text-amber-500" />
                <h2 className="text-zinc-900 dark:text-white font-bold text-sm">Top Predictors</h2>
              </div>
              {leaderboard.length === 0
                ? <p className="text-zinc-400 dark:text-zinc-500 text-xs">No entries yet — log your winning bets to appear here!</p>
                : leaderboard.slice(0, 10).map((e, i) => (
                  <div key={e.uid} className={clsx("flex items-center gap-3 text-sm", e.uid === uid && "bg-brand-50 dark:bg-brand-900/20 -mx-2 px-2 py-1 rounded-lg")}>
                    <span className={clsx("tnum font-black w-6 text-center", i === 0 ? "text-amber-500" : i === 1 ? "text-zinc-400" : i === 2 ? "text-orange-500" : "text-zinc-300 dark:text-zinc-600")}>
                      {i + 1}
                    </span>
                    <span className="flex-1 text-zinc-600 dark:text-zinc-300 text-xs font-mono">{e.uid === uid ? "⭐ You" : `${e.uid.slice(-6)}`}</span>
                    <span className="tnum text-brand-600 dark:text-brand-400 font-bold">{e.wins}W</span>
                  </div>
                ))}
            </div>
          </div>
        )}

        {/* ── Saved Picks ── */}
        {tab === "saved" && (
          <div className="space-y-2.5">
            {saves.length === 0
              ? <div className="text-center py-16 text-zinc-400 dark:text-zinc-500 space-y-2">
                  <Star size={32} className="mx-auto opacity-30" />
                  <p className="font-medium">No saved picks yet</p>
                  <p className="text-xs">Tap the ⭐ on any prediction card to save it</p>
                </div>
              : saves.map((p: any, i: number) => (
                <MatchCard key={i} home={p.home} away={p.away}
                  league={p.league_name} flag={p.flag} date={p.date}
                  onClick={() => router.push(`/match?${new URLSearchParams({ home: p.home, away: p.away, date: p.date ?? "" })}`)}>
                  <div className="flex flex-col items-end gap-1">
                    <span className="tnum text-brand-700 dark:text-brand-400 text-xs font-bold bg-brand-50 dark:bg-brand-900/30 border border-brand-200 dark:border-brand-800 px-2 py-0.5 rounded-full">{p.tip_1x2}</span>
                    <span className="tnum text-zinc-400 dark:text-zinc-500 text-[10px]">{Math.round(p.goals_confidence * 100)}%</span>
                  </div>
                </MatchCard>
              ))}
          </div>
        )}

        {/* ── My Bets ── */}
        {tab === "bets" && (
          <div className="space-y-4">
            {/* Log form */}
            <div className="card p-4 space-y-3">
              <h3 className="text-zinc-900 dark:text-white font-bold text-sm flex items-center gap-2"><Plus size={14} className="text-brand-600 dark:text-brand-400"/> Log a Bet</h3>
              <div className="grid grid-cols-2 gap-2">
                {[
                  { key: "home", placeholder: "Home team" },
                  { key: "away", placeholder: "Away team" },
                  { key: "tip",  placeholder: "Tip (e.g. Home Win)" },
                  { key: "stake", placeholder: "Stake (₦)" },
                  { key: "odds", placeholder: "Odds (e.g. 1.85)" },
                ].map(({ key, placeholder }) => (
                  <input key={key} placeholder={placeholder} value={(betForm as any)[key]}
                    onChange={e => setBetForm(f => ({ ...f, [key]: e.target.value }))}
                    className="bg-zinc-50 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 text-zinc-900 dark:text-white rounded-lg px-3 py-2 text-xs outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 transition-all placeholder:text-zinc-400" />
                ))}
                <select value={betForm.result} onChange={e => setBetForm(f => ({ ...f, result: e.target.value as any }))}
                  className="bg-zinc-50 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 text-zinc-900 dark:text-white rounded-lg px-3 py-2 text-xs outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 transition-all">
                  <option value="won">Won ✅</option>
                  <option value="lost">Lost ❌</option>
                  <option value="void">Void ↩️</option>
                </select>
              </div>
              <button onClick={logBet} disabled={betLoading || !betForm.home || !betForm.stake}
                className="btn-primary w-full">
                {betLoading ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                {betLoading ? "Logging…" : "Log bet"}
              </button>
            </div>

            {/* Bet history */}
            {bets.length === 0
              ? <p className="text-center text-zinc-400 dark:text-zinc-500 py-10 text-sm">No bets logged yet</p>
              : bets.map((b, i) => {
                const profit = b.result === "won" ? b.payout - b.stake : b.result === "void" ? 0 : -b.stake;
                return (
                  <MatchCard key={i} home={b.home} away={b.away} date={b.date}
                    className={b.result === "won" ? "!ring-2 !ring-brand-500/40" : b.result === "lost" ? "!ring-2 !ring-rose-500/40" : ""}>
                    <div className="flex flex-col items-end gap-1 text-xs">
                      <span className={clsx("font-bold flex items-center gap-1 capitalize",
                        b.result === "won" ? "text-brand-600 dark:text-brand-400" : b.result === "lost" ? "text-rose-500" : "text-zinc-400")}>
                        {b.result === "won" ? <TrendingUp size={11}/> : b.result === "lost" ? <TrendingDown size={11}/> : <Minus size={11}/>}
                        {b.result}
                      </span>
                      <span className="tnum text-zinc-500 dark:text-zinc-400">{b.tip} @ {b.odds}x</span>
                      <span className={clsx("tnum font-semibold", profit > 0 ? "text-brand-600 dark:text-brand-400" : profit < 0 ? "text-rose-500" : "text-zinc-400")}>
                        {profit >= 0 ? "+" : ""}₦{Math.abs(profit).toLocaleString()}
                      </span>
                    </div>
                  </MatchCard>
                );
              })}
          </div>
        )}

        {/* ── Accumulators ── */}
        {tab === "codes" && (
          <div className="space-y-3">
            {codes.length === 0
              ? <div className="text-center py-16 text-zinc-400 dark:text-zinc-500 space-y-2">
                  <Ticket size={32} className="mx-auto opacity-30" />
                  <p className="font-medium">No booking codes yet</p>
                  <p className="text-xs">Use the chatbot to generate your first accumulator</p>
                </div>
              : codes.map((c, i) => (
                <div key={i} className="card p-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="tnum text-2xl font-black text-zinc-900 dark:text-white tracking-widest font-mono">{c.code}</p>
                      <p className="tnum text-zinc-400 dark:text-zinc-500 text-xs mt-0.5">{c.date} · Odds ~{c.total_odds}x</p>
                    </div>
                    <button onClick={() => { navigator.clipboard.writeText(c.code); }}
                      className="btn-secondary !px-3 !py-1.5 !text-xs">
                      <Copy size={12} /> Copy
                    </button>
                  </div>
                  <div className="space-y-1 pt-1 border-t border-zinc-100 dark:border-zinc-800">
                    {(c.games || []).map((g, j) => (
                      <div key={j} className="flex justify-between text-xs text-zinc-500 dark:text-zinc-400 pt-1">
                        <span className="truncate flex-1">{g.game}</span>
                        <span className="text-brand-600 dark:text-brand-400 font-semibold ml-2">{g.tip}</span>
                        <span className="tnum text-zinc-400 dark:text-zinc-600 ml-2">@{g.odds}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
          </div>
        )}

        {/* ── Referrals ── */}
        {tab === "referral" && (
          <div className="card p-6 space-y-4 text-center max-w-lg mx-auto">
            <div className="text-4xl">🔗</div>
            <div>
              <h2 className="text-zinc-900 dark:text-white font-bold text-lg">Invite Friends</h2>
              <p className="text-zinc-500 dark:text-zinc-400 text-sm mt-1">
                Share your link. When a friend signs up and subscribes, you both get <span className="text-brand-600 dark:text-brand-400 font-semibold">30 days free premium</span>.
              </p>
            </div>
            {refStats && (
              <>
                <div className="bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-100 dark:border-zinc-800 rounded-xl px-4 py-3">
                  <p className="text-zinc-400 dark:text-zinc-500 text-xs mb-1">Your referral link</p>
                  <p className="text-brand-600 dark:text-brand-400 text-sm font-mono break-all">{refStats.link}</p>
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
                    Share on WhatsApp
                  </button>
                </div>
                <div className="bg-zinc-50 dark:bg-zinc-800/60 rounded-xl px-4 py-3">
                  <p className="text-zinc-400 dark:text-zinc-500 text-xs">Friends who signed up</p>
                  <p className="tnum text-3xl font-black text-zinc-900 dark:text-white mt-1">{refStats.count}</p>
                </div>
              </>
            )}
          </div>
        )}

        {/* ── Account ── */}
        {tab === "account" && (
          <div className="space-y-4 max-w-lg">
            {/* Profile */}
            <div className="card p-5 space-y-3">
              <h2 className="text-zinc-900 dark:text-white font-bold text-sm">Profile</h2>
              <div className="flex items-center gap-4">
                {user?.imageUrl
                  ? <img src={user.imageUrl} alt="" className="w-14 h-14 rounded-full" />
                  : <div className="w-14 h-14 bg-brand-600 rounded-full flex items-center justify-center text-white text-xl font-black">{user?.firstName?.[0]}</div>}
                <div>
                  <p className="text-zinc-900 dark:text-white font-semibold">{user?.fullName || "—"}</p>
                  <p className="text-zinc-400 dark:text-zinc-500 text-sm">{user?.emailAddresses[0]?.emailAddress}</p>
                </div>
              </div>
            </div>

            {/* Subscription */}
            <div className="card p-5 space-y-3">
              <h2 className="text-zinc-900 dark:text-white font-bold text-sm flex items-center gap-2">
                <Crown size={14} className="text-amber-500" /> Subscription
              </h2>
              {isPremium ? (
                <div className="space-y-2">
                  <span className="inline-flex text-xs bg-amber-50 dark:bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-200 dark:border-amber-500/30 px-2 py-0.5 rounded-full font-bold">Premium Active</span>
                  <p className="text-zinc-500 dark:text-zinc-400 text-xs">
                    Expires: {new Date((user?.publicMetadata as any)?.subscription_expires).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" })}
                  </p>
                  <p className="text-zinc-400 dark:text-zinc-500 text-xs">To renew, click Pay again on the upgrade modal. Renewals extend from your current expiry date.</p>
                </div>
              ) : (
                <div className="space-y-3">
                  <p className="text-zinc-500 dark:text-zinc-400 text-sm">You&apos;re on the <span className="text-zinc-900 dark:text-white font-semibold">Free plan</span>.</p>
                  <button onClick={() => router.push("/")}
                    className="w-full bg-amber-500 hover:bg-amber-400 text-white font-bold py-2.5 rounded-xl text-sm transition-all flex items-center justify-center gap-2 active:scale-[0.98]">
                    <Crown size={14} /> Upgrade to Premium — ₦1,500/month
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
