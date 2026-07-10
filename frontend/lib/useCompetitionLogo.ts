"use client";

import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

// Module-level cache shared across every component instance in the browser
// session — a competition's badge is looked up at most once per page load.
const _cache = new Map<string, string | null>();
const _inFlight = new Map<string, Promise<string | null>>();

async function fetchLogo(name: string): Promise<string | null> {
  if (_cache.has(name)) return _cache.get(name)!;
  if (_inFlight.has(name)) return _inFlight.get(name)!;

  const promise = fetch(`${API_URL}/api/competition-logo?name=${encodeURIComponent(name)}`)
    .then(r => (r.ok ? r.json() : { logo: null }))
    .then(d => (d.logo as string | null) ?? null)
    .catch(() => null)
    .finally(() => _inFlight.delete(name));

  _inFlight.set(name, promise);
  const result = await promise;
  _cache.set(name, result);
  return result;
}

/**
 * Looks up a competition/league badge (World Cup, Premier League, EuroLeague,
 * ...) via the generic /api/competition-logo backend endpoint — no
 * hardcoded per-competition list. Pass `enabled: false` to skip the lookup.
 */
export function useCompetitionLogo(name: string, enabled: boolean = true): string | null {
  const [logo, setLogo] = useState<string | null>(() => (enabled ? _cache.get(name) ?? null : null));

  useEffect(() => {
    if (!enabled || !name) {
      setLogo(null);
      return;
    }
    if (_cache.has(name)) {
      setLogo(_cache.get(name)!);
      return;
    }
    let cancelled = false;
    fetchLogo(name).then(url => {
      if (!cancelled) setLogo(url);
    });
    return () => { cancelled = true; };
  }, [name, enabled]);

  return logo;
}
