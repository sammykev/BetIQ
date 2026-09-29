import type { SlipSelection } from "./slip";

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
}

export const FAMILIES: { id: string; name: string }[] = [
  { id: "bb_winner", name: "Winner" },
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
