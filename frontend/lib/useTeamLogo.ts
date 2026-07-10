"use client";

import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

// Module-level cache shared across every component instance in the browser
// session — a team's logo is looked up at most once per page load no matter
// how many cards render it.
const _cache = new Map<string, string | null>();
const _inFlight = new Map<string, Promise<string | null>>();

async function fetchLogo(name: string): Promise<string | null> {
  if (_cache.has(name)) return _cache.get(name)!;
  if (_inFlight.has(name)) return _inFlight.get(name)!;

  const promise = fetch(`${API_URL}/api/team-logo?name=${encodeURIComponent(name)}`)
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
 * Looks up a team's logo via the generic /api/team-logo backend endpoint —
 * covers any team in any league (World Cup national teams not yet in the
 * static flag map, smaller football leagues, EuroLeague/NCAA/NBL basketball,
 * seasonal competitions like NBA Summer League) without a hardcoded
 * per-team/per-league list. Pass `enabled: false` to skip the lookup
 * entirely, e.g. when a fast static match already exists.
 */
export function useTeamLogo(name: string, enabled: boolean): string | null {
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
