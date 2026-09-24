"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, History, ShieldCheck, ShieldAlert } from "lucide-react";
import { Card, Pill, Skeleton, Stat, ago, num, useAdmin } from "../ui";

const EVENT_NAMES: Record<string, string> = {
  admin_bad_secret: "Wrong admin secret", admin_lockout: "IP locked out (too many wrong secrets)",
  rate_limited: "Rate limit hit",
};

const ACTIONS: Record<string, string> = {
  paywall: "Paywall", maintenance: "Maintenance mode", banner: "Banner", featured: "Featured picks",
  clear_cache: "Cleared cache", refresh: "Rebuilt predictions", sportybet_link: "Linked SportyBet",
  international_check: "Fetched internationals", result: "Entered a result", run_job: "Ran a job",
};

function detail(e: Record<string, unknown>): string {
  return Object.entries(e).filter(([k]) => !["at", "actor", "action", "type", "ip", "path", "country"].includes(k))
    .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : String(v)}`).join(" · ");
}

export function SecuritySection() {
  const { get } = useAdmin();
  const [sec, setSec] = useState<any>(null);
  const [audit, setAudit] = useState<any[] | null>(null);

  useEffect(() => {
    get("/api/admin/security").then(setSec);
    get("/api/admin/audit").then(d => setAudit(d?.entries ?? []));
  }, [get]);

  const failing = (sec?.checks ?? []).filter((c: any) => !c.ok);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat label="Checks passing" value={sec ? `${sec.checks.length - failing.length}/${sec.checks.length}` : "…"}
          tone={failing.length ? "warn" : "accent"} />
        <Stat label="Wrong admin secrets (24h)" value={num(sec?.last_24h?.admin_bad_secret)} tone={sec?.last_24h?.admin_bad_secret ? "danger" : undefined} />
        <Stat label="Lockouts (24h)" value={num(sec?.last_24h?.admin_lockout)} tone={sec?.last_24h?.admin_lockout ? "danger" : undefined} />
        <Stat label="Rate limited (24h)" value={num(sec?.last_24h?.rate_limited)} />
      </div>

      <Card title="Protection checks" icon={<ShieldCheck size={15} />}>
        {!sec ? <Skeleton rows={5} /> : (
          <ul className="space-y-2">
            {sec.checks.map((c: any) => (
              <li key={c.id} className="flex items-start gap-2 text-sm">
                {c.ok ? <CheckCircle2 size={15} className="text-accent mt-0.5 shrink-0" /> : <AlertTriangle size={15} className="text-warn mt-0.5 shrink-0" />}
                <span className="min-w-0">
                  <span className="text-n-0">{c.label}</span>
                  {!c.ok && <span className="block text-xs text-n-400">{c.fix}</span>}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Security events" icon={<ShieldAlert size={15} />} subtitle="IPs are shortened; nothing identifies a person">
          {!sec ? <Skeleton rows={4} /> : !sec.events.length ? <p className="text-xs text-n-400">Nothing unusual.</p> : (
            <ul className="divide-y divide-n-800 text-xs max-h-96 overflow-y-auto">
              {sec.events.map((e: any, i: number) => (
                <li key={i} className="py-1.5 flex flex-wrap items-center gap-x-2">
                  <Pill tone={e.type === "rate_limited" ? "muted" : "danger"}>{EVENT_NAMES[e.type] ?? e.type}</Pill>
                  <span className="text-n-300 font-mono">{e.ip}</span>
                  {e.country && <span className="text-n-500">{e.country}</span>}
                  <span className="text-n-500 truncate">{e.path}{e.rule ? ` (${e.rule})` : ""}</span>
                  <span className="ml-auto text-n-500">{ago(e.at)}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Admin activity" icon={<History size={15} />} subtitle="Every change made from this panel">
          {!audit ? <Skeleton rows={4} /> : !audit.length ? <p className="text-xs text-n-400">No changes logged yet.</p> : (
            <ul className="divide-y divide-n-800 text-xs max-h-96 overflow-y-auto">
              {audit.map((e, i) => (
                <li key={i} className="py-1.5">
                  <div className="flex items-center gap-2">
                    <span className="text-n-0 font-semibold">{ACTIONS[e.action] ?? e.action}</span>
                    <span className="text-n-500 truncate">{e.actor === "secret" ? "admin secret" : "signed-in admin"}</span>
                    <span className="ml-auto text-n-500 shrink-0">{ago(e.at)}</span>
                  </div>
                  {detail(e) && <p className="text-n-400 break-words">{detail(e)}</p>}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {sec && (
        <Card title="Limits in force" icon={<ShieldCheck size={15} />}>
          <p className="text-xs text-n-400">Browsers may call the API only from: {sec.allowed_origins.join(", ")}</p>
          <ul className="grid gap-1 sm:grid-cols-2 lg:grid-cols-3 text-xs">
            {sec.rate_limits.map((r: any) => (
              <li key={r.path} className="flex justify-between rounded-lg bg-surface-sunken px-2.5 py-1.5">
                <span className="text-n-300 font-mono truncate">{r.path}</span>
                <span className="text-n-0 tnum shrink-0 ml-2">{r.limit}/{r.per_seconds}s per IP</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
