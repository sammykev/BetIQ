const API_URL = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

export interface Prediction {
  home: string;
  away: string;
  date: string;
  time: string;
  league: string;
  league_name: string;
  flag: string;
  p_home: number;
  p_draw: number;
  p_away: number;
  p_over15: number;
  p_over25: number;
  tip_1x2: string;
  tip_code: string;
  tip_goals: string;
  goals_type: string;
  goals_confidence: number;
  odds_home?: number;
  odds_draw?: number;
  odds_away?: number;
}

export interface League {
  code: string;
  name: string;
  country: string;
  flag: string;
}

export interface PredictionsResponse {
  predictions: Prediction[];
  total: number;
  last_updated: string | null;
}

export async function fetchPredictions(
  league?: string,
  minConfidence?: number
): Promise<PredictionsResponse> {
  const params = new URLSearchParams();
  if (league && league !== "ALL") params.set("league", league);
  if (minConfidence) params.set("min_confidence", minConfidence.toString());
  params.set("limit", "500");

  const res = await fetch(`${API_URL}/api/predictions?${params}`, {
    next: { revalidate: 300 }, // cache 5 min
  });

  if (!res.ok) throw new Error("Failed to fetch predictions");
  return res.json();
}

export async function fetchLeagues(): Promise<League[]> {
  const res = await fetch(`${API_URL}/api/leagues`, { next: { revalidate: 3600 } });
  if (!res.ok) return [];
  return res.json();
}

export async function triggerRefresh(): Promise<void> {
  await fetch(`${API_URL}/api/refresh`, { method: "POST" });
}

export interface MarketOption {
  label: string;
  code: string;
  prob: number;
}

export interface Market {
  id: string;
  name: string;
  options: MarketOption[];
}

export interface EloContext {
  home: number;
  away: number;
  gap: number;
  gap_out_of: number;
  leading: string;
  label: string;
  description: string;
  implied_win_prob: number;
}

export interface TeamForm {
  available: boolean;
  games?: number;
  form?: string;        // "WWDLW"
  wins?: number;
  draws?: number;
  losses?: number;
  goals_scored?: number;
  goals_conceded?: number;
  elo?: number;
  data_source?: string;
}

export interface MatchAnalysis {
  xg_home: number;
  xg_away: number;
  elo: EloContext;
  markets: Market[];
  recommended: MarketOption & { market: string; market_id: string };
  live_odds_fetched?: boolean;
  odds_bookie?: string;
  team_form?: { home: TeamForm; away: TeamForm };
}

export interface H2HMeeting {
  date: string;
  home_team: string;
  away_team: string;
  score: string;
  result: string;
  winner: string | null;
}

export interface H2HSummary {
  total: number;
  home_wins: number;
  draws: number;
  away_wins: number;
  home_goals: number;
  away_goals: number;
  avg_goals: number;
  btts_count: number;
}

export interface H2HData {
  meetings: H2HMeeting[];
  summary: H2HSummary | null;
  source?: "api" | "csv" | "none";
}

export interface HistoryPrediction extends Prediction {
  outcome: "won" | "lost" | "pending";
  actual_result: "H" | "D" | "A" | null;
}

export interface CalendarDay {
  total: number;
  won: number;
  lost: number;
  pending: number;
}

export async function fetchCalendar(month: string): Promise<Record<string, CalendarDay>> {
  const res = await fetch(`${API_URL}/api/calendar?month=${month}`, { cache: "no-store" });
  if (!res.ok) return {};
  return res.json();
}

export async function fetchHistory(date: string): Promise<HistoryPrediction[]> {
  const res = await fetch(`${API_URL}/api/history?date=${date}`, { cache: "no-store" });
  if (!res.ok) return [];
  return res.json();
}

export interface MatchExplanation {
  explanation: string | null;
  sources: string[];
  model: string | null;
  error: string | null;
}

export async function fetchExplanation(home: string, away: string): Promise<MatchExplanation> {
  const params = new URLSearchParams({ home, away });
  const res = await fetch(`${API_URL}/api/explain?${params}`, { cache: "no-store" });
  if (!res.ok) return { explanation: null, sources: [], model: null, error: "fetch_failed" };
  return res.json();
}

export async function fetchMatchAnalysis(home: string, away: string): Promise<MatchAnalysis> {
  const params = new URLSearchParams({ home, away });
  const res = await fetch(`${API_URL}/api/analysis?${params}`, { cache: "no-store" });
  if (!res.ok) throw new Error("Analysis failed");
  return res.json();
}

export async function fetchH2H(home: string, away: string): Promise<H2HData> {
  const params = new URLSearchParams({ home, away });
  const res = await fetch(`${API_URL}/api/h2h?${params}`, { cache: "no-store" });
  if (!res.ok) throw new Error("H2H failed");
  return res.json();
}
