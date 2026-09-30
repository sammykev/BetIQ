import type { SlipSelection } from "./slip";
import type { StatRow } from "./matchday";

// Basketball predictions (backend basketball_predictions.py): SportyBet's
// listed matches, our expected points, and every line it offers priced by
// our model, each with SportyBet's own ids (so it can always be booked).

export interface BBLine {
  market: string;         // bb_total, bb_q1_handicap, …
  family: string;         // bb_winner | bb_1x2 | bb_handicap | bb_total | bb_team_total | bb_halves | bb_quarters | bb_overtime
  market_name: string;
  code: string;
  label: string;          // "Lakers -5.5", "Over 160.5 points", "1st quarter: Over 40.5 points"
  prob: number;           // our chance
  odds: number;           // SportyBet's price
  edge: number;           // prob × odds − 1
  sb: { eventId: string; marketId: string; specifier: string; outcomeId: string };
  /** Player props: his expected count tonight, minutes and games behind it */
  player?: string; expected?: number; minutes?: number; games?: number;
}

export interface BasketballPrediction {
  sport: "basketball";
  home: string; away: string; date: string; time: string;
  league: string; league_name: string; country?: string; flag: string;
  home_logo?: string | null; away_logo?: string | null;
  sportybet_event_id: string;
  p_home: number; p_away: number;
  tip_1x2: string; tip_code: string; tip_confidence: number; goals_confidence: number;
  tip_goals?: string; total_line?: number | null; handicap_line?: number | null;
  odds_home?: number | null; odds_away?: number | null;
  exp_home_pts: number; exp_away_pts: number;
  model: "model" | "market" | "blend";
  rated: boolean;
  top_lines?: BBLine[];   // the list: its likeliest lines
  lines?: number;         // how many lines were priced
  bb_markets?: BBLine[];  // one match (/api/basketball/match): every line
  /** Each team's last 5, oldest to newest ("WWLWL") */
  home_form?: string; away_form?: string;
}

export const FAMILIES: { id: string; name: string }[] = [
  { id: "bb_winner", name: "Winner" },
  { id: "bb_player", name: "Player props" },
  { id: "bb_handicap", name: "Handicap" },
  { id: "bb_total", name: "Total points" },
  { id: "bb_team_total", name: "Team points" },
  { id: "bb_halves", name: "Halves" },
  { id: "bb_quarters", name: "Quarters" },
  { id: "bb_1x2", name: "1X2 (regulation)" },
  { id: "bb_overtime", name: "Overtime" },
];

export const MODEL_NOTE: Record<BasketballPrediction["model"], string> = {
  blend: "Our team ratings, blended with SportyBet's lines",
  model: "Our team ratings",
  market: "SportyBet's lines (we don't rate this league's teams yet)",
};

export function lineSelection(p: Pick<BasketballPrediction, "home" | "away" | "date" | "time" | "league_name">,
                              l: BBLine): SlipSelection {
  return { home: p.home, away: p.away, date: p.date, time: p.time, league: p.league_name,
           market: l.market, marketName: l.market_name, code: l.code, label: l.label, prob: l.prob, sb: l.sb };
}

// ── Match days (backend basketball_matchday.py): the date strip, live, history ──
export interface BBGrade { pick: string; prob: number | null; odds?: number | null; verdict: "won" | "lost" }
export interface BBMatchdayMatch {
  key: string; id: string; home: string; away: string; date: string; time: string;
  league: string; league_name: string; flag: string; home_logo?: string | null; away_logo?: string | null;
  status: "scheduled" | "live" | "finished" | "postponed"; minute: string | null;
  /** Went to overtime */
  aet: boolean;
  score: [number, number] | null;
  /** Quarter scores (regulation) */
  periods: [number, number][] | null;
  pred: {
    p_home?: number; p_away?: number; tip_1x2?: string; tip_code?: string; tip_confidence?: number | null;
    tip_goals?: string; goals_confidence?: number | null; total_line?: number | null; handicap_line?: number | null;
    exp_home_pts?: number; exp_away_pts?: number; model?: BasketballPrediction["model"];
  };
  best: { market: string; market_name: string; code: string; label: string; prob: number; odds: number } | null;
  grades: Partial<Record<"tip" | "points" | "best", BBGrade>> | null;
  locked: boolean;
  /** Live (then full-match) stats from Sportradar, when it has them */
  live_stats?: StatRow[] | null;
}
export interface BBDaySummary { total: number; finished: number; live: number;
  tip: [number, number]; points: [number, number]; best: [number, number] }
export interface BBMatchdayResponse { date: string; today: string; matches: BBMatchdayMatch[]; summary: BBDaySummary; updated: string | null }
export interface BBStripDay extends BBDaySummary { date: string }
export interface BBStripResponse { today: string; days: BBStripDay[] }

export const BB_GRADE_LABELS: Record<string, string> = { tip: "Our tip", points: "Total points", best: "Best line" };

type Fetcher = (url: string, init?: RequestInit) => Promise<Response>;
const API_URL = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

export async function fetchBBStrip(f: Fetcher, signal?: AbortSignal): Promise<BBStripResponse> {
  const r = await f(`${API_URL}/api/basketball/matchday/strip`, { signal });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function fetchBBMatchday(f: Fetcher, date: string, signal?: AbortSignal): Promise<BBMatchdayResponse> {
  const r = await f(`${API_URL}/api/basketball/matchday?date=${encodeURIComponent(date)}`, { signal });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

// ── Match facts (backend basketball_facts.py): form, head to head, averages ──
export interface BBFormRow { date: string; opponent: string; venue: "H" | "A"; for: number; against: number;
  outcome: "W" | "L"; ot: boolean; comp: string }
export interface BBMeeting { date: string; home: string; away: string; hs: number; as: number; ot: boolean; comp: string }
export interface BBAverages {
  played: number;
  points: { for: number | null; against: number | null; total: number | null; margin: number | null };
  results: { won: number; lost: number };
  overtime: number;
  home_points: number | null; away_points: number | null;
  halves: { first_for: number | null; first_against: number | null; second_for: number | null; second_against: number | null; matches: number };
  quarters: { for: (number | null)[]; against: (number | null)[]; matches: number };
  over_line: { line: number; rate: number } | null;
  cover: { line: number; rate: number } | null;
}
export interface BBFacts {
  home: BBFormRow[]; away: BBFormRow[]; h2h: BBMeeting[];
  summary: { h2h: { won: number; lost: number; avg_total: number; avg_margin: number } | null };
  averages: { n: number; home: BBAverages | null; away: BBAverages | null };
}

export async function fetchBBFacts(f: Fetcher, event: string, signal?: AbortSignal): Promise<BBFacts> {
  const r = await f(`${API_URL}/api/basketball/facts?event=${encodeURIComponent(event)}`, { signal });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}
