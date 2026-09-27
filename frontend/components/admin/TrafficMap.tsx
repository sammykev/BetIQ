"use client";

import { useEffect, useRef, useState } from "react";
import type { Map as LeafletMap, LayerGroup } from "leaflet";
import "leaflet/dist/leaflet.css";
import { Maximize2, Minimize2, LocateFixed } from "lucide-react";
import { countryName, flag, num } from "./ui";
import type { CityPoint } from "./WorldMap";

// Where visitors are, on a real map you can pan and zoom (Leaflet, CARTO
// street tiles). One bubble per city, sized by page views, and a pulsing pin
// for everyone on the site in the last few minutes.
//
// Locations are what Vercel reports for a visitor's connection: city level,
// stored rounded to 0.1° (~11 km), and for mobile networks often the
// operator's gateway city rather than the visitor's. So each bubble carries a
// shaded circle of that uncertainty, and zoom stops at district level:
// closer would suggest a street or house the data can't point to.

const MAX_ZOOM = 12;
const UNCERTAINTY_M = 11_000;
const TILES = {
  light: "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
  dark: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
};
const ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>';

const css = (name: string) => {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v ? `rgb(${v.split(/\s+/).join(",")})` : "#65a30d";
};
const isDark = () => document.documentElement.classList.contains("dark");

