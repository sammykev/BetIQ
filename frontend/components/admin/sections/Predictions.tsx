"use client";

import { useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { Database, Download, Flag, Globe2, MousePointerClick, Star, Target } from "lucide-react";
import { TrackRecord } from "@/components/TrackRecord";
import { API, BarList, Btn, Card, Skeleton, Stat, ago, inputClass, num, useAdmin } from "../ui";

const toneOf = (acc: number) => (acc >= 60 ? "text-accent" : acc >= 45 ? "text-warn" : "text-danger");

const STAT_NAMES: Record<string, string> = {
  corners: "Total corners", bookings: "Total bookings (cards)", corners_home: "Home team corners", corners_away: "Away team corners",
  shots: "Total shots", sot: "Total shots on target", shots_home: "Home team shots", shots_away: "Away team shots",
  sot_home: "Home team shots on target", sot_away: "Away team shots on target",
};

/** One row per stat: used or not, and its score against the average (lower is better). */
function StatChecks({ scores, use }: { scores: Record<string, any>; use: Record<string, boolean> }) {
  return (
    <ul className="grid gap-1.5 sm:grid-cols-2 text-xs">
      {Object.entries(scores).map(([stat, v]) => (
        <li key={stat} className="rounded-lg bg-surface-sunken px-2.5 py-1.5 flex justify-between gap-2">
          <span className="text-n-200">{STAT_NAMES[stat] ?? stat}</span>
          <span className={clsx("tnum", use?.[stat] ? "text-accent" : "text-n-400")}>
            {use?.[stat] ? "used" : "not used"} · score {v.model} vs {v.baseline} average · {num(v.matches)} matches
          </span>
        </li>
      ))}
    </ul>
  );
}

const MARKET_LABELS: Record<string, string> = {
  "1x2": "Result", double_chance: "Double chance", goals_ou: "Goals over/under", btts: "Both score",
  corners_ou: "Corners", cards_ou: "Bookings", dc_goals: "Double chance & goals", handicap: "Handicap",
  home_goals_ou: "Home goals", away_goals_ou: "Away goals",
};

const roiText = (r: any) => (r?.bets ? `${r.roi >= 0 ? "+" : ""}${(r.roi * 100).toFixed(1)}% · ${num(r.bets)} bets` : "no bets yet");

/** Flat 1-unit bets at SportyBet's last pre-match price (price_book.py):
 * every priced outcome, and the ones the model rated as value. */
function EdgeReport() {
  const { get } = useAdmin();
  const [rep, setRep] = useState<any>(null);
  const [days, setDays] = useState(60);
  useEffect(() => { setRep(null); get(`/api/admin/edge-report?days=${days}`).then(setRep).catch(() => setRep({ error: true })); }, [days]);
  if (!rep) return <Skeleton rows={3} />;
  if (rep.error) return <p className="text-xs text-n-400">Couldn&apos;t load the report.</p>;
  const tone = (r: any) => (!r?.bets ? "text-n-400" : r.roi > 0 ? "text-accent" : "text-danger");
  return (
    <div className="space-y-3">
      <div className="flex gap-1.5">
        {[30, 60, 118].map(d => (
          <button key={d} onClick={() => setDays(d)}
            className={clsx("rounded-full px-2.5 py-1 text-xs", d === days ? "bg-accent/15 text-accent" : "bg-surface-sunken text-n-300")}>
            {d} days
          </button>
        ))}
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        <Stat label="Matches priced" value={num(rep.matches ?? 0)} />
        <Stat label="Every priced outcome" value={rep.all_priced?.bets ? `${(rep.all_priced.roi * 100).toFixed(1)}%` : "—"}
          sub={rep.all_priced?.bets ? `${num(rep.all_priced.bets)} bets · SportyBet's margin shows here` : undefined} />
        <Stat label={`Model value (edge ≥ ${Math.round(rep.min_ev * 100)}%)`}
          value={rep.value?.bets ? `${(rep.value.roi * 100).toFixed(1)}%` : "—"}
          tone={rep.value?.bets ? (rep.value.roi > 0 ? "accent" : "danger") : undefined}
          sub={rep.value?.bets ? `${num(rep.value.bets)} bets, ${rep.value.won} won` : undefined} />
      </div>
      {rep.by_market?.length > 0 && (
        <ul className="grid gap-1.5 text-xs">
          {rep.by_market.map((m: any) => (
            <li key={m.market} className="rounded-lg bg-surface-sunken px-2.5 py-1.5 flex flex-wrap justify-between gap-2">
              <span className="text-n-200">{MARKET_LABELS[m.market] ?? m.market}</span>
              <span className="tnum text-n-400">all {roiText(m.all)} · <span className={tone(m.value)}>value {roiText(m.value)}</span></span>
            </li>
          ))}
        </ul>
      )}
      {rep.by_edge?.some((b: any) => b.bets) && (
        <p className="text-[11px] text-n-500 tnum">
          By claimed edge: {rep.by_edge.filter((b: any) => b.bets).map((b: any) => `${b.edge} ${(b.roi * 100).toFixed(1)}% (${b.bets})`).join(" · ")}
        </p>
      )}
      <p className="text-[11px] text-n-500">
        A market is only worth betting once its value bets stay profitable over hundreds of bets; a few dozen is mostly luck.
      </p>
    </div>
  );
}

/** Shots and shots-on-target markets: club model (checked on each league
 * data refresh) and international (checked nightly). */
function Shots({ club, intl }: { club: any; intl: any }) {
  const check = club?.check ?? {};
  const ic = intl?.shots_check ?? {};
  const withShots = intl?.dataset?.with_shots ?? 0;
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        <Stat label="Club model" value={club?.active ? "In use" : "Not yet"} tone={club?.active ? "accent" : undefined}
          sub={club?.at ? `checked ${ago(club.at)}` : club?.reason} />
        <Stat label="International matches with shots" value={num(withShots)}
          sub={intl?.dataset?.matches ? `of ${num(intl.dataset.matches)} collected` : undefined} />
        <Stat label="International model" value={intl?.shots_active ? "In use" : "Not yet"}
          tone={intl?.shots_active ? "accent" : undefined} sub={ic.at ? `checked ${ago(ic.at)}` : undefined} />
      </div>
      {Object.keys(check).length > 0 && <StatChecks scores={check} use={club?.use ?? {}} />}
      {ic.reason && <p className="text-xs text-n-400">Internationals: {ic.reason}.</p>}
      {ic.holdout && Object.keys(ic.holdout).length > 0 && <StatChecks scores={ic.holdout} use={ic.use ?? {}} />}
    </div>
  );
}

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
      {check.holdout && Object.keys(check.holdout).length > 0 && <StatChecks scores={check.holdout} use={check.use ?? {}} />}
      {run && (
        <p className="text-[11px] text-n-500">
          Last collection {ago(run.at)}: ESPN +{run.espn?.matches ?? 0} matches ({run.espn?.requests ?? 0} requests
          {run.espn?.stopped && run.espn.stopped !== "time" ? `, stopped: ${run.espn.stopped}` : ""})
          {" · "}SofaScore {run.sofascore?.stopped === "HTTP 403" ? "blocked" : `+${run.sofascore?.matches ?? 0}`}
          {run.api_football ? ` · API-Football +${run.api_football.matches} (${run.api_football.requests} requests${run.api_football.stopped && run.api_football.stopped !== "budget" ? `, ${run.api_football.stopped}` : ""})` : " · API-Football off"}
        </p>
      )}
    </div>
  );
}

