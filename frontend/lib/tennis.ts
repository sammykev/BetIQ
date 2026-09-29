// Tennis match facts (backend tennis_facts.py): each player's last 5, the
// head-to-head, and his numbers over recent matches, from SportyBet's results.

export interface TFormRow {
  date: string; opponent: string; outcome: "W" | "L"; sets: [number, number]; score: string;
  retired: boolean; tournament: string; surface: string;
}
export interface TMeeting {
  date: string; home: string; away: string; sets: [number, number]; score: string;
  retired: boolean; tournament: string; surface: string;
}
export interface TAverages {
  played: number; won: number;
  sets: { for: number; against: number };
  straight_sets_wins: number;
  deciding_set: { rate: number; won: number | null };
  games: { for: number | null; against: number | null; total: number | null; matches: number };
  tiebreak_matches: number | null;
  surface: { name: string; played: number; won: number | null } | null;
}
export interface TFacts {
  home: TFormRow[]; away: TFormRow[]; h2h: TMeeting[]; surface: string;
  summary: { h2h: { won: number; lost: number } | null };
  averages: { n: number; home: TAverages | null; away: TAverages | null };
  results_days?: number | null;
}

const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

export async function fetchTennisFacts(f: (url: string, init?: RequestInit) => Promise<Response>,
  m: { home: string; away: string; date: string; time?: string; league?: string }, signal?: AbortSignal): Promise<TFacts> {
  const q = new URLSearchParams({ home: m.home, away: m.away, date: m.date,
    ...(m.time && /^\d{2}:\d{2}$/.test(m.time) ? { time: m.time } : {}), ...(m.league ? { league: m.league } : {}) });
  const r = await f(`${API}/api/tennis/facts?${q}`, { signal });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}
