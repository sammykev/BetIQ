"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Gauge, RefreshCw, Trash2 } from "lucide-react";
import { API, Btn, Card, Skeleton, Stat, Toggle, ago, naira, num, useAdmin } from "../ui";

interface Alert { tone: "warn" | "danger"; text: string; tab?: string }

export function OverviewSection({ goTo }: { goTo: (tab: string) => void }) {
  const { get, post, flash, adminFetch } = useAdmin();
  const [health, setHealth] = useState<any>(null);
  const [stats, setStats] = useState<any>(null);
  const [subs, setSubs] = useState<{ total: number; premium: number } | null>(null);
  const [revenue, setRevenue] = useState<any>(null);
  const [live, setLive] = useState<{ online: number } | null>(null);
  const [security, setSecurity] = useState<any>(null);
  const [data, setData] = useState<any>(null);
  const [paywall, setPaywall] = useState<boolean | null>(null);
  const [maintenance, setMaintenance] = useState<boolean | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API}/api/health`).then(r => r.json()).then(setHealth).catch(() => {});
    fetch(`${API}/api/config/paywall`).then(r => r.json()).then(d => setPaywall(d.enabled)).catch(() => {});
    fetch(`${API}/api/config/maintenance`).then(r => r.json()).then(d => setMaintenance(d.enabled)).catch(() => {});
    get("/api/admin/stats").then(setStats);
    get("/api/admin/revenue").then(setRevenue);
    get("/api/admin/traffic/live").then(setLive);
    get("/api/admin/security").then(setSecurity);
    get("/api/admin/data-status").then(setData);
    adminFetch("/api/admin/subscribers").then(r => r.json()).then(setSubs).catch(() => {});
  }, [get, adminFetch]);

  const toggle = async (key: "paywall" | "maintenance") => {
    setBusy(key);
    try {
      if (key === "paywall") {
        const d = await post("/api/config/paywall", { enabled: !paywall });
        setPaywall(d.enabled); flash("ok", `Paywall ${d.enabled ? "on" : "off"}`);
      } else {
        const d = await post("/api/config/maintenance", { enabled: !maintenance });
        setMaintenance(d.enabled); flash("ok", `Maintenance ${d.enabled ? "on — the site shows a notice" : "off"}`);
      }
    } catch { flash("err", "Couldn't change that setting"); }
    setBusy(null);
  };

  const action = async (key: string, run: () => Promise<unknown>, ok: string) => {
    setBusy(key);
    try { await run(); flash("ok", ok); } catch { flash("err", "That didn't work — try again"); }
    setBusy(null);
  };

  const alerts: Alert[] = [];
  for (const c of security?.checks ?? []) if (!c.ok && c.id !== "traffic_key") alerts.push({ tone: "danger", text: c.fix, tab: "security" });
  if (data?.shared_model === null) alerts.push({ tone: "warn", text: "No shared model: restarts train on Render's small CPU. Run the \"Train model\" workflow on GitHub.", tab: "system" });
  const links = data?.sportybet_links;
  if (links?.at && links.linked === 0) alerts.push({ tone: "warn", text: "No predictions are linked to SportyBet, so codes need a slow lookup. Check the SportyBet tab.", tab: "sportybet" });
  if (revenue?.error) alerts.push({ tone: "warn", text: revenue.error === "no_paystack_key" ? "PAYSTACK_SECRET_KEY isn't set on Render, so revenue can't load." : `Revenue: ${revenue.error}`, tab: "revenue" });
  if (maintenance) alerts.push({ tone: "warn", text: "Maintenance mode is on: visitors see a notice instead of predictions." });
  if (health && !health.model_ready) alerts.push({ tone: "warn", text: "The model isn't loaded yet (the server may be starting)." });

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 lg:grid-cols-6 gap-3">
        <Stat label="On site now" value={live ? num(live.online) : "…"} tone="accent" sub="last 5 minutes" />
        <Stat label="Users" value={subs ? num(subs.total) : "…"} />
        <Stat label="Premium" value={subs ? num(subs.premium) : "…"} tone="info"
          sub={subs?.total ? `${Math.round((subs.premium / subs.total) * 100)}% of users` : undefined} />
        <Stat label="Revenue 30 days" value={revenue && !revenue.error ? naira(revenue.last_30_days) : "—"} />
        <Stat label="Accuracy 30 days" value={stats?.overall ? `${stats.overall.accuracy}%` : "…"}
          sub={stats?.overall ? `${stats.overall.won + stats.overall.lost} settled` : undefined} />
        <Stat label="Predictions" value={health ? num(health.predictions) : "…"}
          sub={health?.last_updated ? `updated ${ago(health.last_updated)}` : undefined} />
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_1.3fr]">
        <Card title="Quick controls" icon={<Gauge size={15} />}>
          <div className="grid gap-2">
            <Toggle on={paywall} busy={busy === "paywall"} onChange={() => toggle("paywall")} label="Paywall"
              hint={paywall ? "Match analysis and the assistant need Premium" : "Everything free for everyone"} />
            <Toggle on={maintenance} busy={busy === "maintenance"} onChange={() => toggle("maintenance")} label="Maintenance mode"
              hint={maintenance ? "Site paused — visitors see a notice" : "Site live"} />
          </div>
          <div className="flex flex-wrap gap-2">
            <Btn busy={busy === "refresh"} onClick={() => action("refresh", () => post("/api/admin/jobs/refresh/run"), "Rebuilding predictions — a few minutes")}>
              <RefreshCw size={12} /> Rebuild predictions
            </Btn>
            <Btn variant="danger" busy={busy === "cache"}
              onClick={() => confirm("Clear cached predictions and AI explanations?") && action("cache", () => post("/api/admin/clear-cache", {}), "Cache cleared")}>
              <Trash2 size={12} /> Clear cache
            </Btn>
          </div>
        </Card>

        <Card title="Needs attention" icon={<AlertTriangle size={15} />}
          subtitle={alerts.length ? `${alerts.length} item${alerts.length === 1 ? "" : "s"}` : undefined}>
          {!security || !data ? <Skeleton rows={3} /> : alerts.length === 0 ? (
            <p className="flex items-center gap-2 text-sm text-accent"><CheckCircle2 size={15} /> All clear.</p>
          ) : (
            <ul className="space-y-2">
              {alerts.map((a, i) => (
                <li key={i} className="flex items-start gap-2 text-sm">
                  <AlertTriangle size={14} className={a.tone === "danger" ? "text-danger mt-0.5 shrink-0" : "text-warn mt-0.5 shrink-0"} />
                  <span className="text-n-200 flex-1">{a.text}</span>
                  {a.tab && <button onClick={() => goTo(a.tab!)} className="text-xs text-accent hover:underline shrink-0">Open</button>}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