/** Referees appointed to upcoming matches (SofaScore), which scale the cards forecast. */
function Referees({ info, onRun, running, onRunPast }: { info: any; onRun: () => void; running: boolean; onRunPast?: () => void }) {
  const rep = info?.report ?? {};
  const errors: string[] = rep.errors ?? [];
  const blocked = errors.some(e => / 403| 429/.test(e));
  const af = rep.api_football;
  const fd = rep.football_data;
  const past = info?.past ?? {};
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Stat label="Referees found" value={num(info?.count ?? 0)} tone={info?.count ? "accent" : undefined} />
        <Stat label="On predictions" value={num(info?.on_predictions ?? 0)} sub="cards priced with them" />
        <Stat label="Matches listed" value={num(rep.matched ?? 0)} sub="ours, next 4 days on SofaScore" />
        <Stat label="Last check" value={info?.at ? ago(info.at) : "Never"}
          sub={info?.trigger === "github" ? "by the GitHub job" : info?.trigger ? `by ${info.trigger}` : undefined} />
      </div>
      {fd && (
        <p className={clsx("text-xs", fd.skipped || fd.error ? "text-warn" : "text-n-400")}>
          football-data.org: {fd.skipped ?? fd.error ?? `${fd.with_referee} of ${fd.listed} listed matches have a referee · ${fd.found} matched to our predictions`}
        </p>
      )}
      {blocked ? (
        <p className="text-xs text-n-500">SofaScore blocks this server (it has career records when it works); football-data.org and API-Football cover it.</p>
      ) : errors.length > 0 && (
        <p className="text-xs text-warn">SofaScore: {errors.slice(0, 3).join(" · ")}{errors.length > 3 ? ` (+${errors.length - 3})` : ""}</p>
      )}
      {af && (
        <p className={clsx("text-xs", af.skipped || af.errors?.length ? "text-warn" : "text-n-400")}>
          API-Football: {af.skipped ?? `${af.found ?? 0} found in ${num(af.fixtures ?? 0)} fixtures`}
          {af.errors?.length ? ` · ${af.errors[0]}` : ""}
          {` · ${info?.api_football_calls_today ?? 0}/${info?.api_football_cap ?? 12} requests today`}
        </p>
      )}
      {info?.appointments?.length ? (
        <ul className="grid gap-1.5 sm:grid-cols-2 text-xs">
          {info.appointments.slice(0, 20).map((a: any) => (
            <li key={`${a.match}|${a.date}`} className="rounded-lg bg-surface-sunken px-2.5 py-1.5 flex justify-between gap-2">
              <span className="text-n-200 truncate">{a.match}</span>
              <span className="text-n-400 shrink-0">{a.referee}{a.games ? ` · ${a.games} games` : ""} · {a.date}
                {a.source === "api-football" ? " · API-Football" : ""}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-n-400">None named yet. Referees are usually announced two to four days before kick-off;
          this checks every 3 hours and after each predictions rebuild.</p>
      )}
      <div className="rounded-lg bg-surface-sunken px-3 py-2 text-xs text-n-300 space-y-1">
        <p className="font-semibold text-n-0">Past referees, for the referee ratings</p>
        <p className="text-n-400">
          {past.refs ? `${num(past.refs)} matches over ${past.seasons_done} finished seasons from football-data.org${past.at ? ` · updated ${ago(past.at)}` : ""}`
            : "Not collected yet: runs daily (first ~7 minutes after a restart), or start it below."}
          {past.report?.failed?.length ? ` · couldn't read: ${past.report.failed.slice(0, 4).join(", ")}` : ""}
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Btn onClick={onRun} busy={running}>Check now</Btn>
        {onRunPast && <Btn onClick={onRunPast}>Collect past referees</Btn>}
      </div>
    </div>
  );
}

/** Booking codes made by signed-in accounts, and the match-day results job. */
function TicketsAndResults({ info, tickets }: { info: any; tickets: any }) {
  const rep = info?.report ?? {};
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Stat label="Codes made" value={num(tickets?.created ?? 0)} sub={tickets ? `${num(tickets.open_accounts ?? 0)} accounts with open tickets` : undefined} />
        <Stat label="Tickets won" value={tickets && tickets.won + tickets.lost ? `${tickets.won}/${tickets.won + tickets.lost}` : "—"}
          sub={tickets?.hit_rate != null ? `${Math.round(tickets.hit_rate * 100)}% of settled` : "none settled yet"} tone="accent" />
        <Stat label="Results check" value={info?.at ? ago(info.at) : "Never"} sub={info?.trigger ? `by ${info.trigger}` : undefined} />
        <Stat label="Scores updated" value={num(rep.updated ?? 0)}
          sub={rep.matches ? `${rep.matches} matches waiting · ${rep.unmatched ?? 0} not found` : "nothing waiting"} />
      </div>
      {tickets?.sources && Object.keys(tickets.sources).length > 0 && (
        <p className="text-xs text-n-400">By source: {Object.entries(tickets.sources as Record<string, number>).map(([k, v]) => `${k.replace("_", " ")} ${v}`).join(" · ")}</p>
      )}
      {rep.errors?.length > 0 && <p className="text-xs text-warn">Result sources: {rep.errors.slice(0, 3).join(" · ")}</p>}
    </div>
  );
}

/** European competitions and domestic cups from ESPN, and whether training uses them. */
function ClubCups({ info }: { info: any }) {
  if (!info || info.error) return <p className="text-xs text-n-500">European/cup data: {info?.error ?? "not loaded"}</p>;
  if (!info.matches) {
    return <p className="text-xs text-n-400">European and cup matches: none collected yet. They come from the nightly &quot;Collect international stats&quot; workflow on GitHub.</p>;
  }
  const check = info.check ?? {};
  const scores = check.scores ?? {};
  const label = (k: string) => (k === "europe" ? "European competitions" : "Domestic cups");
  return (
    <div className="text-xs text-n-400 space-y-1">
      <p>European and cup matches from ESPN: <span className="text-n-0 font-semibold tnum">{num(info.matches)}</span>
        {" "}({num(info.with_shots)} with shots){info.first ? ` · ${info.first} → ${info.last}` : ""}</p>
      {["europe", "cups"].map(k => scores[k] && (
        <p key={k}>
          {label(k)}: {scores[k].skipped ?? `log loss ${scores[k].with} vs ${scores[k].league_only} without, on ${scores[k].matches} league matches`}
          {" · "}<span className={check.use?.[k] ? "text-accent font-semibold" : "text-n-500"}>{check.use?.[k] ? "used in training" : "not used"}</span>
        </p>
      ))}
      {!check.at && <p className="text-n-500">Not checked yet: the weekly check runs after the collection.</p>}
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
  const [ticketStats, setTicketStats] = useState<any>(null);
  const [popular, setPopular] = useState<any>(null);
  const [preds, setPreds] = useState<any[]>([]);
  const [featured, setFeatured] = useState<any[]>([]);
  const [search, setSearch] = useState("");
  const [result, setResult] = useState({ home: "", away: "", date: "", home_score: "", away_score: "" });
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    get("/api/admin/stats").then(setStats);
    get("/api/admin/data-status").then(setData);
    get("/api/admin/tickets").then(setTicketStats);
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
        subtitle="Collected nightly from ESPN, topped up by API-Football; lower score is better">
        {!data ? <Skeleton rows={2} /> : <InternationalSetPieces info={data.international_set_pieces} />}
      </Card>

      <Card title="Profit at SportyBet prices" icon={<Target size={15} />}
        subtitle="Every outcome we model that SportyBet priced before kick-off, settled at full time: where the model beats SportyBet, and where it doesn't">
        <EdgeReport />
      </Card>

      <Card title="Shots & shots on target" icon={<Target size={15} />}
        subtitle="Each stat is offered only where it beat the league average on matches the model hadn't seen; lower score is better">
        {!data ? <Skeleton rows={2} /> : <Shots club={data.shots} intl={data.international_set_pieces} />}
      </Card>

      <Card title="Results & booking codes" icon={<Target size={15} />}
        subtitle="Scores come from ESPN every 15 minutes while matches are on; every code a signed-in user makes is settled from them"
        action={<Btn onClick={async () => { await post("/api/admin/jobs/matchday_sweep/run"); flash("ok", "Checking the last 7 days' scores…"); }}>Check now</Btn>}>
        {!data ? <Skeleton rows={2} /> : <TicketsAndResults info={data.matchday} tickets={ticketStats} />}
      </Card>

      <Card title="Referees" icon={<Flag size={15} />}
        subtitle="Appointed referees for upcoming matches (football-data.org, SofaScore, API-Football); they adjust the cards forecast">
        {!data ? <Skeleton rows={2} /> : <Referees info={data.referees} onRun={runReferees} running={refRunning}
          onRunPast={async () => { try { await post("/api/admin/jobs/fd_referees/run"); flash("ok", "Collecting past referees — about 7 minutes"); } catch { flash("err", "Couldn't start it"); } }} />}
      </Card>

      <Card title="Training data" icon={<Database size={15} />}
        subtitle={data?.last_sync?.at ? `League results synced ${ago(data.last_sync.at)}` : "Not synced since the last restart"}>
        {!data ? <Skeleton rows={3} /> : (
          <>
            <SharedModel model={data.shared_model} />
            <ClubCups info={data.club_cups} />
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
