// Backend response shapes (the website's frontend/lib/matchday.ts and lib/api.ts).

export type MatchStatus = "scheduled" | "live" | "finished" | "postponed";
export type Verdict = "won" | "lost" | "push" | "half_won" | "half_lost";

export interface Grade { pick: string; prob: number | null; verdict: Verdict }

export interface MatchdayPred {
  p_home?: number; p_draw?: number; p_away?: number;
  p_over15?: number; p_over25?: number; p_over35?: number; p_btts?: number;
  tip_1x2?: string; tip_code?: string; tip_confidence?: number | null;
  tip_goals?: string; goals_confidence?: number;
  odds_home?: number | null; odds_draw?: number | null; odds_away?: number | null;
  corners_mean?: number; corners_over?: number; bookings_mean?: number; bookings_over?: number;
  referee?: string;
}

export interface MatchdayMatch {
  key: string; home: string; away: string; date: string; time: string;
  league: string; league_name: string; flag: string;
  status: MatchStatus; minute: string | null; aet: boolean;
  score: [number, number] | null;
  stats?: Record<string, [number, number]> | null;
  pred: MatchdayPred; grades: Record<string, Grade> | null; locked: boolean;
}

export interface DaySummary { total: number; finished: number; live: number; tip: [number, number] }
export interface MatchdayResponse { date: string; today: string; matches: MatchdayMatch[]; summary: DaySummary }
export interface StripResponse { today: string; days: (DaySummary & { date: string })[] }

export interface FactMatch {
  date: string; home: string; away: string; hg: number; ag: number; comp: string | null;
  venue: "H" | "A"; opponent: string; outcome: "W" | "D" | "L";
}
export interface StatAverage { for: number | null; against: number | null; total: number | null; matches: number }
export interface TeamAverages {
  played: number;
  goals: { for: number | null; against: number | null; total: number | null };
  over: Record<"0.5" | "1.5" | "2.5" | "3.5", number>;
  btts: number; clean_sheet: number; failed_to_score: number;
  results: { won: number; drawn: number; lost: number };
  corners: StatAverage | null; bookings: StatAverage | null; shots: StatAverage | null; sot: StatAverage | null;
}
export interface MatchFacts {
  home: FactMatch[]; away: FactMatch[]; h2h: FactMatch[];
  averages?: { home: TeamAverages | null; away: TeamAverages | null; n: number };
}

export type LegStatus = "won" | "lost" | "void" | "pending" | "unknown";
export interface Pick {
  home: string; away: string; date: string; time?: string; league?: string;
  market: string; market_name?: string; code: string; label?: string;
  prob: number; odds: number; status: LegStatus;
  live?: { score: [number, number] | null; minute: string | null; status: string } | null;
}
export interface Slip {
  target: number; status: "pending" | "won" | "lost" | "void" | "none"; error?: string;
  total_odds?: number; win_chance?: number; games?: number; picks: Pick[];
  bookable?: boolean; within_target?: boolean; days?: number;
  booking?: { code: string | null; share_url: string | null; booked: number; of: number; error?: string | null } | null;
}
export interface DailyResponse {
  date: string; today: string; slips: Slip[]; record: Record<string, { won: number; lost: number }>;
  min_prob: number; publish_at_utc?: string; retry_minutes?: number;
}

export interface TicketLeg {
  home: string; away: string; date: string; time?: string; marketName?: string; label?: string; code: string;
  odds?: number | null; status: LegStatus;
  live?: { score: [number, number] | null; minute: string | null; status: string; as_it_stands?: "won" | "lost" | null };
}
export interface Ticket {
  code: string; created_at: string; source: string; share_url: string | null;
  legs: TicketLeg[]; total_odds: number | null; status: "pending" | "open" | "won" | "lost" | "void";
}
export interface TicketsResponse {
  tickets: Ticket[];
  summary: { tickets: number; won: number; lost: number; pending: number; hit_rate: number | null };
  older?: { code: string; created_at?: string; share_url?: string | null }[];
}
