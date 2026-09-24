"use client";

import { useMemo, useState } from "react";
import { geoNaturalEarth1, geoPath } from "d3-geo";
import { feature } from "topojson-client";
import type { Topology, GeometryCollection } from "topojson-specification";
import world from "world-atlas/countries-110m.json";
import { countryName, flag, num } from "./ui";

// Where visitors are: one bubble per city (area ∝ page views) on a Natural
// Earth map, plus a pulsing pin for everyone on the site in the last few
// minutes. Hover a bubble for the city and its numbers.

export interface CityPoint { city: string; region: string; country: string; lat: number | null; lon: number | null; pageviews: number }

const W = 960;
const H = 500;

export function WorldMap({ cities, live = [] }: { cities: CityPoint[]; live?: { lat: number | null; lon: number | null }[] }) {
  const [hover, setHover] = useState<(CityPoint & { x: number; y: number }) | null>(null);

  const { projection, countries } = useMemo(() => {
    const topo = world as unknown as Topology<{ countries: GeometryCollection }>;
    const geo = feature(topo, topo.objects.countries);
    const projection = geoNaturalEarth1().fitExtent([[4, 4], [W - 4, H - 4]], geo);
    const path = geoPath(projection);
    return { projection, countries: geo.features.map(f => path(f) ?? "").filter(Boolean) };
  }, []);

  const points = useMemo(() => {
    const max = Math.max(1, ...cities.map(c => c.pageviews));
    return cities
      .filter(c => c.lat !== null && c.lon !== null)
      .map(c => {
        const [x, y] = projection([c.lon as number, c.lat as number]) ?? [0, 0];
        return { ...c, x, y, r: 3 + Math.sqrt(c.pageviews / max) * 16 };
      })
      .sort((a, b) => b.r - a.r);  // small bubbles drawn last, on top
  }, [cities, projection]);

  const pins = useMemo(() => live
    .filter(p => p.lat !== null && p.lon !== null)
    .map(p => projection([p.lon as number, p.lat as number]) ?? [0, 0]), [live, projection]);

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" role="img" aria-label="Map of visitors by city"
        onPointerLeave={() => setHover(null)}>
        <g>
          {countries.map((d, i) => (
            <path key={i} d={d} fill="rgb(var(--n-800))" stroke="rgb(var(--surface))" strokeWidth="0.6" />
          ))}
        </g>
        <g>
          {points.map(p => (
            <circle key={`${p.city}|${p.country}|${p.lat}|${p.lon}`} cx={p.x} cy={p.y} r={p.r}
              fill="rgb(var(--chart-1))" fillOpacity={hover && hover.city === p.city ? 0.9 : 0.55}
              stroke="rgb(var(--surface))" strokeWidth="1.5"
              onPointerEnter={() => setHover(p)} />
          ))}
        </g>
        <g aria-hidden="true">
          {pins.map(([x, y], i) => (
            <g key={i}>
              <circle cx={x} cy={y} r="4" fill="rgb(var(--chart-2))" stroke="rgb(var(--surface))" strokeWidth="1.5" />
              <circle cx={x} cy={y} r="4" fill="none" stroke="rgb(var(--chart-2))" strokeWidth="2">
                <animate attributeName="r" from="4" to="16" dur="1.8s" repeatCount="indefinite" />
                <animate attributeName="opacity" from="0.8" to="0" dur="1.8s" repeatCount="indefinite" />
              </circle>
            </g>
          ))}
        </g>
      </svg>
      {hover && (
        <div className="pointer-events-none absolute rounded-lg border border-n-800 bg-surface-raised px-2.5 py-1.5 text-[11px] shadow-card"
          style={{ left: `${(hover.x / W) * 100}%`, top: `${(hover.y / H) * 100}%`,
                   transform: `translate(${hover.x > W / 2 ? "-110%" : "10%"}, -50%)` }}>
          <p className="text-n-0 font-semibold">{flag(hover.country)} {hover.city || "Unknown city"}</p>
          <p className="text-n-400">{[hover.region, countryName(hover.country)].filter(Boolean).join(", ")}</p>
          <p className="text-n-0 tnum">{num(hover.pageviews)} page views</p>
        </div>
      )}
      <div className="flex flex-wrap gap-4 text-[11px] text-n-400 mt-1">
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: "rgb(var(--chart-1))", opacity: 0.7 }} />
          City — bigger = more page views
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: "rgb(var(--chart-2))" }} />
          On the site now
        </span>
      </div>
    </div>
  );
}
