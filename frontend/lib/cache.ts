// The last answer of each list the home page shows, kept in the browser: a
// refresh shows it at once while the fresh one loads (stale-while-revalidate).
// Anything older than MAX_AGE is ignored, and a full store makes room by
// dropping the oldest entries.

const PREFIX = "betiq:c:";
const MAX_AGE = 6 * 3600_000;

interface Entry<T> { at: number; data: T }

/** The kept copy of `key`, if there is a recent one. */
export function peek<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(PREFIX + key);
    if (!raw) return null;
    const e = JSON.parse(raw) as Entry<T>;
    return Date.now() - e.at < MAX_AGE ? e.data : null;
  } catch {
    return null;
  }
}

/** Keep `data` as the latest copy of `key`. */
export function keep<T>(key: string, data: T): void {
  const value = JSON.stringify({ at: Date.now(), data } satisfies Entry<T>);
  try {
    localStorage.setItem(PREFIX + key, value);
  } catch {
    // Full: drop the oldest kept copies (never other site data) and try once more
    try {
      const ours = Object.keys(localStorage).filter(k => k.startsWith(PREFIX))
        .map(k => ({ k, at: (() => { try { return (JSON.parse(localStorage.getItem(k) || "{}") as Entry<unknown>).at || 0; } catch { return 0; } })() }))
        .sort((a, b) => a.at - b.at);
      ours.slice(0, Math.max(1, Math.ceil(ours.length / 2))).forEach(({ k }) => localStorage.removeItem(k));
      localStorage.setItem(PREFIX + key, value);
    } catch { /* storage off or still full: go without */ }
  }
}
