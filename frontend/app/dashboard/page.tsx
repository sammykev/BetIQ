"use client";

import { useUser } from "@clerk/nextjs";
import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import clsx from "clsx";
import {
  Ticket, BarChart2, User, Link2, Trophy,
  Check, Copy, Crown, RefreshCw, Share2,
} from "lucide-react";
import { AppShell } from "@/components/shell/AppShell";
import { PageHeader } from "@/components/shell/PageHeader";
import { planLabel } from "@/lib/pricing";
import { tierOf } from "@/lib/subscription";
import { useAccess } from "@/lib/access";
import { Unavailable } from "@/components/FeatureGate";
import { PaywallModal } from "@/components/PaywallModal";
import { useAuthedFetch } from "@/lib/useAuthedFetch";
import { TicketsList } from "@/components/TicketsList";

const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

type Tab = "overview" | "codes" | "referral" | "account";

interface Code {
  code: string; games: { game: string; tip: string; odds: string }[];
  total_odds: number; date: string; saved_at: string;
}

// The record from the account's booked codes (backend tickets.summary)
interface TicketRecord {
  tickets: number; won: number; lost: number; void: number; pending: number; hit_rate: number | null;
  legs_won: number; legs_lost: number; leg_hit_rate: number | null;
  units: number; roi: number | null; streak: number; streak_type: "won" | "lost" | null;
  best_win: { code: string; odds: number } | null;
  by_market: { market: string; won: number; lost: number; hit_rate: number }[];
}

interface Stats {
  tickets?: TicketRecord;
  saved_count: number; codes_count: number;
}

/** GET a JSON endpoint; anything that isn't a 2xx JSON body comes back as null. */
async function getJson(doFetch: (url: string) => Promise<Response>, url: string): Promise<unknown> {
  try {
    const r = await doFetch(url);
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}
const asList = <T,>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);

function StatCard({ label, value, color = "text-n-0", sub }: { label: string; value: React.ReactNode; color?: string; sub?: string }) {
  return (
    <div className="card px-4 py-3.5">
      <p className="eyebrow">{label}</p>
      <p className={clsx("font-display font-extrabold text-4xl leading-none mt-1.5 tnum", color)}>{value}</p>
      {sub && <p className="text-[11px] text-n-500 mt-1 capitalize">{sub}</p>}
    </div>
  );
}

function EmptyState({ icon, title, body }: { icon: React.ReactNode; title: string; body: string }) {
  return (
    <div className="card border-dashed text-center py-14 px-6 space-y-2">
      <div className="mx-auto w-11 h-11 rounded-2xl bg-n-800/70 flex items-center justify-center text-n-400">{icon}</div>
      <p className="font-display font-bold text-xl uppercase tracking-wide text-n-0 pt-1">{title}</p>
      <p className="text-sm text-n-400">{body}</p>
    </div>
  );
}

