// Match days (backend matchday.py): each match's pre-match prediction next
// to its score, and how every market's pick did.

export const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

export type MatchStatus = "scheduled" | "live" | "finished" | "postponed";
export type Verdict = "won" | "lost" | "push" | "half_won" | "half_lost";

export interface Grade { pick: string; prob: number | null; verdict: Verdict }

export interface MatchdayPred {
  p_home?: number; p_draw?: number; p_away?: number;
  p_over15?: number; p_over25?: number; p_over35?: number; p_btts?: number;
  tip_1x2?: string; tip_code?: string; tip_confidence?: number | null;
  tip_goals?: string; goals_confidence?: number;
  odds_home?: number | null; odds_draw?: number | null; odds_away?: number | null;
  is_value_bet?: boolean;
  corners_mean?: number; corners_over?: number; bookings_mean?: number; bookings_over?: number;
  referee?: string;
}

export interface MatchdayMatch {
  key: string; home: string; away: string; date: string; time: string;
  league: string; league_name: string; flag: string;
  status: MatchStatus; minute: string | null; aet: boolean;
  score: [number, number] | null; corners: [number, number] | null; bookings: [number, number] | null;
  shots?: [number, number] | null; sot?: [number, number] | null;
  /** Live and full-time team stats (possession, shots, sot, corners, fouls, ...: [home, away]). */
  stats?: Record<string, [number, number]> | null;
  /** Goals and cards in match order. */
  events?: MatchEvent[] | null;
  pred: MatchdayPred; grades: Record<string, Grade> | null; locked: boolean;
}

export interface MatchEvent {
  minute: string; side: "home" | "away"; player: string | null;
  kind: "goal" | "penalty_goal" | "own_goal" | "yellow" | "red";
}

export interface DaySummary {
  total: number; finished: number; live: number;
  tip: [number, number]; goals: [number, number]; favourite: [number, number];
}

export interface MatchdayResponse { date: string; today: string; matches: MatchdayMatch[]; summary: DaySummary; updated: string | null }
export interface StripDay extends DaySummary { date: string }
export interface StripResponse { today: string; days: StripDay[] }

export const MARKET_LABELS: Record<string, string> = {
  tip: "Our tip", goals: "Goals tip", favourite: "Most likely result",
  ou25: "Over/under 2.5", btts: "Both teams score", corners: "Corners 9.5", bookings: "Bookings 4.5",
};

/** The match-day store's key for a fixture (backend matchday.key). */
export const matchKey = (home: string, away: string) => `${home.trim().toLowerCase()}|${away.trim().toLowerCase()}`;

export const won = (v?: Verdict) => v === "won" || v === "half_won";
export const lost = (v?: Verdict) => v === "lost" || v === "half_lost";

export async function fetchStrip(signal?: AbortSignal): Promise<StripResponse> {
  const r = await fetch(`${API}/api/matchday/strip`, { signal });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function fetchMatchday(date: string, signal?: AbortSignal): Promise<MatchdayResponse> {
  const r = await fetch(`${API}/api/matchday?date=${encodeURIComponent(date)}`, { signal });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

// ── Track record ──
export interface MarketStat { n: number; hit_rate: number | null; avg_prob: number | null; name: string }
export interface Accuracy {
  days: number; matches: number;
  markets: Record<string, MarketStat>;
  calibration: { from: number; to: number; n: number; said: number; happened: number }[];
  brier: { model: number | null; matches: number;
           vs_bookmaker: { model: number; bookmaker: number; matches: number } | null };
  leagues: { league: string; name: string; flag: string; n: number; hits: number; hit_rate: number }[];
  daily: { date: string; n: number; favourite_hit: number }[];
}

export async function fetchAccuracy(days: number, signal?: AbortSignal): Promise<Accuracy> {
  const r = await fetch(`${API}/api/accuracy?days=${days}`, { signal });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

// ── Tickets (booking codes) ──
export type LegStatus = "won" | "lost" | "void" | "pending" | "unknown";
export interface TicketLeg {
  home: string; away: string; date: string; time?: string; market: string; marketName?: string;
  code: string; label?: string; prob?: number | null; odds?: number | null; status: LegStatus;
}
export interface Ticket {
  code: string; created_at: string; source: string; share_url: string | null;
  legs: TicketLeg[]; total_odds: number | null; status: "pending" | "open" | "won" | "lost" | "void";
  settled_at: string | null;
}
export interface TicketSummary {
  tickets: number; won: number; lost: number; pending: number; hit_rate: number | null;
  legs_won: number; legs_lost: number; leg_hit_rate: number | null;
}
