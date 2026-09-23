// What a prediction actually recommends, and how likely the model thinks it is.
//
// The API's `goals_confidence` is the probability of the *goals* tip (e.g.
// Over 1.5), not of the 1X2 tip shown as the headline pick — so the 1X2 pick's
// probability is derived from p_home / p_draw / p_away here.

import type { Prediction } from "./api";

type PickFields = Pick<Prediction, "tip_code" | "p_home" | "p_draw" | "p_away"> & {
  tip_confidence?: number | null;
};

/** Probability of the 1X2 / double-chance tip, or null when there's no such tip. */
export function pickProbability(p: PickFields): number | null {
  if (typeof p.tip_confidence === "number") return p.tip_confidence;
  switch (p.tip_code) {
    case "1": return p.p_home;
    case "X": return p.p_draw;
    case "2": return p.p_away;
    case "1X": return p.p_home + p.p_draw;
    case "X2":
    case "2X": return p.p_away + p.p_draw;
    case "12": return p.p_home + p.p_away;
    default: return null;
  }
}

export type PickKind = "single" | "double" | "goals";

export function pickKind(code: string | undefined): PickKind {
  return code === "1X" || code === "X2" || code === "2X" || code === "12" ? "double" : "single";
}

export type Tier = "strong" | "lean" | "weak";

/**
 * How strong a probability is *for that kind of pick*. A straight win at 55%
 * is a solid 1X2 pick (three outcomes); a double chance or goals line at 55%
 * is barely better than a coin flip. Revisit against the backtest's
 * calibration numbers (/api/model/metrics).
 */
export function confidenceTier(prob: number, kind: PickKind): Tier {
  const [strong, lean] = kind === "single" ? [0.6, 0.45] : kind === "double" ? [0.8, 0.65] : [0.75, 0.6];
  return prob >= strong ? "strong" : prob >= lean ? "lean" : "weak";
}

export interface HeadlinePick {
  label: string;
  prob: number | null;
  kind: PickKind;
  /** The goals tip, when there's also a 1X2 pick to headline. */
  secondary: { label: string; prob: number } | null;
}

/** The pick a card leads with: the 1X2 tip if there is one, else the goals tip. */
export function headlinePick(p: Prediction): HeadlinePick {
  const prob = pickProbability(p);
  const goals = p.tip_goals && p.tip_goals !== "Skip" && p.goals_confidence > 0
    ? { label: p.tip_goals, prob: p.goals_confidence }
    : null;
  if (prob !== null) return { label: p.tip_1x2, prob, kind: pickKind(p.tip_code), secondary: goals };
  if (goals) return { label: goals.label, prob: goals.prob, kind: "goals", secondary: null };
  return { label: "No pick", prob: null, kind: "single", secondary: null };
}
