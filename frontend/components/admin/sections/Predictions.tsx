"use client";

import { useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { Database, Download, Flag, Globe2, MousePointerClick, Star, Target } from "lucide-react";
import { TrackRecord } from "@/components/TrackRecord";
import { API, BarList, Btn, Card, Skeleton, Stat, ago, inputClass, num, useAdmin } from "../ui";

const toneOf = (acc: number) => (acc >= 60 ? "text-accent" : acc >= 45 ? "text-warn" : "text-danger");

const STAT_NAMES: Record<string, string> = {
  corners: "Total corners", bookings: "Total bookings (cards)", corners_home: "Home team corners", corners_away: "Away team corners",
};

/** International corners/cards: data collected nightly (GitHub Actions) and
 * whether the model beat the competition average on the last 12 months. */
function InternationalSetPieces({ info }: { info: any }) {
  const ds = info?.dataset;
  const check = info?.check ?? {};
  if (!ds || !ds.matches) {
    return (
      <p className="text-xs text-n-400">
        No international corner/card data yet. Add the UPSTASH_REDIS_URL secret on GitHub (and APIFOOTBALL_KEY for the
        API-Football top-up), then run the &quot;Collect international stats&quot; workflow. Until then internationals use
        SportyBet&apos;s own corners and bookings lines.
      </p>
    );
  }
  const run = ds.last_run;
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Stat label="Matches" value={num(ds.matches)} sub={ds.first ? `${ds.first} → ${ds.last}` : undefined} />
        <Stat label="Sources" value={Object.keys(ds.sources ?? {}).length}
          sub={Object.entries(ds.sources ?? {}).map(([k, v]) => `${k} ${num(v as number)}`).join(" · ")} />
        <Stat label="Backfill" value={ds.backfill_complete ? "Done" : "Running"} tone={ds.backfill_complete ? "accent" : "warn"}
          sub={ds.oldest_day_scanned ? `back to ${ds.oldest_day_scanned}` : undefined} />
        <Stat label="Model" value={info.active ? "In use" : "Not yet"} tone={info.active ? "accent" : undefined}
          sub={check.at ? `checked ${ago(check.at)}` : undefined} />
      </div>
      {check.reason && <p className="text-xs text-n-400">{check.reason} — internationals use SportyBet&apos;s lines meanwhile.</p>}
      {check.holdout && Object.keys(check.holdout).length > 0 && (
        <ul className="grid gap-1.5 sm:grid-cols-2 text-xs">
          {Object.entries(check.holdout as Record<string, any>).map(([stat, v]) => (
            <li key={stat} className="rounded-lg bg-surface-sunken px-2.5 py-1.5 flex justify-between gap-2">
              <span className="text-n-200">{STAT_NAMES[stat] ?? stat}</span>
              <span className={clsx("tnum", check.use?.[stat] ? "text-accent" : "text-n-400")}>
                {check.use?.[stat] ? "used" : "not used"} · score {v.model} vs {v.baseline} average · {num(v.matches)} matches
              </span>
            </li>
          ))}
        </ul>
      )}
      {run && (
        <p className="text-[11px] text-n-500">
          Last collection {ago(run.at)}: SofaScore {run.sofascore?.days ?? 0} days, +{run.sofascore?.matches ?? 0} matches
          {run.sofascore?.stopped && run.sofascore.stopped !== "time" ? ` (stopped: ${run.sofascore.stopped})` : ""}
          {run.api_football ? ` · API-Football +${run.api_football.matches} (${run.api_football.requests} requests${run.api_football.stopped && run.api_football.stopped !== "budget" ? `, ${run.api_football.stopped}` : ""})` : " · API-Football off"}
        </p>
      )}
    </div>
  );
}

/** Referees appointed to upcoming matches (SofaScore), which scale the cards forecast. */
function Referees({ info, onRun, running }: { info: any; onRun: () => void; running: boolean }) {
  const rep = info?.report ?? {};
  const errors: string[] = rep.errors ?? [];
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Stat label="Referees found" value={num(info?.count ?? 0)} tone={info?.count ? "accent" : undefined} />
        <Stat label="On predictions" value={num(info?.on_predictions ?? 0)} sub="cards priced with them" />
        <Stat label="Matches listed" value={num(rep.matched ?? 0)} sub="ours, next 4 days on SofaScore" />
        <Stat label="Last found" value={info?.at ? ago(info.at) : "Never"}
          sub={info?.checked ? `tried ${ago(info.checked)}` : info?.trigger ? `by ${info.trigger}` : undefined} />
      </div>
      {errors.length > 0 && (
        <p className="text-xs text-warn">SofaScore: {errors.slice(0, 3).join(" · ")}{errors.length > 3 ? ` (+${errors.length - 3})` : ""}</p>
      )}
      {info?.appointments?.length ? (
        <ul className="grid gap-1.5 sm:grid-cols-2 text-xs">
          {info.appointments.slice(0, 20).map((a: any) => (
            <li key={`${a.match}|${a.date}`} className="rounded-lg bg-surface-sunken px-2.5 py-1.5 flex justify-between gap-2">
              <span className="text-n-200 truncate">{a.match}</span>
              <span className="text-n-400 shrink-0">{a.referee}{a.games ? ` · ${a.games} games` : ""} · {a.date}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-n-400">None named yet. Referees are usually announced two to four days before kick-off;
          this checks every 3 hours and after each predictions rebuild.</p>
      )}
      <Btn onClick={onRun} busy={running}>Check now</Btn>
    </div>
  );
}

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

  // The lookup runs in the background (a page per match, ~1–2 min): re-read the status after
  const runReferees = async () => {
    setBusy("referees");
    try {
      const d = await post("/api/admin/jobs/referees/run");
      flash("ok", d.message ?? "Started");
      setTimeout(() => { get("/api/admin/data-status").then(setData); setBusy(null); }, 45_000);
    } catch { flash("err", "Couldn't start the referee check"); setBusy(null); }
  };
  const refRunning = busy === "referees";

  // Downloads the league files now; predictions rebuild by themselves if any changed
  const syncNow = async () => {
    setBusy("sync");
    try {
      await post("/api/admin/jobs/football_sync/run");
      flash("ok", "Downloading league results…");
      setTimeout(() => { get("/api/admin/data-status").then(setData); setBusy(null); }, 30_000);
    } catch { flash("err", "Couldn't start the download"); setBusy(null); }
  };

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

      <Card title="International corners & cards" icon={<Globe2 size={15} />}
        subtitle="Collected nightly from SofaScore and API-Football; lower score is better">
        {!data ? <Skeleton rows={2} /> : <InternationalSetPieces info={data.international_set_pieces} />}
      </Card>

      <Card title="Referees" icon={<Flag size={15} />}
        subtitle="Appointed referees for upcoming matches (SofaScore); they adjust the cards forecast">
        {!data ? <Skeleton rows={2} /> : <Referees info={data.referees} onRun={runReferees} running={refRunning} />}
      </Card>

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
            {data.last_sync?.report?.failed?.length > 0 && (
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-xs text-danger">Sync failed: {data.last_sync.report.failed.join(" · ")}
                  <span className="text-n-400"> — retried automatically every 30 minutes</span></p>
                <Btn onClick={syncNow} busy={busy === "sync"}>Download again</Btn>
              </div>
            )}
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
            {/* iPhones show an empty date field with no hint: label it */}
            <label className="relative block">
              {!result.date && <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-n-500">Match date</span>}
              <input type="date" aria-label="Match date" value={result.date}
                onChange={e => setResult({ ...result, date: e.target.value })} className={inputClass} />
            </label>
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