export default function DashboardPage() {
  const { user, isLoaded } = useUser();
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("overview");
  const [plans, setPlans] = useState(false);
  const access = useAccess();
  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get("tab");
    if (t && ["overview", "codes", "referral", "account"].includes(t)) setTab(t as Tab);
  }, []);

  const [stats,  setStats]  = useState<Stats | null>(null);
  const [codes,  setCodes]  = useState<Code[]>([]);
  const [refStats, setRefStats] = useState<{ code: string; count: number; link: string } | null>(null);
  // `name`/`you` from the current API; `uid` from the pre-auth API during a rollout
  const [leaderboard, setLeaderboard] = useState<{ name?: string; you?: boolean; uid?: string; wins: number }[]>([]);

  const [copied, setCopied] = useState(false);
  const [loadingTab, setLoadingTab] = useState(false);

  const uid = user?.id ?? "";
  const authFetch = useAuthedFetch();

  const fetchAll = useCallback(async () => {
    if (!uid) return;
    setLoadingTab(true);
    try {
      const q = `uid=${encodeURIComponent(uid)}`;
      const [s, c, ref, lb] = await Promise.all([
        getJson(authFetch, `${API}/api/user/stats?${q}`),
        getJson(authFetch, `${API}/api/user/codes?${q}`),
        getJson(authFetch, `${API}/api/referral/stats?${q}`),
        getJson(authFetch, `${API}/api/leaderboard?${q}`),
      ]);
      // Offline / error replies arrive as objects like {"error": "offline"} —
      // only accept the shapes each view actually renders.
      setStats(s && typeof (s as Stats).tickets === "object" ? (s as Stats) : null);
      setCodes(asList<Code>(c));
      setRefStats(ref && typeof (ref as { link?: unknown }).link === "string" ? (ref as { code: string; count: number; link: string }) : null);
      setLeaderboard(asList(lb));
    } finally {
      setLoadingTab(false);
    }
  }, [uid, authFetch]);

  useEffect(() => {
    if (isLoaded && !user) router.push("/");
  }, [isLoaded, user, router]);

  useEffect(() => { if (uid) fetchAll(); }, [uid, fetchAll]);

  const copyRef = () => {
    if (!refStats) return;
    navigator.clipboard.writeText(refStats.link);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const tier = tierOf(user?.publicMetadata as Record<string, unknown> | undefined);
  const isPremium = tier !== "free";
  const tierName = tier === "premium" ? "Premium" : "Lite";

  if (access.ready && !access.shown("dashboard")) return <AppShell><Unavailable title="Dashboard" /></AppShell>;

  if (!isLoaded) return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="w-8 h-8 border-2 border-brand-400 border-t-transparent rounded-full animate-spin" />
    </div>
  );

  const TABS: { id: Tab; label: string; count?: number; icon: React.ReactNode }[] = [
    { id: "overview",  label: "Overview",  icon: <BarChart2 size={14} /> },
    { id: "codes",     label: "Tickets",   count: stats?.tickets?.tickets ?? codes.length, icon: <Ticket size={14} /> },
    { id: "referral",  label: "Referrals", icon: <Link2 size={14} /> },
    { id: "account",   label: "Account",   icon: <User size={14} /> },
  ];

  const refreshAction = (
    <button onClick={fetchAll} className="btn-secondary !px-3 !py-1.5 !text-xs !rounded-lg">
      <RefreshCw size={12} className={clsx(loadingTab && "animate-spin")} />
      <span className="hidden sm:inline">Refresh</span>
    </button>
  );

  const rec = stats?.tickets;

  return (
    <AppShell actions={refreshAction} onUpgrade={() => setPlans(true)}>
      <div className="space-y-6 animate-fade-in">
        <PageHeader
          eyebrow={`Welcome back${user?.firstName ? `, ${user.firstName}` : ""}`}
          title="Dashboard"
          description="Your record from the SportyBet codes you booked here."
          right={isPremium && (
            <span className="inline-flex items-center gap-1.5 font-display font-bold text-sm uppercase tracking-wider text-warn bg-amber-400/10 border border-amber-400/30 px-3 py-1 rounded-lg">
              <Crown size={13} /> {tierName}
            </span>
          )}
        />

        {/* Tabs */}
        <div role="tablist" className="flex gap-6 border-b border-n-800 overflow-x-auto -mx-4 px-4 sm:mx-0 sm:px-0">
          {TABS.map(t => {
            const active = tab === t.id;
            return (
              <button key={t.id} role="tab" aria-selected={active} onClick={() => setTab(t.id)}
                className={clsx(
                  "relative shrink-0 inline-flex items-center gap-1.5 pb-3 font-display font-bold text-[16px] uppercase tracking-[0.06em] transition-colors",
                  active ? "text-n-0" : "text-n-500 hover:text-n-300"
                )}>
                <span className={active ? "text-accent" : undefined}>{t.icon}</span>
                {t.label}
                {t.count != null && t.count > 0 && <span className="font-sans text-[11px] font-bold text-n-500 tnum">{t.count}</span>}
                {active && <span className="absolute left-0 right-0 -bottom-px h-[2px] bg-accent rounded-full" />}
              </button>
            );
          })}
        </div>

        {/* ── Overview ── */}
        {tab === "overview" && (
          <div className="grid lg:grid-cols-[1fr_340px] gap-4 items-start">
            <div className="space-y-3">
              {rec && rec.tickets > 0 ? (
                <>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <StatCard label="Tickets won" value={rec.hit_rate == null ? "—" : `${Math.round(rec.hit_rate * 100)}%`}
                      color={rec.hit_rate == null ? "text-n-400" : rec.hit_rate >= 0.5 ? "text-accent" : rec.hit_rate >= 0.3 ? "text-warn" : "text-danger"}
                      sub={`${rec.won} of ${rec.won + rec.lost} settled`} />
                    <StatCard label="Picks won" value={rec.leg_hit_rate == null ? "—" : `${Math.round(rec.leg_hit_rate * 100)}%`}
                      color={rec.leg_hit_rate == null ? "text-n-400" : rec.leg_hit_rate >= 0.6 ? "text-accent" : rec.leg_hit_rate >= 0.45 ? "text-warn" : "text-danger"}
                      sub={`${rec.legs_won} of ${rec.legs_won + rec.legs_lost} legs`} />
                    <StatCard label="Return" value={rec.roi == null ? "—" : `${rec.roi > 0 ? "+" : ""}${Math.round(rec.roi * 100)}%`}
                      color={rec.roi == null ? "text-n-400" : rec.roi >= 0 ? "text-accent" : "text-danger"}
                      sub={`${rec.units >= 0 ? "+" : "−"}${Math.abs(rec.units).toFixed(1)} units at 1 a ticket`} />
                    <StatCard label="Streak" value={rec.streak || "—"}
                      color={rec.streak_type === "won" ? "text-accent" : rec.streak_type === "lost" ? "text-danger" : "text-n-400"}
                      sub={rec.streak_type ? `${rec.streak_type} in a row` : undefined} />
                  </div>
                  <div className="card px-4 py-4">
                    <div className="flex items-center justify-between">
                      <p className="eyebrow">Your tickets</p>
                      <p className="text-xs text-n-500 tnum">{rec.tickets} booked · {rec.pending} still open</p>
                    </div>
                    <div className="flex h-2 rounded-full overflow-hidden gap-0.5 mt-3 bg-n-800">
                      {[
                        { n: rec.won, cls: "bg-accent" },
                        { n: rec.lost, cls: "bg-rose-500/80" },
                        { n: rec.void, cls: "bg-n-600" },
                        { n: rec.pending, cls: "bg-n-700" },
                      ].map(({ n, cls }, i) => n > 0 && (
                        <div key={i} className={cls} style={{ flexGrow: n }} />
                      ))}
                    </div>
                    <div className="flex flex-wrap gap-x-5 gap-y-1 mt-3 text-sm">
                      <span className="text-n-400"><span className="font-display font-bold text-xl text-accent tnum mr-1">{rec.won}</span>won</span>
                      <span className="text-n-400"><span className="font-display font-bold text-xl text-danger tnum mr-1">{rec.lost}</span>lost</span>
                      {rec.void > 0 && <span className="text-n-400"><span className="font-display font-bold text-xl text-n-300 tnum mr-1">{rec.void}</span>void</span>}
                      <span className="text-n-400"><span className="font-display font-bold text-xl text-n-300 tnum mr-1">{rec.pending}</span>open</span>
                      {rec.best_win && (
                        <span className="text-n-400 ml-auto">Best win <span className="font-display font-bold text-xl text-accent tnum">{rec.best_win.odds.toFixed(2)}x</span></span>
                      )}
                    </div>
                  </div>
                  {rec.by_market.length > 0 && (
                    <div className="card px-4 py-4 space-y-2.5">
                      <p className="eyebrow">Picks by market</p>
                      {rec.by_market.slice(0, 8).map(m => (
                        <div key={m.market} className="space-y-1">
                          <div className="flex justify-between text-xs">
                            <span className="text-n-200">{m.market}</span>
                            <span className="text-n-400 tnum">{m.won}/{m.won + m.lost} · <span className="font-semibold text-n-0">{Math.round(m.hit_rate * 100)}%</span></span>
                          </div>
                          <div className="h-1.5 rounded-full bg-n-800 overflow-hidden">
                            <div className={clsx("h-full rounded-full", m.hit_rate >= 0.6 ? "bg-accent" : m.hit_rate >= 0.45 ? "bg-warn" : "bg-rose-500/80")}
                              style={{ width: `${Math.round(m.hit_rate * 100)}%` }} />
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                  <p className="text-[11px] text-n-500">
                    From the SportyBet codes you booked on BetIQ while signed in, settled from the final scores. Return
                    assumes the same stake on every ticket.
                  </p>
                </>
              ) : loadingTab ? (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  {Array.from({ length: 4 }).map((_, i) => (
                    <div key={i} className="card px-4 py-4 space-y-3"><div className="skeleton h-3 w-16" /><div className="skeleton h-8 w-20 !rounded-lg" /></div>
                  ))}
                </div>
              ) : (
                <EmptyState icon={<Ticket size={20} />} title="No tickets yet"
                  body="Book a SportyBet code from your bet slip or the optimizer while signed in, and your record builds itself here as the matches finish." />
              )}
            </div>

            {/* Leaderboard */}
            <div className="card p-4">
              <div className="flex items-center gap-2 mb-2">
                <Trophy size={15} className="text-warn" />
                <h2 className="font-display font-bold text-lg uppercase tracking-[0.06em] text-n-0">Top predictors</h2>
              </div>
              {leaderboard.length === 0
                ? <p className="text-n-400 text-sm py-2">No entries yet. Book codes here while signed in — every winning ticket counts.</p>
                : <ol className="divide-y divide-n-800/70">
                    {leaderboard.slice(0, 10).map((e, i) => {
                      const you = e.you ?? e.uid === uid;
                      const name = e.name ?? `#${(e.uid ?? "").slice(-6)}`;
                      return (
                        <li key={`${name}-${i}`} className={clsx("flex items-center gap-3 py-2.5", you && "bg-brand-400/[0.07] -mx-2 px-2 rounded-lg")}>
                          <span className={clsx("font-display font-extrabold text-xl w-6 text-center tnum",
                            i === 0 ? "text-warn" : i === 1 ? "text-n-300" : i === 2 ? "text-orange-600 dark:text-orange-400" : "text-n-500")}>
                            {i + 1}
                          </span>
                          <span className={clsx("flex-1 text-sm", you ? "text-n-0 font-semibold" : "text-n-400 font-mono text-xs")}>
                            {you ? "You" : name}
                          </span>
                          <span className="font-display font-bold text-lg text-accent tnum">{e.wins}<span className="text-xs text-n-400 ml-0.5">W</span></span>
                        </li>
                      );
                    })}
                  </ol>}
            </div>
          </div>
        )}


        {/* ── Tickets: booking codes, settled from the results ── */}
        {tab === "codes" && <TicketsList uid={uid} authFetch={authFetch} />}

        {/* ── Referrals ── */}
        {tab === "referral" && (
          <div className="card p-6 space-y-5 max-w-lg">
            <div>
              <span className="inline-flex items-center justify-center w-11 h-11 rounded-xl bg-brand-400/10 text-accent ring-1 ring-brand-400/20">
                <Link2 size={20} />
              </span>
              <h2 className="display text-4xl text-n-0 mt-4">Invite friends</h2>
              <p className="text-n-400 text-sm mt-2">
                When a friend signs up and subscribes, you both get <span className="text-accent font-semibold">30 days of premium free</span>.
              </p>
            </div>
            {refStats ? (
              <>
                <div className="bg-surface-sunken border border-n-800 rounded-xl px-4 py-3">
                  <p className="eyebrow mb-1">Your referral link</p>
                  <p className="text-accent text-sm font-mono break-all">{refStats.link}</p>
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
                <div className="flex items-baseline justify-between border-t border-n-800 pt-4">
                  <p className="eyebrow">Friends who signed up</p>
                  <p className="font-display font-extrabold text-4xl text-n-0 leading-none tnum">{refStats.count}</p>
                </div>
              </>
            ) : (
              <p className="text-sm text-n-500">Your referral link isn&apos;t available right now. Try refreshing.</p>
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
                  ? <img src={user.imageUrl} alt="" className="w-14 h-14 rounded-full ring-2 ring-n-800" />
                  : <div className="w-14 h-14 bg-brand-400 rounded-full flex items-center justify-center text-ink text-2xl font-display font-extrabold">{user?.firstName?.[0]}</div>}
                <div className="min-w-0">
                  <p className="text-n-0 font-semibold truncate">{user?.fullName || "—"}</p>
                  <p className="text-n-400 text-sm truncate">{user?.emailAddresses[0]?.emailAddress}</p>
                </div>
              </div>
            </div>

            <div className="card p-5 space-y-3">
              <p className="eyebrow flex items-center gap-1.5"><Crown size={12} className="text-warn" /> Subscription</p>
              {isPremium ? (
                <div className="space-y-2">
                  <p className="font-display font-extrabold text-3xl uppercase text-warn leading-none">{tierName}</p>
                  <p className="text-n-400 text-sm">
                    Active until {new Date((user?.publicMetadata as any)?.subscription_expires).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" })}
                  </p>
                  <p className="text-n-500 text-xs">Renewing extends from your current expiry date.</p>
                  {tier === "lite" && (
                    <button onClick={() => setPlans(true)} className="btn-secondary w-full !text-sm"><Crown size={14} /> Upgrade to Premium</button>
                  )}
                </div>
              ) : (
                <div className="space-y-3">
                  <p className="font-display font-extrabold text-3xl uppercase text-n-0 leading-none">Free plan</p>
                  <button onClick={() => setPlans(true)}
                    className="w-full bg-amber-400 hover:bg-amber-300 text-ink font-bold py-2.5 rounded-xl text-sm transition-all flex items-center justify-center gap-2 active:scale-[0.98]">
                    <Crown size={14} /> See plans · from {planLabel("lite")}
                  </button>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
      {plans && <PaywallModal onClose={() => setPlans(false)} onSuccess={() => setPlans(false)} />}
    </AppShell>
  );
}
