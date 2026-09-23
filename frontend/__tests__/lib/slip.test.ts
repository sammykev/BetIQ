import {
  MAX_SLIP, bookableOnSportybet, combinedProbability, dropPast, isSelected, matchKey,
  removeSelection, selectionFromPrediction, slipAsText, toggleSelection, type SlipSelection,
} from "@/lib/slip";
import type { Prediction } from "@/lib/api";

const sel = (over: Partial<SlipSelection> = {}): SlipSelection => ({
  home: "Arsenal", away: "Chelsea", date: "2026-09-26", market: "1x2",
  marketName: "Match Result", code: "1", label: "Arsenal Win", prob: 0.6, ...over,
});

const pred = (over: Partial<Prediction> = {}): Prediction => ({
  home: "Arsenal", away: "Chelsea", date: "2026-09-26", time: "16:30", league: "PL",
  league_name: "Premier League", flag: "", p_home: 0.6, p_draw: 0.25, p_away: 0.15,
  p_over15: 0.8, p_over25: 0.55, tip_1x2: "Home Win", tip_code: "1", tip_goals: "Over 1.5 Goals",
  goals_type: "Banker", goals_confidence: 0.84, ...over,
});

describe("toggleSelection", () => {
  it("adds, and removes on a second tap", () => {
    const one = toggleSelection([], sel());
    expect(one).toHaveLength(1);
    expect(toggleSelection(one, sel())).toEqual([]);
  });

  it("keeps one pick per match, replacing in place", () => {
    const slip = [sel(), sel({ home: "Leeds", away: "Hull" })];
    const next = toggleSelection(slip, sel({ market: "btts", code: "BTTS-Y", label: "Yes" }));
    expect(next.map(s => s.code)).toEqual(["BTTS-Y", "1"]);
  });

  it("stops at the size limit", () => {
    const full = Array.from({ length: MAX_SLIP }, (_, i) => sel({ home: `T${i}` }));
    expect(toggleSelection(full, sel({ home: "Extra" }))).toHaveLength(MAX_SLIP);
  });

  it("isSelected matches market and code, not just the match", () => {
    const slip = [sel()];
    expect(isSelected(slip, sel())).toBe(true);
    expect(isSelected(slip, sel({ code: "X" }))).toBe(false);
  });
});

describe("slip helpers", () => {
  it("removes by match key", () => {
    expect(removeSelection([sel(), sel({ home: "Leeds" })], matchKey(sel()))).toHaveLength(1);
  });

  it("drops played matches", () => {
    expect(dropPast([sel({ date: "2026-09-20" }), sel()], "2026-09-23")).toEqual([sel()]);
  });

  it("combines probabilities, or gives up when one is unknown", () => {
    expect(combinedProbability([sel({ prob: 0.5 }), sel({ home: "L", prob: 0.8 })])).toBeCloseTo(0.4);
    expect(combinedProbability([sel({ prob: null })])).toBeNull();
    expect(combinedProbability([])).toBeNull();
  });

  it("formats a copyable list", () => {
    expect(slipAsText([sel({ time: "16:30" })])).toBe(
      "BetIQ slip\n1. Arsenal vs Chelsea (2026-09-26 16:30) — Match Result: Arsenal Win");
  });
});

describe("bookableOnSportybet (mirrors backend/booking_slip.py)", () => {
  it.each([
    ["1x2", "X"], ["double_chance", "2X"], ["btts", "BTTS-N"], ["goals_ou", "O25"], ["goals_ou", "U45"],
    ["draw_no_bet", "DNB-H"], ["half_time", "HT1"], ["goals_odd_even", "GOE-ODD"],
  ])("%s %s is bookable", (market, code) => expect(bookableOnSportybet({ market, code })).toBe(true));

  it.each([["correct_score", "CS-1-0"], ["asian_handicap", "AH-H05"], ["goals_ou", "O2"], ["result_btts", "RB-H-Y"]])(
    "%s %s is not", (market, code) => expect(bookableOnSportybet({ market, code })).toBe(false));
});

describe("selectionFromPrediction", () => {
  it("uses the 1X2 tip and its own probability", () => {
    expect(selectionFromPrediction(pred())).toMatchObject({
      market: "1x2", code: "1", label: "Home Win", prob: 0.6, time: "16:30", league: "Premier League",
    });
  });

  it("books double chance as X2 whichever way the tip is written", () => {
    const s = selectionFromPrediction(pred({ tip_code: "2X", tip_1x2: "Away or Draw" }));
    expect(s).toMatchObject({ market: "double_chance", code: "X2", label: "Draw or Away" });
    expect(s!.prob).toBeCloseTo(0.4);
  });

  it("skips predictions without a 1X2 pick", () => {
    expect(selectionFromPrediction(pred({ tip_code: "?" }))).toBeNull();
  });
});
