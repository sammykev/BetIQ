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
  /** Probability of the goals tip (tip_goals), not of tip_1x2. */
  goals_confidence: number;
  /** Probability of the 1X2 / double-chance tip. Newer API only — use pickProbability(). */
  tip_confidence?: number | null;
  odds_home?: number;
  odds_draw?: number;
  odds_away?: number;
  value_edge?: number | null;      // model_prob - implied_prob (positive = value)
  is_value_bet?: boolean;          // true when value_edge > 0.05
  /** Appointed referee (SofaScore), once named. cards_factor > 1: more cards than these teams usually get. */
  referee?: { name: string; games?: number; cards_factor?: number };
  /** A club has fewer than 5 matches in the model's data: left out of slips. */
  thin_history?: boolean;
  /** The clubs' crests as the fixture feed gives them */
  home_crest?: string | null;
  away_crest?: string | null;
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
  xg_for?: number | null;
  xg_against?: number | null;
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
  web_confidence_modifier?: number;
  web_adjustment_flags?: string[];
  web_adjustment_reason?: string;
  /** When this (shared, per-match) analysis was built; `refreshing`: a newer one is on its way. */
  updated_at?: string | null;
  refreshing?: boolean;
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

export type GoalsOutcome = "won" | "lost" | "push" | "half_won" | "half_lost";

export interface HistoryPrediction extends Prediction {
  /** The 1X2 / double-chance tip. "void": result known but there was no such tip. */
  outcome: "won" | "lost" | "pending" | "void";
  actual_result: "H" | "D" | "A" | null;
  /** The goals tip; null until the score is known or when there was no goals tip. */
  goals_outcome?: GoalsOutcome | null;
  score?: string;
}

export interface CalendarDay {
  total: number;
  won: number;
  lost: number;
  pending: number;
  goals_won?: number;
  goals_lost?: number;
}

export async function fetchCalendar(month: string): Promise<Record<string, CalendarDay>> {
  const res = await fetch(`${API_URL}/api/calendar?month=${month}`, { cache: "no-store" });
  if (!res.ok) return {};
  return res.json();
}

// ── Backtest (backend/backtest.py → /api/admin/model-metrics, admin only) ──────────────────────

export interface ProbScores { log_loss: number | null; brier: number | null; accuracy?: number | null }
export interface PickTierRow { kind: "single" | "double"; tier: "strong" | "lean" | "weak" | "all"; n: number; won: number; hit_rate: number | null; avg_prob: number | null }
export interface GoalsTipRow { tip?: string; tier?: string; n: number; won: number; lost: number; push: number; hit_rate: number | null; avg_prob: number | null }
export interface CalibrationBucket { from: number; to: number; n: number; predicted: number; actual: number }
export interface FlatStake { bets: number; won: number; profit: number; roi: number | null }

export interface ModelMetrics {
  available: true;
  generated_at: string;
  period: { from: string; to: string } | null;
  matches: number;
  leagues: string[];
  method: string;
  match_result: { n: number; model: ProbScores; market: ProbScores };
  over25: { n: number; model: ProbScores; market: ProbScores };
  calibration: CalibrationBucket[];
  picks: { by_tier: PickTierRow[]; by_code: (Omit<PickTierRow, "kind" | "tier"> & { code: string })[] };
  goals_tips: { by_tip: GoalsTipRow[]; by_tier: GoalsTipRow[]; all: GoalsTipRow };
  betting: { all_straight_tips: FlatStake; value_bets: FlatStake; edge_threshold: number };
  /** Same matches predicted without odds (fixtures the odds feed missed). */
  without_odds?: { match_result: { model: ProbScores; market: ProbScores }; straight?: PickTierRow; double?: PickTierRow };
}

/** `adminFetch` adds the admin credentials (see app/betiq-hq). */
export async function fetchModelMetrics(adminFetch: (url: string) => Promise<Response>): Promise<ModelMetrics | null> {
  try {
    const res = await adminFetch(`${API_URL}/api/admin/model-metrics`);
    if (!res.ok) return null;
    const data = await res.json();
    return data && data.available === true && Array.isArray(data.calibration) ? data : null;
  } catch {
    return null;
  }
}

