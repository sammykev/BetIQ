"use client";

import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

// Module-level cache shared across every component instance in the browser
// session — a team's logo is looked up at most once per page load no matter
// how many cards render it.
const _cache = new Map<string, string | null>();
const _inFlight = new Map<string, Promise<string | null>>();

// Names asked for within one short window go to the server together
// (/api/team-logos): a page full of badges is one request, not one each
const _queue = new Map<string, (url: string | null) => void>();
let _timer: ReturnType<typeof setTimeout> | null = null;

function flush(): void {
  _timer = null;
  const batch = Array.from(_queue.entries());
  _queue.clear();
  for (let i = 0; i < batch.length; i += 100) {
    const part = batch.slice(i, i + 100);
    fetch(`${API_URL}/api/team-logos?names=${encodeURIComponent(part.map(([n]) => n).join("|"))}`)
      .then(r => (r.ok ? r.json() : { logos: {} }))
      .then(d => { for (const [n, done] of part) done((d?.logos ?? {})[n] ?? null); })
      .catch(() => { for (const [, done] of part) done(null); });
  }
}

async function fetchLogo(name: string): Promise<string | null> {
  if (_cache.has(name)) return _cache.get(name)!;
  if (_inFlight.has(name)) return _inFlight.get(name)!;

  const promise = new Promise<string | null>(resolve => {
    _queue.set(name, resolve);
    if (!_timer) _timer = setTimeout(flush, 30);
  }).finally(() => _inFlight.delete(name));

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
