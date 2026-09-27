"use client";

import { useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { ChevronDown, FlaskConical, Layers, Search, ToggleRight, Trophy, UserPlus, X } from "lucide-react";
import { API, Btn, Card, Pill, Skeleton, inputClass, useAdmin } from "../ui";
import { refreshAccess } from "@/lib/access";

// Feature switches (backend access.py): every page, sport and feature, who
// sees it (on / testers / off), the tier it needs, and the accounts given it.

type State = "on" | "testers" | "off";
type Tier = "free" | "lite" | "premium";
interface Account { id: string; label: string }
interface Feature {
  id: string; label: string; group: string; about: string;
  state: State; tier: Tier; allow: Account[]; default_state: State; default_tier: Tier;
}

const STATES: { id: State; label: string; hint: string }[] = [
  { id: "on", label: "On", hint: "Everyone sees it" },
  { id: "testers", label: "Testers", hint: "Only the accounts below (and admins)" },
  { id: "off", label: "Off", hint: "Nobody sees it" },
];
const TIERS: { id: Tier; label: string }[] = [{ id: "free", label: "Free" }, { id: "lite", label: "Lite" }, { id: "premium", label: "Premium" }];
const GROUP_ICON: Record<string, JSX.Element> = { Pages: <Layers size={15} />, Sports: <Trophy size={15} />, Features: <ToggleRight size={15} /> };

function Segmented<T extends string>({ value, options, onChange, disabled, tone }: {
  value: T; options: { id: T; label: string; hint?: string }[]; onChange: (v: T) => void; disabled?: boolean;
  tone?: (v: T) => string;
}) {
  return (
    <div className="inline-flex rounded-lg border border-n-800 bg-surface-sunken p-0.5">
      {options.map(o => (
        <button key={o.id} type="button" title={o.hint} disabled={disabled} onClick={() => o.id !== value && onChange(o.id)}
          className={clsx("px-2.5 py-1 rounded-md text-xs font-semibold transition-colors disabled:opacity-60",
            o.id === value ? clsx("bg-surface shadow-card", tone ? tone(o.id) : "text-n-0") : "text-n-400 hover:text-n-0")}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Accounts({ f, users, busy, onSave }: {
  f: Feature; users: { id: string; email: string }[]; busy: boolean; onSave: (allow: Account[]) => void;
}) {
  const [q, setQ] = useState("");
  const matches = useMemo(() => {
    const t = q.trim().toLowerCase();
    if (!t) return [];
    return users.filter(u => !f.allow.some(a => a.id === u.id) && u.email.toLowerCase().includes(t)).slice(0, 6);
  }, [q, users, f.allow]);
  const add = (a: Account) => { onSave([...f.allow, a]); setQ(""); };
  // A pasted Clerk user id (user_...) for accounts outside the newest 100
  const rawId = /^user_[A-Za-z0-9]{10,}$/.test(q.trim()) && !f.allow.some(a => a.id === q.trim()) ? q.trim() : null;
  return (
    <div className="mt-3 rounded-xl border border-n-800 bg-surface-sunken p-3 space-y-2.5">
      <p className="text-[11px] text-n-400">
        {f.state === "testers" ? "Only these accounts (and admins) see it." : "These accounts get it whatever their plan."}
      </p>
      {f.allow.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {f.allow.map(a => (
            <span key={a.id} className="inline-flex items-center gap-1 rounded-full border border-n-700 bg-surface pl-2.5 pr-1 py-0.5 text-xs text-n-200">
              {a.label}
              <button type="button" aria-label={`Remove ${a.label}`} disabled={busy}
                onClick={() => onSave(f.allow.filter(x => x.id !== a.id))}
                className="p-0.5 rounded-full hover:bg-n-800 text-n-400 hover:text-danger"><X size={12} /></button>
            </span>
          ))}
        </div>
      ) : <p className="text-xs text-n-500">No accounts yet.</p>}
      <div className="relative">
        <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-n-500" />
        <input value={q} onChange={e => setQ(e.target.value)} placeholder="Add by email (or paste a user id)"
          className={`${inputClass} pl-8 !py-1.5 !text-xs`} />
      </div>
      {(matches.length > 0 || rawId) && (
        <ul className="rounded-lg border border-n-800 bg-surface divide-y divide-n-800 overflow-hidden">
          {matches.map(u => (
            <li key={u.id}>
              <button type="button" disabled={busy} onClick={() => add({ id: u.id, label: u.email })}
                className="w-full flex items-center gap-2 px-3 py-2 text-xs text-left text-n-200 hover:bg-surface-raised">
                <UserPlus size={12} className="text-accent" />{u.email}
              </button>
            </li>
          ))}
          {rawId && (
            <li>
              <button type="button" disabled={busy} onClick={() => add({ id: rawId, label: rawId })}
                className="w-full flex items-center gap-2 px-3 py-2 text-xs text-left text-n-200 hover:bg-surface-raised">
                <UserPlus size={12} className="text-accent" />Add user id {rawId}
              </button>
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

function Row({ f, users, onChange }: { f: Feature; users: { id: string; email: string }[]; onChange: (id: string, change: Partial<Feature>) => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const save = async (change: Partial<Feature>) => { setBusy(true); await onChange(f.id, change); setBusy(false); };
  const changed = f.state !== f.default_state || f.tier !== f.default_tier;
  return (
    <li className="py-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1 basis-56">
          <p className="text-sm font-semibold text-n-0 flex items-center gap-2">
            {f.label}
            {f.state === "testers" && <Pill tone="warn">Testing</Pill>}
            {f.state === "off" && <Pill>Off</Pill>}
          </p>
          <p className="text-xs text-n-400">{f.about}{changed && <span className="text-n-500"> · default {f.default_state}, {f.default_tier}</span>}</p>
        </div>
        <Segmented value={f.state} options={STATES} disabled={busy} onChange={v => save({ state: v })}
          tone={v => (v === "on" ? "text-accent" : v === "testers" ? "text-warn" : "text-danger")} />
        <Segmented value={f.tier} options={TIERS} disabled={busy || f.state === "off"} onChange={v => save({ tier: v })} />
        <button type="button" onClick={() => setOpen(o => !o)} aria-expanded={open}
          className={clsx("inline-flex items-center gap-1 text-xs font-semibold rounded-lg px-2 py-1 border",
            f.allow.length ? "border-warn/40 text-warn" : "border-n-800 text-n-400 hover:text-n-0")}>
          <FlaskConical size={12} /> {f.allow.length} account{f.allow.length === 1 ? "" : "s"}
          <ChevronDown size={12} className={clsx("transition-transform", open && "rotate-180")} />
        </button>
      </div>
      {open && <Accounts f={f} users={users} busy={busy} onSave={allow => save({ allow })} />}
    </li>
  );
}

export function AccessSection() {
  const { adminFetch, get, flash } = useAdmin();
  const [features, setFeatures] = useState<Feature[] | null>(null);
  const [paywall, setPaywall] = useState<boolean>(true);
  const [users, setUsers] = useState<{ id: string; email: string }[]>([]);

  useEffect(() => {
    get("/api/admin/features").then(d => { if (d) { setFeatures(d.features); setPaywall(d.paywall !== false); } });
    adminFetch("/api/admin/users").then(r => r.json()).then(d => setUsers((d?.users ?? []).map((u: any) => ({ id: u.id, email: u.email }))))
      .catch(() => {});
  }, [get, adminFetch]);

  const change = async (id: string, patch: Partial<Feature>) => {
    try {
      const r = await adminFetch(`${API}/api/admin/features/${encodeURIComponent(id)}`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) });
      const d = await r.json();
      if (!r.ok) throw new Error(d?.detail || "Couldn't save");
      setFeatures(fs => fs?.map(f => (f.id === id ? { ...f, ...d } : f)) ?? fs);
      refreshAccess();
      flash("ok", "Saved: takes effect within a few seconds");
    } catch (e: any) { flash("err", e?.message || "Couldn't save"); }
  };

  if (!features) return <Card title="Access"><Skeleton rows={6} /></Card>;
  const groups = Array.from(new Set(features.map(f => f.group)));
  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-n-800 bg-surface-sunken px-4 py-3 text-xs text-n-400 space-y-1">
        <p><span className="text-n-0 font-semibold">On</span>: everyone sees it and it needs the plan shown.{" "}
          <span className="text-warn font-semibold">Testers</span>: only the accounts you add (and admins) see it.{" "}
          <span className="text-danger font-semibold">Off</span>: hidden from everyone, including you.</p>
        <p>Accounts you add get the feature whatever their plan. Plans: Free, Lite (₦5,000) and Premium (₦8,500); the pricing
          window lists each plan&apos;s perks from these settings.</p>
        {!paywall && <p className="text-warn">The paywall is off (Overview), so every plan requirement is ignored right now.</p>}
      </div>
      {groups.map(g => (
        <Card key={g} title={g} icon={GROUP_ICON[g] ?? <Layers size={15} />}>
          <ul className="divide-y divide-n-800 -my-3">
            {features.filter(f => f.group === g).map(f => <Row key={f.id} f={f} users={users} onChange={change} />)}
          </ul>
        </Card>
      ))}
    </div>
  );
}
