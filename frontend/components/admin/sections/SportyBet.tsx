"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import { AlertTriangle, CheckCircle2, Globe2, Link2, ListChecks, RefreshCw, Zap } from "lucide-react";
import { Btn, Card, Pill, Skeleton, Stat, ago, num, useAdmin } from "../ui";

// What started a linking run (backend _link_sportybet_events)
const TRIGGERS: Record<string, string> = {
  startup: "after a deploy", pipeline: "after new predictions", schedule: "30-minute check",
  manual: "Link now", international: "after internationals",
};

// booking_slip.VERIFIED names, in the order the optimizer shows them
const MARKET_NAMES: Record<string, string> = {
  corners_ou: "Total corners", cards_ou: "Total bookings (cards)", home_goals_ou: "Home team goals",
  away_goals_ou: "Away team goals", "clean_sheet:H": "Home clean sheet", "clean_sheet:A": "Away clean sheet",
  "win_to_nil:H": "Home win to nil", "win_to_nil:A": "Away win to nil", handicap: "Asian handicap",
  dc_goals: "Double chance & goals", home_corners_ou: "Home team corners", away_corners_ou: "Away team corners",
  corners_1x2: "Most corners", shots_ou: "Total shots", sot_ou: "Total shots on target",
  home_shots_ou: "Home team shots", away_shots_ou: "Away team shots",
  home_sot_ou: "Home team shots on target", away_sot_ou: "Away team shots on target",
};

