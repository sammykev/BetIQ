"use client";

import { useEffect, useState } from "react";
import { Cpu, Play, Server } from "lucide-react";
import { API, Btn, Card, Pill, Skeleton, Stat, ago, num, useAdmin } from "../ui";

export function SystemSection() {
  const { get, post, flash } = useAdmin();
  const [jobs, setJobs] = useState<any>(null);
  const [health, setHealth] = useState<any>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = () => {
    get("/api/admin/jobs").then(setJobs);
    fetch(`${API}/api/health`).then(r => r.json()).then(setHealth).catch(() => setHealth({ status: "down" }));
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, []);

  const run = async (id: string, name: string) => {
    setBusy(id);
    try { const d = await post(`/api/admin/jobs/${id}/run`); flash(d.started ? "ok" : "err", d.message ?? `${name} started`); }
    catch { flash("err", `${name} didn't start`); }
    setBusy(null);
    setTimeout(load, 1500);
  };

  const until = (iso: string | null) => {
    if (!iso) return "not scheduled";
    const m = Math.round((new Date(iso).getTime() - Date.now()) / 60000);
    return m <= 0 ? "due now" : m < 60 ? `in ${m} min` : `in ${Math.round(m / 60)} h`;
  };

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat label="Backend" value={health ? (health.status === "ok" ? "Up" : "Down") : "…"} tone={health?.status === "ok" ? "accent" : "danger"} />
        <Stat label="Model" value={health ? (health.model_ready ? "Ready" : "Loading") : "…"} />
        <Stat label="Predictions" value={num(health?.predictions)} />
        <Stat label="Updated" value={health?.last_updated ? ago(health.last_updated) : "—"} />
      </div>
      <Card title="Scheduled jobs" icon={<Cpu size={15} />} subtitle="They run by themselves; start one now if you need it sooner"
        action={jobs?.training ? <Pill tone="warn">Rebuilding predictions…</Pill> : undefined}>
        {!jobs ? <Skeleton rows={4} /> : (
          <ul className="divide-y divide-n-800">
            {jobs.jobs.map((j: any) => (
              <li key={j.id} className="flex items-center gap-3 py-2 text-sm">
                <Server size={13} className="text-n-500 shrink-0" />
                <span className="text-n-0 flex-1 min-w-0 truncate">{j.name}</span>
                <span className="text-xs text-n-400 hidden sm:inline">next {until(j.next_run)}</span>
                <Btn busy={busy === j.id} onClick={() => run(j.id, j.name)}><Play size={11} /> Run now</Btn>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
