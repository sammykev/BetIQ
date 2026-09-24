"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity, Globe2, Megaphone, MapPin, MousePointerClick, Smartphone, Clock, Radio } from "lucide-react";
import { API, BarList, Btn, Card, Skeleton, Stat, ago, countryName, flag, num, useAdmin } from "../ui";
import { ColumnChart, LineChart } from "../charts";
import { WorldMap, type CityPoint } from "../WorldMap";

type Top = { key: string; count: number }[];
interface TrafficData {
  range: { from: string; to: string; days: number };
  totals: { pageviews: number; visitors: number; sessions: number; new_visitors: number; pages_per_session: number | null };
  series: { date: string; pageviews: number; visitors: number; sessions: number }[];
  countries: { code: string; pageviews: number; sessions: number }[];
  cities: CityPoint[];
  pages: Top; landing: Top; referrers: Top; utm: Top; devices: Top; browsers: Top; os: Top; languages: Top; plans: Top;
  hours: number[];
  live: Live;
}
interface Live {
  online: number; places: [string, number][]; pins: { lat: number | null; lon: number | null }[];
  recent: { at: number; country: string; city: string; path: string; device: string; plan: string; referrer: string }[];
}

const RANGES = [7, 30, 90];

/** "whatsapp/social/-" → "whatsapp / social" */
const campaign = (key: string) => key.split("/").filter(p => p && p !== "-").join(" / ") || "(untagged)";

const share = (part: number, whole: number) => (whole ? Math.round((part / whole) * 100) : 0);

/** UTC hour counts → the viewer's local hours */
function localHours(utc: number[]): number[] {
  const offset = -new Date().getTimezoneOffset() / 60;
  return Array.from({ length: 24 }, (_, h) => utc[((h - Math.round(offset)) % 24 + 24) % 24] ?? 0);
}

function insights(d: TrafficData): string[] {
  const out: string[] = [];
  const pv = d.totals.pageviews;
  if (!pv) return out;
  const c = d.countries[0];
  if (c) out.push(`${share(c.pageviews, pv)}% of page views come from ${countryName(c.code)}${
    d.countries[1] ? `, then ${countryName(d.countries[1].code)} (${share(d.countries[1].pageviews, pv)}%)` : ""}.`);
  const topCities = d.cities.slice(0, 3);
  if (topCities.length) out.push(`Top cities: ${topCities.map(x => `${x.city} (${share(x.pageviews, pv)}%)`).join(", ")} — good geo targets for ads.`);
  const mobile = d.devices.find(x => x.key === "mobile")?.count ?? 0;
  const os = d.os[0];
  out.push(`${share(mobile, pv)}% of visits are on phones${os ? `; most use ${os.key} (${share(os.count, pv)}%)` : ""} — design ads for mobile first.`);
  const hours = localHours(d.hours);
  const peak = hours.indexOf(Math.max(...hours));
  if (hours[peak] > 0) out.push(`Busiest hour: ${String(peak).padStart(2, "0")}:00–${String((peak + 1) % 24).padStart(2, "0")}:00 your time — schedule ads and pushes just before.`);
  const src = d.referrers[0];
  if (src) out.push(`Top source: ${src.key} (${num(src.count)} visits).`);
  const utm = d.utm[0];
  if (utm) out.push(`Best tagged campaign: ${campaign(utm.key)} (${num(utm.count)} visits).`);
  else out.push("No tagged campaigns yet — add ?utm_source=…&utm_campaign=… to ad links to see which ads bring visitors.");
  const premium = d.plans.find(x => x.key === "premium")?.count ?? 0;
  if (premium) out.push(`${share(premium, pv)}% of page views are by premium members.`);
  return out;
}

