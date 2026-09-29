import type { SlipSelection } from "./slip";
import type { RacketSport } from "./tennis";

// Tennis and table tennis predictions (backend racket_predictions.py):
// SportyBet's listed matches, and every line it offers priced by our model
// (fitted ratings blended with SportyBet's winner prices), each with its
// SportyBet ids (so it can always be booked). One shape for both sports:
// in table tennis a "set" is a game and a "game" is a point.

export interface RKLine {
  market: string;         // rk_winner, rk_total_games, rk_s1_winner, …
  family: string;         // rk_winner | rk_set_handicap | rk_games_handicap | rk_total_games | rk_total_sets | …
  market_name: string;
  code: string;
  label: string;          // "Sinner -1.5 sets", "Over 22.5 games", "1st set: Alcaraz"
  prob: number;
  odds: number;
  edge: number;
  sb: { eventId: string; marketId: string; specifier: string; outcomeId: string };
}

export interface RacketPrediction {
  sport: RacketSport;
  home: string; away: string; date: string; time: string;
  league: string; league_name: string; flag: string; surface?: string;
  sportybet_event_id: string;
  p_home: number; p_away: number;
  tip_1x2: string; tip_code: string; tip_confidence: number; goals_confidence: number;
  tip_goals?: string; total_line?: number | null; p_over_line?: number | null;
  odds_home?: number | null; odds_away?: number | null;
  exp_games?: number;
  set_scores?: Record<string, number>;
  model: "ratings+market" | "market";
  rated: boolean;
  top_lines?: RKLine[];
  lines?: number;
  rk_markets?: RKLine[];  // one match (/api/{sport}/match): every line
  home_form?: string; away_form?: string;
}

export const RK_FAMILIES: Record<RacketSport, { id: string; name: string }[]> = {
  tennis: [
    { id: "rk_winner", name: "Winner" },
    { id: "rk_set_handicap", name: "Set handicap" },
    { id: "rk_games_handicap", name: "Games handicap" },
    { id: "rk_total_games", name: "Total games" },
    { id: "rk_total_sets", name: "Total sets" },
    { id: "rk_to_win_set", name: "To win a set" },
    { id: "rk_player_games", name: "Player games" },
    { id: "rk_sets", name: "Set markets" },
    { id: "rk_correct_score", name: "Correct score" },
    { id: "rk_odd_even", name: "Odd/even games" },
  ],
  table_tennis: [
    { id: "rk_winner", name: "Winner" },
    { id: "rk_set_handicap", name: "Games handicap" },
    { id: "rk_games_handicap", name: "Points handicap" },
    { id: "rk_total_games", name: "Total points" },
    { id: "rk_total_sets", name: "Total games" },
    { id: "rk_to_win_set", name: "To win a game" },
    { id: "rk_sets", name: "Game markets" },
    { id: "rk_correct_score", name: "Correct score" },
    { id: "rk_odd_even", name: "Odd/even points" },
  ],
};

export const RK_WORDS: Record<RacketSport, { set: string; sets: string; game: string; games: string; emoji: string; name: string }> = {
  tennis: { set: "set", sets: "sets", game: "game", games: "games", emoji: "🎾", name: "tennis" },
  table_tennis: { set: "game", sets: "games", game: "point", games: "points", emoji: "🏓", name: "table tennis" },
};

export const RK_MODEL_NOTE: Record<RacketPrediction["model"], string> = {
  "ratings+market": "Our player ratings (years of results), blended with SportyBet's prices",
  market: "SportyBet's prices (we haven't rated both players yet)",
};

export function rkSelection(p: Pick<RacketPrediction, "home" | "away" | "date" | "time" | "league_name">,
                            l: RKLine): SlipSelection {
  return { home: p.home, away: p.away, date: p.date, time: p.time, league: p.league_name,
           market: l.market, marketName: l.market_name, code: l.code, label: l.label, prob: l.prob, sb: l.sb };
}

// ── Match days (backend racket_matchday.py): the date strip, live, history ──
export interface RKGrade { pick: string; prob: number | null; odds?: number | null; verdict: "won" | "lost" }
export interface RKMatchdayMatch {
  key: string; id: string; home: string; away: string; date: string; time: string;
  league: string; league_name: string; flag: string; surface?: string | null;
  status: "scheduled" | "live" | "finished" | "postponed"; minute: string | null;
  /** A player retired */
  ret: boolean;
  /** Sets won (table tennis: games) */
  score: [number, number] | null;
  /** Each set's games (table tennis: each game's points) */
  periods: [number, number][] | null;
  pred: {
    p_home?: number; p_away?: number; tip_1x2?: string; tip_code?: string; tip_confidence?: number | null;
    tip_goals?: string; goals_confidence?: number | null; total_line?: number | null; model?: RacketPrediction["model"];
  };
  best: { market: string; market_name: string; code: string; label: string; prob: number; odds: number } | null;
  grades: Partial<Record<"tip" | "games" | "best", RKGrade>> | null;
  locked: boolean;
  home_form?: string; away_form?: string;
}
export interface RKDaySummary { total: number; finished: number; live: number;
  tip: [number, number]; games: [number, number]; best: [number, number] }
export interface RKMatchdayResponse { date: string; today: string; matches: RKMatchdayMatch[]; summary: RKDaySummary; updated: string | null }
export interface RKStripDay extends RKDaySummary { date: string }
export interface RKStripResponse { today: string; days: RKStripDay[] }

type Fetcher = (url: string, init?: RequestInit) => Promise<Response>;
const API_URL = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";
export const rkPath = (sport: RacketSport) => (sport === "table_tennis" ? "table-tennis" : "tennis");

export async function fetchRKStrip(f: Fetcher, sport: RacketSport, signal?: AbortSignal): Promise<RKStripResponse> {
  const r = await f(`${API_URL}/api/${rkPath(sport)}/matchday/strip`, { signal });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function fetchRKMatchday(f: Fetcher, sport: RacketSport, date: string, signal?: AbortSignal): Promise<RKMatchdayResponse> {
  const r = await f(`${API_URL}/api/${rkPath(sport)}/matchday?date=${encodeURIComponent(date)}`, { signal });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function fetchRKMatch(f: Fetcher, sport: RacketSport, event: string, signal?: AbortSignal): Promise<RacketPrediction> {
  const r = await f(`${API_URL}/api/${rkPath(sport)}/match?event=${encodeURIComponent(event)}`, { signal });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}
