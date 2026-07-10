"use client";

import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

// Module-level cache shared across every component instance in the browser
// session — a competition's badge is looked up at most once per page load.
// Keyed by "sport:name" since the same short name could theoretically exist
// across sports (unlikely today, but cheap to keep correct).
const _cache = new Map<string, string | null>();
const _inFlight = new Map<string, Promise<string | null>>();

async function fetchLogo(name: string, sport: string): Promise<string | null> {
  const key = `${sport}:${name}`;
  if (_cache.has(key)) return _cache.get(key)!;
  if (_inFlight.has(key)) return _inFlight.get(key)!;

  const params = new URLSearchParams({ name, sport });
  const promise = fetch(`${API_URL}/api/competition-logo?${params}`)
    .then(r => (r.ok ? r.json() : { logo: null }))
    .then(d => (d.logo as string | null) ?? null)
    .catch(() => null)
    .finally(() => _inFlight.delete(key));

  _inFlight.set(key, promise);
  const result = await promise;
  _cache.set(key, result);
  return result;
}

/**
 * Looks up a competition/league badge (World Cup, Premier League, EuroLeague,
 * ...) via the generic /api/competition-logo backend endpoint — no
 * hardcoded per-competition list. `sport` is TheSportsDB's taxonomy (e.g.
 * "Soccer", "Basketball") — defaults to "Soccer". Pass `enabled: false` to
 * skip the lookup.
 */
export function useCompetitionLogo(name: string, enabled: boolean = true, sport: string = "Soccer"): string | null {
  const key = `${sport}:${name}`;
  const [logo, setLogo] = useState<string | null>(() => (enabled ? _cache.get(key) ?? null : null));

  useEffect(() => {
    if (!enabled || !name) {
      setLogo(null);
      return;
    }
    if (_cache.has(key)) {
      setLogo(_cache.get(key)!);
      return;
    }
    let cancelled = false;
    fetchLogo(name, sport).then(url => {
      if (!cancelled) setLogo(url);
    });
    return () => { cancelled = true; };
  }, [name, enabled, sport, key]);

  return logo;
}