export function TrafficMap({ cities, live = [] }: { cities: CityPoint[]; live?: { lat: number | null; lon: number | null }[] }) {
  const box = useRef<HTMLDivElement>(null);
  const holder = useRef<HTMLDivElement>(null);
  const map = useRef<LeafletMap | null>(null);
  const layers = useRef<{ cities: LayerGroup; live: LayerGroup } | null>(null);
  const [full, setFull] = useState(false);
  const [ready, setReady] = useState(false);

  // Create the map once (Leaflet needs the browser, so it's loaded here)
  useEffect(() => {
    let cancelled = false;
    let tiles: import("leaflet").TileLayer | null = null;
    let observer: MutationObserver | null = null;
    (async () => {
      const L = (await import("leaflet")).default;
      if (cancelled || !holder.current || map.current) return;
      const m = L.map(holder.current, {
        center: [15, 10], zoom: 2, minZoom: 2, maxZoom: MAX_ZOOM, worldCopyJump: true,
        zoomSnap: 0.5, attributionControl: true, preferCanvas: true,
      });
      tiles = L.tileLayer(isDark() ? TILES.dark : TILES.light, { attribution: ATTRIBUTION, subdomains: "abcd", maxZoom: MAX_ZOOM }).addTo(m);
      // Follow the admin's light/dark switch
      observer = new MutationObserver(() => tiles?.setUrl(isDark() ? TILES.dark : TILES.light));
      observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
      layers.current = { cities: L.layerGroup().addTo(m), live: L.layerGroup().addTo(m) };
      map.current = m;
      setReady(true);
    })();
    return () => {
      cancelled = true;
      observer?.disconnect();
      map.current?.remove();
      map.current = null;
      layers.current = null;
    };
  }, []);

  // Draw the cities and live pins whenever they change
  useEffect(() => {
    if (!ready || !map.current || !layers.current) return;
    let alive = true;
    (async () => {
      const L = (await import("leaflet")).default;
      if (!alive || !layers.current || !map.current) return;
      const { cities: cl, live: ll } = layers.current;
      cl.clearLayers();
      ll.clearLayers();
      const c1 = css("--chart-1"), c2 = css("--chart-2");
      const pts = cities.filter(c => c.lat !== null && c.lon !== null);
      const max = Math.max(1, ...pts.map(c => c.pageviews));
      for (const c of [...pts].sort((a, b) => b.pageviews - a.pageviews)) {
        const at: [number, number] = [c.lat as number, c.lon as number];
        // How far off the location may be
        L.circle(at, { radius: UNCERTAINTY_M, stroke: false, fillColor: c1, fillOpacity: 0.08, interactive: false }).addTo(cl);
        const place = [c.region, countryName(c.country)].filter(Boolean).join(", ");
        L.circleMarker(at, {
          radius: 5 + Math.sqrt(c.pageviews / max) * 18, color: "#fff", weight: 1.5,
          fillColor: c1, fillOpacity: 0.6,
        }).bindPopup(
          `<b>${flag(c.country)} ${escape(c.city || "Unknown city")}</b><br>${escape(place)}<br>` +
          `${num(c.pageviews)} page views<br><span style="opacity:.65">Approximate: about 10 km, city level</span>`,
        ).bindTooltip(`${escape(c.city || "?")} · ${num(c.pageviews)}`, { direction: "top" }).addTo(cl);
      }
      for (const p of live.filter(p => p.lat !== null && p.lon !== null)) {
        L.marker([p.lat as number, p.lon as number], {
          icon: L.divIcon({ className: "", iconSize: [14, 14],
            html: `<span class="betiq-live-pin" style="--pin:${c2}"></span>` }),
          keyboard: false, interactive: false,
        }).addTo(ll);
      }
    })();
    return () => { alive = false; };
  }, [ready, cities, live]);

  // Leaflet must be told when its box changes size (fullscreen, window resize)
  useEffect(() => {
    if (!map.current || !box.current) return;
    const ro = new ResizeObserver(() => map.current?.invalidateSize());
    ro.observe(box.current);
    return () => ro.disconnect();
  }, [ready]);

  useEffect(() => {
    if (!full) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setFull(false); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [full]);

  const fit = async () => {
    const L = (await import("leaflet")).default;
    const pts = cities.filter(c => c.lat !== null && c.lon !== null).map(c => [c.lat, c.lon] as [number, number]);
    if (!map.current) return;
    if (pts.length) map.current.fitBounds(L.latLngBounds(pts).pad(0.2), { maxZoom: 6 });
    else map.current.setView([15, 10], 2);
  };
  // Start framed on the visitors
  useEffect(() => { if (ready) fit(); }, [ready]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div ref={box} className={full
      ? "fixed inset-0 z-50 bg-surface p-3 flex flex-col"
      : "relative"}>
      <div className={full ? "relative flex-1 min-h-0" : "relative h-[320px] sm:h-[440px]"}>
        <div ref={holder} className="absolute inset-0 rounded-xl overflow-hidden border border-n-800 z-0" />
        <div className="absolute right-2 top-2 z-[500] flex flex-col gap-1.5">
          <button type="button" onClick={() => setFull(f => !f)} aria-label={full ? "Exit full screen" : "Full screen"}
            className="h-8 w-8 rounded-lg bg-surface/90 border border-n-700 text-n-200 flex items-center justify-center shadow-card hover:text-n-0">
            {full ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
          </button>
          <button type="button" onClick={fit} aria-label="Show all visitors"
            className="h-8 w-8 rounded-lg bg-surface/90 border border-n-700 text-n-200 flex items-center justify-center shadow-card hover:text-n-0">
            <LocateFixed size={15} />
          </button>
        </div>
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-n-400 mt-2">
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: "rgb(var(--chart-1))", opacity: 0.7 }} />
          City, bigger = more page views
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: "rgb(var(--chart-2))" }} />
          On the site now
        </span>
        <span>Locations are approximate (city level, about 10 km); mobile networks often show their gateway city.</span>
      </div>
      <style>{`
        .betiq-live-pin { display:block; width:14px; height:14px; border-radius:9999px; background:var(--pin);
          border:2px solid #fff; box-shadow:0 0 0 0 var(--pin); animation:betiq-pulse 1.8s infinite; }
        @keyframes betiq-pulse { 0% { box-shadow:0 0 0 0 var(--pin); } 100% { box-shadow:0 0 0 14px transparent; } }
        .leaflet-container { font: inherit; background: rgb(var(--surface-sunken, var(--surface))); }
      `}</style>
    </div>
  );
}

function escape(s: string): string {
  return s.replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch] as string));
}