export function SportyBetSection() {
  const { get, flash } = useAdmin();
  const [links, setLinks] = useState<any>(null);
  const [check, setCheck] = useState<any>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [intl, setIntl] = useState<any>(null);

  useEffect(() => {
    get("/api/admin/sportybet-link-status").then(setLinks);
    // Linking also runs on its own (every deploy, every 30 minutes)
    const t = setInterval(() => get("/api/admin/sportybet-link-status").then(l => l?.at && setLinks(l)), 30_000);
    return () => clearInterval(t);
  }, [get]);

  const linkNow = async () => {
    setBusy("link");
    const l = await get("/api/admin/sportybet-link");
    if (l) { setLinks(l); flash("ok", `Linked ${l.linked} of ${l.predictions}`); } else flash("err", "Linking failed");
    setBusy(null);
  };
  const testBooking = async () => {
    setBusy("check"); setCheck(null);
    setCheck(await get("/api/admin/sportybet-check") ?? { ok: false, steps: [{ step: "Request", ok: false, detail: "No response" }] });
    setBusy(null);
  };
  const fetchIntl = async () => {
    setBusy("intl"); setIntl(null);
    setIntl(await get("/api/admin/international-check") ?? { error: "No response" });
    setBusy(null);
  };

  const map: Record<string, any> = links?.market_map ?? {};

  return (
    <div className="space-y-4">
      {!links ? <Card><Skeleton rows={4} /></Card> : (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <Stat label="Bookable now" value={`${num(links.linked)} / ${num(links.predictions)}`} tone={links.linked ? "accent" : "warn"}
              sub="predictions linked to SportyBet" />
            <Stat label="SportyBet lists" value={num(links.events)} sub="upcoming football events" />
            <Stat label="Last linked" value={links.at ? ago(links.at) : "never"}
              sub={links.trigger ? `${TRIGGERS[links.trigger] ?? links.trigger}${links.deploy ? ` · ${links.deploy}` : ""}` : undefined} />
            <Stat label="Took" value={links.seconds != null ? `${links.seconds}s` : "—"} />
          </div>

          <Card title="Instant booking" icon={<Link2 size={15} />}
            subtitle="Predictions are matched to SportyBet's events ahead of time, so a code is one request"
            action={<Btn onClick={linkNow} busy={busy === "link"}><RefreshCw size={12} /> Link now</Btn>}>
            {links.error && <p className="text-xs text-warn">{links.error}</p>}
            {links.report?.length > 0 && <p className="text-xs text-n-400 break-words">{links.report.join(" · ")}</p>}
            {links.catalog?.days && (
              <p className="text-xs text-n-500 break-words">By day: {Object.entries(links.catalog.days as Record<string, number>)
                .map(([d, n]) => `${d.slice(5)} ${n}`).join(" · ")}</p>
            )}
            {links.catalog?.international && Object.keys(links.catalog.international).length > 0 && (
              <p className="text-xs text-n-500 break-words">Internationals on SportyBet: {Object.entries(links.catalog.international as Record<string, number>)
                .map(([t, n]) => `${t} ${n}`).join(" · ")}</p>
            )}
            {links.unlinked?.length > 0 && (
              <details className="text-xs">
                <summary className="cursor-pointer text-n-300 hover:text-n-0">
                  Not linked: {links.predictions - links.linked} (closest SportyBet match shown)
                </summary>
                <ul className="mt-2 space-y-1 text-n-400">
                  {links.unlinked.map((u: any) => (
                    <li key={`${u.match}-${u.date}`}>
                      <span className="text-n-200">{u.match}</span> · {u.date} {u.time} · {u.league} —{" "}
                      {u.closest ? <>closest: {u.closest} ({Math.round(u.score * 100)}%)</> : "nothing that day"}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </Card>

          <Card title="Extra markets" icon={<ListChecks size={15} />}
            subtitle="Codes include these only once SportyBet's own match page confirms the market (checked every linking run)">
            {Object.keys(map).length === 0 ? (
              <p className="text-xs text-n-400">Not checked yet — press Link now.</p>
            ) : (
              <ul className="grid gap-1.5 sm:grid-cols-2">
                {Object.entries(MARKET_NAMES).map(([kind, name]) => {
                  const m = map[kind] ?? {};
                  return (
                    <li key={kind} className="flex items-start gap-2 text-xs rounded-lg bg-surface-sunken px-2.5 py-2">
                      {m.ok ? <CheckCircle2 size={13} className="text-accent mt-px shrink-0" /> : <AlertTriangle size={13} className="text-warn mt-px shrink-0" />}
                      <span className="min-w-0">
                        <span className="text-n-0 font-semibold">{name}</span>{" "}
                        {m.ok ? <span className="text-n-400">— SportyBet &quot;{m.label}&quot; (id {m.id})</span>
                          : <span className="text-n-400">— {m.why ?? "not checked"}{m.label ? ` (id ${m.id}: "${m.label}")` : ""}</span>}
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
            {links.market_labels && Object.keys(links.market_labels).length > 0 && (
              <details className="text-xs">
                <summary className="cursor-pointer text-n-300 hover:text-n-0">All markets SportyBet showed ({Object.keys(links.market_labels).length})</summary>
                <p className="mt-2 text-n-500 break-words">
                  {Object.entries(links.market_labels as Record<string, string>).map(([id, label]) =>
                    `${id}: ${label}${links.market_coverage?.[id] != null ? ` (${links.market_coverage[id]} priced)` : ""}`).join(" · ")}
                </p>
              </details>
            )}
          </Card>
        </>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Live booking test" icon={<Zap size={15} />}
          subtitle="Books a real one-pick code from the server. Codes only load a bet slip; nothing is staked."
          action={<Btn variant="primary" onClick={testBooking} busy={busy === "check"}>Test</Btn>}>
          {check && (
            <div className="space-y-1.5">
              <p className={clsx("text-sm font-semibold", check.ok ? "text-accent" : "text-danger")}>
                {check.ok ? "Booking works" : "Booking is failing"}
                {check.impersonate && <span className="text-n-500 font-normal text-xs ml-2">({check.impersonate}, {check.country})</span>}
              </p>
              {(check.steps ?? []).map((st: any) => (
                <div key={st.step} className="flex items-start gap-2 text-xs">
                  {st.ok ? <CheckCircle2 size={13} className="text-accent mt-px shrink-0" /> : <AlertTriangle size={13} className="text-danger mt-px shrink-0" />}
                  <span className="text-n-200 font-semibold shrink-0">{st.step}</span>
                  <span className="text-n-400 break-words">{st.detail}{st.ms != null && <span className="text-n-500"> · {st.ms} ms</span>}</span>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card title="International fixtures" icon={<Globe2 size={15} />}
          subtitle="Fetch national-team matches now from ESPN, SofaScore and The Odds API, and publish them"
          action={<Btn onClick={fetchIntl} busy={busy === "intl"}>Fetch now</Btn>}>
          {intl?.error && <p className="text-xs text-danger">{intl.error}</p>}
          {intl && !intl.error && (
            <div className="text-xs space-y-1.5">
              <p className={clsx("font-semibold", intl.fixtures > 0 ? "text-accent" : "text-danger")}>
                {intl.fixtures} fixtures found · {intl.published} published
                {!intl.model_ready && " (the model is still loading; they publish when it's ready)"}
              </p>
              {Object.keys(intl.by_competition ?? {}).length > 0 && (
                <p className="text-n-300">{Object.entries(intl.by_competition as Record<string, number>).map(([c, n]) => `${c}: ${n}`).join(" · ")}</p>
              )}
              {([["espn", "ESPN"], ["sofascore", "SofaScore"], ["odds_api", "The Odds API"]] as const).map(([key, label]) => (
                <p key={key} className="text-n-400">{label} — {Object.entries((intl.sources?.[key] ?? {}) as Record<string, number>)
                  .map(([k, n]) => `${k} ${n}`).join(" · ") || "nothing"}</p>
              ))}
              {intl.errors?.length > 0 && (
                <p className="text-danger break-words">Errors: {(intl.errors as string[]).map(e => e.length > 90 ? e.slice(0, 90) + "…" : e).join(" · ")}</p>
              )}
            </div>
          )}
          {!intl && <Pill>Runs by itself with every predictions refresh</Pill>}
        </Card>
      </div>
    </div>
  );
}