export async function fetchHistory(date: string): Promise<HistoryPrediction[]> {
  const res = await fetch(`${API_URL}/api/history?date=${date}`, { cache: "no-store" });
  if (!res.ok) return [];
  return res.json();
}

export interface NewsItem { date: string; text: string; }

export interface MatchExplanation {
  explanation: string | null;
  sources: string[];
  model: string | null;
  error: string | null;
  /** Team news published in the last week, newest first. */
  news?: NewsItem[];
  news_checked_at?: string | null;
  updated_at?: string | null;
  refreshing?: boolean;
}

/** One result in a team's recent form or the head-to-head (outcome is the team's / the home side's). */
export interface FactMatch {
  date: string; home: string; away: string; hg: number; ag: number; comp: string | null;
  venue: "H" | "A"; opponent: string; outcome: "W" | "D" | "L";
}
export interface FactSummary { played: number; won: number; drawn: number; lost: number; scored: number; conceded: number; }
/** A stat for and against the team, and the match total, over the matches that recorded it. */
export interface StatAverage { for: number | null; against: number | null; total: number | null; matches: number }
/** A team's numbers for each market over its last matches (backend match_facts.averages). */
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
  summary: { home: FactSummary | null; away: FactSummary | null; h2h: FactSummary | null };
  averages?: { home: TeamAverages | null; away: TeamAverages | null; n: number };
}

type Fetcher = (url: string, init?: RequestInit) => Promise<Response>;

/** Premium content: pass the signed-in fetch (useAuthedFetch) so the
 * backend can check the subscription. */
export async function fetchExplanation(home: string, away: string, fetcher: Fetcher = fetch, date = ""): Promise<MatchExplanation> {
  const params = new URLSearchParams({ home, away, date });
  const res = await fetcher(`${API_URL}/api/explain?${params}`, { cache: "no-store" });
  if (!res.ok) return { explanation: null, sources: [], model: null,
    error: res.status === 402 ? "premium_required" : res.status === 404 ? "feature_off" : "fetch_failed" };
  return res.json();
}

/** Thrown when the backend refuses: 401 sign in, 402 a higher tier, 404 switched off. */
export class AccessError extends Error {
  constructor(public status: number) {
    super(status === 401 ? "sign_in_required" : status === 404 ? "feature_off" : "premium_required");
  }
}

export async function fetchMatchAnalysis(home: string, away: string, fetcher: Fetcher = fetch, date = ""): Promise<MatchAnalysis> {
  const params = new URLSearchParams({ home, away, date });
  const res = await fetcher(`${API_URL}/api/analysis?${params}`, { cache: "no-store" });
  if (res.status === 401 || res.status === 402) throw new AccessError(res.status);
  if (res.status === 404 && (await res.clone().json().catch(() => null))?.detail === "feature_off") throw new AccessError(404);
  if (!res.ok) throw new Error("Analysis failed");
  return res.json();
}

export async function fetchMatchFacts(home: string, away: string, date = ""): Promise<MatchFacts> {
  const params = new URLSearchParams({ home, away, date });
  const res = await fetch(`${API_URL}/api/match/facts?${params}`, { cache: "no-store" });
  if (!res.ok) throw new Error("Match facts failed");
  return res.json();
}

export async function fetchH2H(home: string, away: string): Promise<H2HData> {
  const params = new URLSearchParams({ home, away });
  const res = await fetch(`${API_URL}/api/h2h?${params}`, { cache: "no-store" });
  if (!res.ok) throw new Error("H2H failed");
  return res.json();
}

export async function subscribeToPush(subscription: PushSubscription): Promise<void> {
  await fetch(`${API_URL}/api/push/subscribe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ subscription: subscription.toJSON() }),
  });
}

export async function getPushPublicKey(): Promise<string | null> {
  try {
    const res = await fetch(`${API_URL}/api/push/public-key`);
    if (!res.ok) return null;
    const data = await res.json();
    return data.public_key || null;
  } catch {
    return null;
  }
}
