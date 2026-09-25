// Bet slip: bookmaker-neutral selections in the model's market vocabulary.
// backend/booking_slip.py converts them into a platform's booking code.

import type { Prediction } from "./api";
import { pickProbability } from "./picks";

export interface SlipSelection {
  home: string;
  away: string;
  date: string;          // YYYY-MM-DD
  time?: string;
  league?: string;
  market: string;        // model market id: "1x2", "double_chance", "goals_ou", …
  marketName: string;    // "Match Result"
  code: string;          // model option code: "1", "1X", "O25", "BTTS-Y", …
  label: string;         // "Arsenal Win", "Over 2.5"
  prob?: number | null;  // model probability of this outcome
  /** A leg kept from a SportyBet code that we don't model: booked by SportyBet's own ids */
  sb?: { eventId: string; marketId: string; specifier: string; outcomeId: string };
}

export const MAX_SLIP = 30;

/** One selection per match, like a bookmaker accumulator. */
export const matchKey = (s: Pick<SlipSelection, "home" | "away" | "date">) => `${s.home}:${s.away}:${s.date}`;

export function isSelected(slip: SlipSelection[], s: Pick<SlipSelection, "home" | "away" | "date" | "market" | "code">) {
  return slip.some(x => matchKey(x) === matchKey(s) && x.market === s.market && x.code === s.code);
}

/** Add a selection, replacing any other pick from the same match. Tapping
 *  the same selection again removes it. */
export function toggleSelection(slip: SlipSelection[], s: SlipSelection): SlipSelection[] {
  if (isSelected(slip, s)) return slip.filter(x => matchKey(x) !== matchKey(s));
  const i = slip.findIndex(x => matchKey(x) === matchKey(s));
  if (i >= 0) return slip.map((x, j) => (j === i ? s : x));
  if (slip.length >= MAX_SLIP) return slip;
  return [...slip, s];
}

export function removeSelection(slip: SlipSelection[], key: string) {
  return slip.filter(x => matchKey(x) !== key);
}

/** Drop matches that have already been played (kept picks from last week are noise). */
export function dropPast(slip: SlipSelection[], today: string) {
  return slip.filter(s => s.date >= today);
}

/** The model's chance every pick wins, treating matches as independent. */
export function combinedProbability(slip: SlipSelection[]): number | null {
  if (!slip.length || slip.some(s => typeof s.prob !== "number")) return null;
  return slip.reduce((p, s) => p * (s.prob as number), 1);
}

// Mirrors backend/booking_slip.py sportybet_ids — which picks SportyBet can take
const SPORTYBET_FIXED = new Set([
  "1x2:1", "1x2:X", "1x2:2",
  "double_chance:1X", "double_chance:12", "double_chance:X2", "double_chance:2X",
  "btts:BTTS-Y", "btts:BTTS-N", "draw_no_bet:DNB-H", "draw_no_bet:DNB-A",
  "goals_odd_even:GOE-ODD", "goals_odd_even:GOE-EVEN",
  "half_time:HT1", "half_time:HTX", "half_time:HT2",
  "clean_sheet:CS-H", "clean_sheet:CS-A", "win_to_nil:WTN-H", "win_to_nil:WTN-A",
  "corners_1x2:CR-1", "corners_1x2:CR-X", "corners_1x2:CR-2",
]);
const LINE_MARKETS = ["goals_ou", "corners_ou", "cards_ou", "home_goals_ou", "away_goals_ou",
                      "home_corners_ou", "away_corners_ou", "shots_ou", "sot_ou", "home_shots_ou",
                      "away_shots_ou", "home_sot_ou", "away_sot_ou"];

export function bookableOnSportybet(s: Pick<SlipSelection, "market" | "code"> & { sb?: SlipSelection["sb"] }) {
  if (s.sb) return true;
  if (SPORTYBET_FIXED.has(`${s.market}:${s.code}`)) return true;
  // Over/under lines (goals, corners, bookings, team totals): half lines only, e.g. O25, U105
  if (LINE_MARKETS.includes(s.market)) return /^[OU]\d{1,2}5$/.test(s.code);
  if (s.market === "handicap") return /^[HA][+-]\d\.5$/.test(s.code);  // H-1.5, A+2.5
  if (s.market === "dc_goals") return /^(1X|X2|12)&[OU]\d5$/.test(s.code);  // 1X&O15
  return false;
}

const DC_NAMES: Record<string, string> = { "1X": "Home or Draw", "X2": "Draw or Away", "2X": "Draw or Away", "12": "Home or Away" };

/** The slip selection for a prediction card's headline pick (1X2 / double chance). */
export function selectionFromPrediction(p: Prediction): SlipSelection | null {
  const code = p.tip_code;
  const prob = pickProbability(p);
  if (prob === null) return null;
  const single = code === "1" || code === "X" || code === "2";
  const label = single ? p.tip_1x2 : DC_NAMES[code] ?? p.tip_1x2;
  return {
    home: p.home, away: p.away, date: p.date, time: p.time, league: p.league_name,
    market: single ? "1x2" : "double_chance",
    marketName: single ? "Match Result" : "Double Chance",
    code: code === "2X" ? "X2" : code, label, prob,
  };
}

/** Plain-text slip for bookmakers without automatic booking. */
export function slipAsText(slip: SlipSelection[]): string {
  const lines = slip.map((s, i) =>
    `${i + 1}. ${s.home} vs ${s.away} (${s.date}${s.time ? " " + s.time : ""}) — ${s.marketName}: ${s.label}`);
  return ["BetIQ slip", ...lines].join("\n");
}
