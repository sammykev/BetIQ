import { confidenceTier, headlinePick, pickProbability } from "@/lib/picks";
import type { Prediction } from "@/lib/api";

const base: Prediction = {
  home: "Arsenal", away: "Chelsea", date: "2026-09-27", time: "16:30",
  league: "PL", league_name: "Premier League", flag: "",
  p_home: 0.54, p_draw: 0.24, p_away: 0.22, p_over15: 0.81, p_over25: 0.56,
  tip_1x2: "Home Win", tip_code: "1", tip_goals: "Over 1.5 Goals", goals_type: "Banker", goals_confidence: 0.81,
};

describe("pickProbability", () => {
  it("is the tipped outcome's probability, not goals_confidence", () => {
    expect(pickProbability(base)).toBe(0.54);
  });
  it("sums both outcomes for double chance", () => {
    expect(pickProbability({ ...base, tip_code: "1X" })).toBeCloseTo(0.78);
    expect(pickProbability({ ...base, tip_code: "2X" })).toBeCloseTo(0.46);
  });
  it("prefers the API's tip_confidence when present", () => {
    expect(pickProbability({ ...base, tip_confidence: 0.6 })).toBe(0.6);
  });
  it("is null when there's no 1X2 tip", () => {
    expect(pickProbability({ ...base, tip_code: "?" })).toBeNull();
  });
});

describe("headlinePick", () => {
  it("leads with the 1X2 pick and shows the goals tip underneath", () => {
    const h = headlinePick(base);
    expect(h).toMatchObject({ label: "Home Win", prob: 0.54, kind: "single" });
    expect(h.secondary).toEqual({ label: "Over 1.5 Goals", prob: 0.81 });
  });
  it("falls back to the goals tip when there's no 1X2 pick", () => {
    expect(headlinePick({ ...base, tip_code: "?", tip_1x2: "Skip" })).toMatchObject({ label: "Over 1.5 Goals", prob: 0.81, kind: "goals" });
  });
  it("reports no pick when neither market has one", () => {
    const h = headlinePick({ ...base, tip_code: "?", tip_goals: "Skip", goals_confidence: 0 });
    expect(h.prob).toBeNull();
  });
});

describe("confidenceTier", () => {
  it("judges a probability by the kind of pick", () => {
    expect(confidenceTier(0.55, "single")).toBe("lean");
    expect(confidenceTier(0.62, "single")).toBe("strong");
    expect(confidenceTier(0.70, "double")).toBe("lean");
    expect(confidenceTier(0.55, "double")).toBe("weak");
    expect(confidenceTier(0.81, "goals")).toBe("strong");
  });
});
