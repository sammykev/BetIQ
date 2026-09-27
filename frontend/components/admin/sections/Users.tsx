"use client";

import { useEffect, useMemo, useState } from "react";
import { Crown, Gift, Search, UserPlus, Users as UsersIcon } from "lucide-react";
import { API, Btn, Card, Pill, Skeleton, Stat, Toggle, inputClass, num, useAdmin } from "../ui";
import { ColumnChart } from "../charts";

const REFUSED: Record<string, string> = {
  email_used: "email already used", device_used: "device already used", phone_used: "phone already used",
  ip_limit: "network limit", disposable_email: "throwaway email", email_unverified: "email not verified",
  needs_phone: "asked to verify phone",
};

/** The free trial new accounts get (backend /api/trial, started by the site's /api/trial). */
function FreeTrial({ stats }: { stats?: { active: number; started: number; converted: number } }) {
  const { get, adminFetch, flash } = useAdmin();
  const [cfg, setCfg] = useState<any>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [blocked, setBlocked] = useState<Record<string, number>>({});
  useEffect(() => {
    get("/api/trial").then(d => setCfg(d ?? { error: true }));
    get("/api/admin/trial/stats").then(d => setBlocked(d ?? {}));
  }, [get]);

  const save = async (patch: object, key: string) => {
    setBusy(key);
    try {
      const r = await adminFetch(`${API}/api/admin/trial`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) });
      const d = await r.json();
      if (!r.ok) throw new Error(d?.detail || "Couldn't save");
      setCfg(d);
      flash("ok", d.enabled ? `New accounts get ${d.days} days of ${d.tier === "lite" ? "Lite" : "Premium"} free` : "Free trial off");
    } catch (e: any) {
      flash("err", e instanceof TypeError ? "Couldn't reach the server, so nothing was saved" : e?.message || "Couldn't save");
    }
    setBusy(null);
  };

  if (!cfg) return <Skeleton rows={3} />;
  if (cfg.error) return <p className="text-xs text-n-400">Couldn&apos;t load the trial settings.</p>;
  return (
    <div className="space-y-3">
      <div className="grid gap-2 grid-cols-1 sm:grid-cols-2">
        <Toggle on={cfg.enabled} busy={busy === "enabled"} onChange={() => save({ enabled: !cfg.enabled }, "enabled")}
          label={cfg.enabled ? "Free trial on" : "Free trial off"}
          hint={cfg.enabled && cfg.since ? `For accounts made since ${new Date(cfg.since).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}` : "New accounts start on Free"} />
        <div className="flex flex-wrap items-center gap-2 rounded-xl bg-surface-sunken px-3 py-2.5">
          {(["lite", "premium"] as const).map(t => (
            <button key={t} disabled={busy !== null} onClick={() => save({ tier: t }, "tier")}
              className={cfg.tier === t ? "chip chip-active" : "chip chip-idle"}>{t === "lite" ? "Lite" : "Premium"}</button>
          ))}
          <select value={cfg.days} disabled={busy !== null} onChange={e => save({ days: Number(e.target.value) }, "days")}
            className="rounded-md bg-surface border border-n-800 px-2 py-1 text-xs text-n-200" aria-label="Trial length">
            {[3, 5, 7, 10, 14, 30].map(d => <option key={d} value={d}>{d} days</option>)}
          </select>
        </div>
      </div>
      <div className="grid gap-2 grid-cols-1 sm:grid-cols-2">
        {/* Phone numbers are a Clerk Pro feature: without them nobody could verify,
            so the switch only shows to turn it off if it was ever turned on */}
        {cfg.require_phone ? (
          <Toggle on busy={busy === "phone"} onChange={() => save({ require_phone: false }, "phone")}
            label="Require a verified phone" hint="Needs Clerk Pro: switch off unless you have it" />
        ) : (
          <div className="rounded-xl bg-surface-sunken px-3 py-2.5 text-sm">
            <span className="block font-semibold text-n-0">Phone check: off</span>
            <span className="block text-[11px] text-n-400">One trial per phone needs Clerk Pro (phone numbers)</span>
          </div>
        )}
        <label className="flex items-center justify-between gap-3 rounded-xl bg-surface-sunken px-3 py-2.5 text-sm">
          <span className="min-w-0">
            <span className="block font-semibold text-n-0">Per network, per week</span>
            <span className="block text-[11px] text-n-400">Trials from one IP address in 7 days</span>
          </span>
          <select value={cfg.per_ip_week ?? 3} disabled={busy !== null} onChange={e => save({ per_ip_week: Number(e.target.value) }, "ip")}
            className="rounded-md bg-surface border border-n-800 px-2 py-1 text-xs text-n-200">
            {[1, 2, 3, 5, 10, 25].map(n => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
      </div>
      {cfg.require_phone && (
        <p className="rounded-lg border border-warn/30 bg-warn/10 px-3 py-2 text-[11px] text-n-300">
          Phone numbers are a Clerk Pro feature. Without them new users can&apos;t verify and won&apos;t get a trial:
          switch this off unless your Clerk plan has them.
        </p>
      )}
      {stats && (
        <div className="grid grid-cols-3 gap-3">
          <Stat label="On a trial now" value={num(stats.active)} />
          <Stat label="Trials started" value={num(stats.started)} sub="newest 500 accounts" />
          <Stat label="Went on to pay" value={num(stats.converted)}
            sub={stats.started ? `${Math.round((stats.converted / stats.started) * 100)}% of trials` : undefined} />
        </div>
      )}
      {Object.keys(blocked).some(k => k.startsWith("refused:")) && (
        <p className="text-[11px] text-n-400">
          <span className="font-semibold text-n-200">Refused:</span>{" "}
          {Object.entries(blocked).filter(([k]) => k.startsWith("refused:")).map(([k, n]) =>
            `${REFUSED[k.slice(8)] ?? k.slice(8)} ${num(n)}`).join(" · ")}
        </p>
      )}
      <p className="text-[11px] text-n-500">
        One trial per email (Gmail dots and +tags count as the same address), per device and, if required, per phone; throwaway
        email addresses get none. A new account gets the plan from sign-up for the days set, once, with no card; it starts on their first visit. Accounts made before
        the trial was switched on don&apos;t get one. Paying for the same plan during a trial adds 30 days on top of what&apos;s left.
      </p>
    </div>
  );
}

export function UsersSection() {
  const { adminFetch, flash } = useAdmin();
  const [data, setData] = useState<any>(null);
  const [q, setQ] = useState("");
  const [plan, setPlan] = useState<"all" | "premium" | "lite" | "free">("all");
  const [email, setEmail] = useState("");
  const [giveTier, setGiveTier] = useState<"lite" | "premium">("premium");
  const [days, setDays] = useState("30");
  const [busy, setBusy] = useState<string | null>(null);

  const load = () => adminFetch("/api/admin/users").then(r => r.json()).then(setData).catch(() => setData({ error: true }));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, []);

  const TIER_NAME = { lite: "Lite", premium: "Premium" } as const;
  /** Give a plan (tier, for `n` days) or, with tier null, remove it. */
  const setPlanFor = async (target: string, tier: "lite" | "premium" | null, n = 30) => {
    if (!tier && !confirm(`Remove the plan from ${target}?`)) return;
    setBusy(target);
    try {
      const r = await adminFetch("/api/admin/grant", {
        method: tier ? "POST" : "DELETE", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(tier ? { email: target, tier, days: n } : { email: target }),
      });
      const d = await r.json();
      if (!d.ok) throw new Error(d.error);
      flash("ok", tier ? `Gave ${n} days of ${TIER_NAME[tier]} to ${target}` : `Removed the plan from ${target}`);
      setEmail("");
      load();
    } catch (e: any) { flash("err", e?.message === "user_not_found" ? "No account with that email" : "Couldn't update that user"); }
    setBusy(null);
  };

  const users: any[] = data?.users ?? [];
  const shown = useMemo(() => users.filter(u =>
    (plan === "all" || (u.tier ?? (u.premium ? "premium" : "free")) === plan) &&
    (!q || `${u.email} ${u.name}`.toLowerCase().includes(q.toLowerCase()))), [users, q, plan]);

  const growth = useMemo(() => {
    const days = Array.from({ length: 30 }, (_, i) => new Date(Date.now() - (29 - i) * 86400000).toISOString().slice(0, 10));
    return { labels: days.map(d => d.slice(5)), values: days.map(d => data?.growth?.[d] ?? 0) };
  }, [data]);

  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
        <Card title="Sign-ups" icon={<UsersIcon size={15} />} subtitle={data?.total ? `${num(data.total)} accounts` : undefined}>
          {!data ? <Skeleton rows={3} /> : <ColumnChart name="Sign-ups by day" labels={growth.labels} values={growth.values} tick={5} />}
        </Card>
        <Card title="Give a plan" icon={<Crown size={15} />} subtitle="By account email, free of charge">
          <form className="space-y-2" onSubmit={e => { e.preventDefault(); if (email) setPlanFor(email.trim(), giveTier, Number(days) || 30); }}>
            <input value={email} onChange={e => setEmail(e.target.value)} placeholder="user@email.com" className={inputClass} />
            <div className="flex gap-2">
              {(["lite", "premium"] as const).map(t => (
                <button type="button" key={t} onClick={() => setGiveTier(t)} className={giveTier === t ? "chip chip-active" : "chip chip-idle"}>{TIER_NAME[t]}</button>
              ))}
              <input inputMode="numeric" value={days} onChange={e => setDays(e.target.value.replace(/\D/g, "").slice(0, 3))}
                aria-label="Days" className={`${inputClass} !w-20`} />
              <span className="text-xs text-n-400 self-center">days</span>
              <Btn type="submit" variant="primary" busy={busy === email.trim()} disabled={!email.trim()}><UserPlus size={12} /> Give</Btn>
            </div>
          </form>
        </Card>
      </div>

      <Card title="Free trial" icon={<Gift size={15} />} subtitle="What new accounts get when they sign up">
        <FreeTrial stats={data?.trials} />
      </Card>

      <Card title="Accounts" icon={<UsersIcon size={15} />} subtitle="Newest 100">
        <div className="flex flex-wrap gap-2">
          <div className="relative flex-1 min-w-[200px]">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-n-500" />
            <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search email or name" className={`${inputClass} pl-9`} />
          </div>
          {(["all", "premium", "lite", "free"] as const).map(p => (
            <button key={p} onClick={() => setPlan(p)} className={plan === p ? "chip chip-active" : "chip chip-idle"}>
              {p === "all" ? "Everyone" : p === "premium" ? "Premium" : p === "lite" ? "Lite" : "Free"}
            </button>
          ))}
        </div>
        {!data ? <Skeleton rows={6} /> : data.error ? <p className="text-sm text-warn">Couldn&apos;t load accounts (Clerk).</p> : (
          <div className="overflow-x-auto -mx-4 sm:mx-0">
            <table className="w-full text-xs min-w-[560px]">
              <thead>
                <tr className="text-left text-n-400 border-b border-n-800">
                  <th className="py-2 px-4 sm:px-2 font-semibold">Account</th>
                  <th className="py-2 px-2 font-semibold">Joined</th>
                  <th className="py-2 px-2 font-semibold">Plan</th>
                  <th className="py-2 px-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-n-800">
                {shown.map(u => (
                  <tr key={u.id}>
                    <td className="py-2 px-4 sm:px-2 min-w-0">
                      <p className="text-n-0 truncate max-w-[260px]">{u.email}</p>
                      {u.name !== "—" && <p className="text-n-500 truncate max-w-[260px]">{u.name}</p>}
                    </td>
                    <td className="py-2 px-2 text-n-400 tnum whitespace-nowrap">{new Date(u.created).toISOString().slice(0, 10)}</td>
                    <td className="py-2 px-2">
                      {u.tier === "premium" || (!u.tier && u.premium) ? <Pill tone="info">Premium{u.trial ? " trial" : ""} · {u.days_left}d</Pill>
                        : u.tier === "lite" ? <Pill tone="ok">Lite{u.trial ? " trial" : ""} · {u.days_left}d</Pill> : <Pill>Free</Pill>}
                    </td>
                    <td className="py-2 px-2 text-right">
                      <span className="inline-flex gap-1.5 justify-end">
                        {u.tier !== "lite" && <Btn busy={busy === u.email} onClick={() => setPlanFor(u.email, "lite")}>Lite</Btn>}
                        {u.tier !== "premium" && <Btn busy={busy === u.email} onClick={() => setPlanFor(u.email, "premium")}>Premium</Btn>}
                        {u.tier && u.tier !== "free" && <Btn variant="danger" busy={busy === u.email} onClick={() => setPlanFor(u.email, null)}>Remove</Btn>}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!shown.length && <p className="text-xs text-n-400 py-3 px-4 sm:px-0">No matches.</p>}
          </div>
        )}
      </Card>
    </div>
  );
}
