"use client";

import { useEffect, useMemo, useState } from "react";
import { Crown, Search, UserPlus, Users as UsersIcon } from "lucide-react";
import { Btn, Card, Pill, Skeleton, inputClass, num, useAdmin } from "../ui";
import { ColumnChart } from "../charts";

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
                      {u.tier === "premium" || (!u.tier && u.premium) ? <Pill tone="info">Premium · {u.days_left}d</Pill>
                        : u.tier === "lite" ? <Pill tone="ok">Lite · {u.days_left}d</Pill> : <Pill>Free</Pill>}
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
