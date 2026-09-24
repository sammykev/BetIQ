"use client";

import { useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { Database, Download, Flag, MousePointerClick, Star, Target } from "lucide-react";
import { TrackRecord } from "@/components/TrackRecord";
import { API, BarList, Btn, Card, Skeleton, Stat, ago, inputClass, num, useAdmin } from "../ui";

const toneOf = (acc: number) => (acc >= 60 ? "text-accent" : acc >= 45 ? "text-warn" : "text-danger");

function SharedModel({ model }: { model: any }) {
  if (!model) return <p className="text-xs text-warn">No shared model yet, so restarts train on the server. Run the &quot;Train model&quot; workflow on GitHub.</p>;
  const stale = (Date.now() - new Date(model.trained_at).getTime()) / 3600000 > model.max_age_hours;
  const by = model.source === "github-actions" ? "GitHub Actions" : model.source === "api-server" ? "this server" : model.source ?? "unknown";
  return (
    <p className={clsx("text-xs", stale ? "text-warn" : "text-n-400")}>
      Model trained by {by} {ago(model.trained_at)}
      {model.rows ? ` · ${num(model.rows)} matches` : ""}{model.size ? ` · ${(model.size / 1e6).toFixed(1)} MB` : ""}
      {stale ? ` · older than ${model.max_age_hours} h — check the "Train model" workflow` : " · restarts load it in seconds"}
    </p>
  );
}

export function PredictionsSection() {
  const { get, post, flash, adminFetch } = useAdmin();
  const [stats, setStats] = useState<any>(null);
  const [data, setData] = useState<any>(null);
  const [popular, setPopular] = useState<any>(null);
  const [preds, setPreds] = useState<any[]>([]);
  const [featured, setFeatured] = useState<any[]>([]);
  const [search, setSearch] = useState("");
  const [result, setResult] = useState({ home: "", away: "", date: "", home_score: "", away_score: "" });
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    get("/api/admin/stats").then(setStats);
    get("/api/admin/data-status").then(setData);
    get("/api/admin/popular").then(setPopular);
    fetch(`${API}/api/predictions?limit=500`).then(r => r.json()).then(d => setPreds(d.predictions ?? [])).catch(() => {});
    fetch(`${API}/api/admin/featured`).then(r => r.json()).then(d => setFeatured(d.featured ?? [])).catch(() => {});
  }, [get]);

  const trackFetch = useMemo(() => (url: string) => adminFetch(url), [adminFetch]);

  const matches = useMemo(() => preds.filter(p =>
    !search || `${p.home} ${p.away} ${p.league_name}`.toLowerCase().includes(search.toLowerCase())).slice(0, 8), [preds, search]);

  const saveFeatured = async (next: any[]) => {
    setBusy("featured");
    try { const d = await post("/api/admin/featured", { picks: next }); setFeatured(d.featured); flash("ok", "Featured picks saved"); }
    catch { flash("err", "Couldn't save featured picks"); }
    setBusy(null);
  };

  const settle = async () => {
    const { home, away, date, home_score, away_score } = result;
    const hs = Number(home_score), as = Number(away_score);
    if (!home || !away || !date || home_score === "" || away_score === "") return;
    setBusy("result");
    try {
      await post("/api/feedback/result", { home, away, date, home_score: hs, away_score: as, result: hs > as ? "H" : hs < as ? "A" : "D" });
      flash("ok", `Saved ${home} ${hs}–${as} ${away}`);
      setResult({ home: "", away: "", date: "", home_score: "", away_score: "" });
    } catch { flash("err", "Couldn't save the result — check the names and date"); }
    setBusy(null);
  };

  const exportCsv = () => {
    const header = "home,away,date,time,league,tip_1x2,tip_goals,p_home,p_draw,p_away,p_over25\n";
    const esc = (v: unknown) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const csv = header + preds.map(p => [p.home, p.away, p.date, p.time, p.league_name, p.tip_1x2, p.tip_goals,
      p.p_home, p.p_draw, p.p_away, p.p_over25].map(esc).join(",")).join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    a.download = `betiq-predictions-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
  };

  return (
    <div className="space-y-4">
      <Card title="Accuracy, last 30 days" icon={<Target size={15} />}>
        {!stats ? <Skeleton rows={4} /> : stats.error ? <p className="text-xs text-n-400">{stats.error}</p> : (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <Stat label="Accuracy" value={<span className={toneOf(stats.overall.accuracy)}>{stats.overall.accuracy}%</span>}
                sub={`${stats.overall.won + stats.overall.lost} settled`} />
              <Stat label="Won" value={num(stats.overall.won)} />
              <Stat label="Lost" value={num(stats.overall.lost)} />
              <Stat label="Pending" value={num(stats.overall.pending)} />
            </div>
            <div className="grid gap-4 lg:grid-cols-2">
              <div>
                <p className="eyebrow mb-2">By league</p>
                <BarList max={100} format={n => `${n}%`}
                  items={Object.entries(stats.by_league as Record<string, any>).sort((a, b) => b[1].total - a[1].total).slice(0, 10)
                    .map(([code, s]) => ({ key: code, label: `${s.flag ?? ""} ${s.name}`, value: s.accuracy, sub: `${s.total} games` }))} />
              </div>
              <div>
                <p className="eyebrow mb-2">By tip</p>
                <BarList max={100} format={n => `${n}%`}
                  items={Object.entries(stats.by_tip as Record<string, any>).map(([tip, s]) => ({
                    key: tip, label: tip, value: s.accuracy, sub: `${s.won}W / ${s.lost}L` }))} />
              </div>
            </div>
          </>
        )}
      </Card>

      <TrackRecord adminFetch={trackFetch} />

      <Card title="Training data" icon={<Database size={15} />}
        subtitle={data?.last_sync?.at ? `League results synced ${ago(data.last_sync.at)}` : "Not synced since the last restart"}>
        {!data ? <Skeleton rows={3} /> : (
          <>
            <SharedModel model={data.shared_model} />
            {data.leagues && (
              <div className="grid grid-cols-3 sm:grid-cols-5 lg:grid-cols-9 gap-2">
                {Object.entries(data.leagues as Record<string, { latest_match: string }>).map(([div, l]) => {
                  const days = Math.floor((Date.now() - new Date(l.latest_match).getTime()) / 86400000);
                  return (
                    <div key={div} className="rounded-lg bg-surface-sunken px-2.5 py-2">
                      <p className="eyebrow">{div}</p>
                      <p className={clsx("text-xs font-semibold tnum", days > 14 ? "text-warn" : "text-n-0")}>{l.latest_match}</p>
                    </div>
                  );
                })}
              </div>
            )}
            {data.last_sync?.report?.failed?.length > 0 && <p className="text-xs text-danger">Sync failed: {data.last_sync.report.failed.join(" · ")}</p>}
            <p className="text-xs text-n-400">
              {data.teams_checked} upcoming teams checked · {Object.keys(data.renamed ?? {}).length} matched to a different training name ·{" "}
              {data.thin_history?.length ?? 0} with under 5 known matches
            </p>
            {data.thin_history?.length > 0 && (
              <p className="text-xs text-n-500">Little history (new club, or a name to add to backend/team_names.py):{" "}
                {data.thin_history.map((t: any) => `${t.team} (${t.matches})`).join(", ")}</p>
            )}
          </>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Featured picks" icon={<Star size={15} />} subtitle="Up to 3, shown at the top of the site">
          <ul className="space-y-1.5">
            {featured.map(f => (
              <li key={`${f.home}${f.away}`} className="flex items-center gap-2 text-sm">
                <Star size={12} className="text-accent" />
                <span className="text-n-0 flex-1 truncate">{f.home} v {f.away}</span>
                <Btn onClick={() => saveFeatured(featured.filter(x => x !== f))} busy={busy === "featured"}>Remove</Btn>
              </li>
            ))}
            {!featured.length && <li className="text-xs text-n-400">None featured.</li>}
          </ul>
          {featured.length < 3 && (
            <>
              <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Find a match to feature" className={inputClass} />
              {search && (
                <ul className="space-y-1">
                  {matches.map(p => (
                    <li key={`${p.home}${p.away}${p.date}`}>
                      <button onClick={() => { saveFeatured([...featured, p]); setSearch(""); }}
                        className="w-full text-left text-xs rounded-lg px-2 py-1.5 hover:bg-surface-sunken">
                        <span className="text-n-0">{p.home} v {p.away}</span>{" "}
                        <span className="text-n-500">{p.date} · {p.league_name} · {p.tip_1x2}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </Card>

        <Card title="Most clicked" icon={<MousePointerClick size={15} />}>
          <BarList items={(popular?.matches ?? []).slice(0, 6).map((m: any) => ({ key: m.name, label: m.name, value: m.clicks }))} />
          <p className="eyebrow">Leagues</p>
          <BarList items={(popular?.leagues ?? []).slice(0, 6).map((l: any) => ({ key: l.code, label: l.code, value: l.clicks }))} />
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Enter a result" icon={<Flag size={15} />}
          subtitle="If a score didn't come through automatically: grades the pick and feeds the next training run">
          <div className="grid grid-cols-2 gap-2">
            <input value={result.home} onChange={e => setResult({ ...result, home: e.target.value })} placeholder="Home team" className={inputClass} />
            <input value={result.away} onChange={e => setResult({ ...result, away: e.target.value })} placeholder="Away team" className={inputClass} />
            <input type="date" value={result.date} onChange={e => setResult({ ...result, date: e.target.value })} className={inputClass} />
            <div className="flex gap-2">
              <input inputMode="numeric" value={result.home_score} onChange={e => setResult({ ...result, home_score: e.target.value.replace(/\D/g, "") })} placeholder="H" className={inputClass} />
              <input inputMode="numeric" value={result.away_score} onChange={e => setResult({ ...result, away_score: e.target.value.replace(/\D/g, "") })} placeholder="A" className={inputClass} />
            </div>
          </div>
          <Btn variant="primary" onClick={settle} busy={busy === "result"}
            disabled={!result.home || !result.away || !result.date || result.home_score === "" || result.away_score === ""}>Save result</Btn>
        </Card>
        <Card title="Export" icon={<Download size={15} />} subtitle={`${num(preds.length)} upcoming predictions`}>
          <Btn onClick={exportCsv} disabled={!preds.length}><Download size={12} /> Download CSV</Btn>
        </Card>
      </div>
    </div>
  );
}