export function TrafficSection() {
  const { get, adminFetch } = useAdmin();
  const [days, setDays] = useState(30);
  const [data, setData] = useState<TrafficData | null>(null);
  const [loading, setLoading] = useState(false);
  const [live, setLive] = useState<Live | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    const d = await get<TrafficData>(`/api/admin/traffic?days=${days}`);
    setData(d); if (d) setLive(d.live);
    setLoading(false);
  }, [get, days]);

  useEffect(() => { load(); }, [load]);

  // "On the site now" refreshes by itself (memory on the server: no Redis cost)
  useEffect(() => {
    const t = setInterval(async () => {
      const r = await adminFetch(`${API}/api/admin/traffic/live`).catch(() => null);
      if (r?.ok) setLive(await r.json());
    }, 20_000);
    return () => clearInterval(t);
  }, [adminFetch]);

  const pv = data?.totals.pageviews ?? 0;
  const hours = data ? localHours(data.hours) : [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {RANGES.map(r => (
          <button key={r} onClick={() => setDays(r)} className={r === days ? "chip chip-active" : "chip chip-idle"}>Last {r} days</button>
        ))}
        <Btn onClick={load} busy={loading}>Refresh</Btn>
        <span className="ml-auto inline-flex items-center gap-1.5 text-xs text-n-300">
          <Radio size={13} className="text-accent" />
          <span className="tnum font-semibold text-n-0">{live?.online ?? 0}</span> on the site now
        </span>
      </div>

      {!data ? <Card><Skeleton rows={5} /></Card> : (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
            <Stat label="Visitors" value={num(data.totals.visitors)} sub="unique browsers" tone="accent" />
            <Stat label="Page views" value={num(pv)} />
            <Stat label="Visits" value={num(data.totals.sessions)} sub="30 min apart = new visit" />
            <Stat label="New visitors" value={num(data.totals.new_visitors)} sub={`${share(data.totals.new_visitors, data.totals.visitors)}% of visitors`} />
            <Stat label="Pages / visit" value={data.totals.pages_per_session ?? "—"} />
          </div>

          <Card title="Visitors by day" icon={<Activity size={15} />} subtitle={`${data.range.from} → ${data.range.to}`}>
            {pv === 0 ? (
              <p className="text-xs text-n-400">No page views recorded yet. They start as soon as this version is live on Vercel and Render
                (the site reports each page view to the backend).</p>
            ) : (
              <LineChart labels={data.series.map(s => s.date)} series={[
                { name: "Visitors", values: data.series.map(s => s.visitors) },
                { name: "Page views", values: data.series.map(s => s.pageviews) },
              ]} />
            )}
          </Card>

          <Card title="Where visitors are" icon={<Globe2 size={15} />}
            subtitle="City-level location from Vercel. No IP addresses are stored.">
            <WorldMap cities={data.cities} live={live?.pins ?? []} />
          </Card>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Countries" icon={<MapPin size={15} />}>
              <BarList items={data.countries.slice(0, 12).map(c => ({
                key: c.code, label: `${flag(c.code)} ${countryName(c.code)}`, value: c.pageviews,
                sub: `${share(c.pageviews, pv)}% · ${num(c.sessions)} visits`,
              }))} />
            </Card>
            <Card title="Cities" icon={<MapPin size={15} />}>
              <BarList items={data.cities.slice(0, 12).map(c => ({
                key: `${c.city}|${c.country}`, label: `${flag(c.country)} ${c.city || "Unknown"}`, value: c.pageviews,
                sub: c.region || undefined,
              }))} />
            </Card>
          </div>

          <Card title="Ad targeting" icon={<Megaphone size={15} />} subtitle="What these numbers say about who to reach, and when">
            <ul className="space-y-1.5 text-sm text-n-200 list-disc pl-5">
              {insights(data).map((t, i) => <li key={i}>{t}</li>)}
            </ul>
          </Card>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Busiest hours" icon={<Clock size={15} />} subtitle="Page views by hour of day, in your time zone">
              <ColumnChart name="Page views by hour" labels={hours.map((_, h) => String(h).padStart(2, "0"))}
                values={hours} tick={3} />
            </Card>
            <Card title="Devices" icon={<Smartphone size={15} />}>
              <BarList items={data.devices.map(d => ({ key: d.key, label: d.key, value: d.count, sub: `${share(d.count, pv)}%` }))} />
              <div className="grid grid-cols-2 gap-4 pt-1">
                <div><p className="eyebrow mb-2">Systems</p>
                  <BarList items={data.os.slice(0, 5).map(d => ({ key: d.key, label: d.key, value: d.count }))} /></div>
                <div><p className="eyebrow mb-2">Browsers</p>
                  <BarList items={data.browsers.slice(0, 5).map(d => ({ key: d.key, label: d.key, value: d.count }))} /></div>
              </div>
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <Card title="Sources" icon={<MousePointerClick size={15} />} subtitle="Sites visitors came from">
              <BarList items={data.referrers.map(d => ({ key: d.key, label: d.key, value: d.count }))} empty="Mostly direct visits so far" />
            </Card>
            <Card title="Campaigns" icon={<Megaphone size={15} />} subtitle="utm_source / medium / campaign">
              <BarList items={data.utm.map(d => ({ key: d.key, label: campaign(d.key), value: d.count }))}
                empty="Tag ad links with utm_… to see them here" />
            </Card>
            <Card title="Pages" icon={<Activity size={15} />}>
              <BarList items={data.pages.slice(0, 8).map(d => ({ key: d.key, label: d.key, value: d.count }))} />
              <p className="eyebrow pt-1">First page of a visit</p>
              <BarList items={data.landing.slice(0, 5).map(d => ({ key: d.key, label: d.key, value: d.count }))} />
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Audience" icon={<Activity size={15} />}>
              <p className="eyebrow">Signed in</p>
              <BarList items={data.plans.map(d => ({ key: d.key, value: d.count, sub: `${share(d.count, pv)}%`,
                label: d.key === "anon" ? "Not signed in" : d.key === "free" ? "Free account" : "Premium" }))} />
              <p className="eyebrow pt-1">Languages</p>
              <BarList items={data.languages.slice(0, 6).map(d => ({ key: d.key, label: d.key, value: d.count }))} />
            </Card>
            <Card title="Live feed" icon={<Radio size={15} />} subtitle="Latest page views">
              {!live?.recent.length ? <p className="text-xs text-n-400">Quiet right now.</p> : (
                <ul className="divide-y divide-n-800 text-xs">
                  {live.recent.slice(0, 12).map((h, i) => (
                    <li key={i} className="flex items-center gap-2 py-1.5">
                      <span>{flag(h.country)}</span>
                      <span className="text-n-200 truncate">{h.city || countryName(h.country)}</span>
                      <span className="text-n-500 truncate">{h.path}</span>
                      <span className="ml-auto text-n-500 shrink-0">{h.device} · {ago(h.at)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
