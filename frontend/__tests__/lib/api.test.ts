/**
 * Unit tests for lib/api.ts — all fetch calls are mocked; no network access.
 */

import {
  fetchPredictions,
  fetchLeagues,
  fetchCalendar,
  fetchHistory,
  fetchExplanation,
  fetchMatchAnalysis,
  fetchH2H,
  triggerRefresh,
  type Prediction,
  type PredictionsResponse,
  type League,
} from "../../lib/api";

// ── Setup ────────────────────────────────────────────────────────────────

const mockFetch = jest.fn();
global.fetch = mockFetch;

function mockOk(body: unknown) {
  return Promise.resolve({
    ok: true,
    json: () => Promise.resolve(body),
  } as Response);
}

function mockError(status = 500) {
  return Promise.resolve({
    ok: false,
    status,
    json: () => Promise.resolve({ error: "server error" }),
  } as Response);
}

beforeEach(() => {
  mockFetch.mockReset();
});

// ── fetchPredictions ──────────────────────────────────────────────────────

describe("fetchPredictions", () => {
  const mockResponse: PredictionsResponse = {
    predictions: [],
    total: 0,
    last_updated: null,
  };

  it("returns data on success", async () => {
    mockFetch.mockResolvedValueOnce(mockOk(mockResponse));
    const result = await fetchPredictions();
    expect(result).toEqual(mockResponse);
  });

  it("always includes limit=500 in the query", async () => {
    mockFetch.mockResolvedValueOnce(mockOk(mockResponse));
    await fetchPredictions();
    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).toContain("limit=500");
  });

  it("includes league param when provided and not ALL", async () => {
    mockFetch.mockResolvedValueOnce(mockOk(mockResponse));
    await fetchPredictions("PL");
    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).toContain("league=PL");
  });

  it("omits league param when value is ALL", async () => {
    mockFetch.mockResolvedValueOnce(mockOk(mockResponse));
    await fetchPredictions("ALL");
    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).not.toContain("league=");
  });

  it("includes min_confidence when provided", async () => {
    mockFetch.mockResolvedValueOnce(mockOk(mockResponse));
    await fetchPredictions(undefined, 70);
    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).toContain("min_confidence=70");
  });

  it("omits min_confidence when not provided", async () => {
    mockFetch.mockResolvedValueOnce(mockOk(mockResponse));
    await fetchPredictions();
    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).not.toContain("min_confidence");
  });

  it("throws on non-ok response", async () => {
    mockFetch.mockResolvedValueOnce(mockError(503));
    await expect(fetchPredictions()).rejects.toThrow("Failed to fetch predictions");
  });

  it("calls /api/predictions endpoint", async () => {
    mockFetch.mockResolvedValueOnce(mockOk(mockResponse));
    await fetchPredictions();
    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).toContain("/api/predictions");
  });
});

// ── fetchLeagues ──────────────────────────────────────────────────────────

describe("fetchLeagues", () => {
  const mockLeagues: League[] = [
    { code: "PL", name: "Premier League", country: "England", flag: "🏴󠁧󠁢󠁥󠁮󠁧󠁿" },
    { code: "CL", name: "Champions League", country: "Europe", flag: "🏆" },
  ];

  it("returns array of leagues on success", async () => {
    mockFetch.mockResolvedValueOnce(mockOk(mockLeagues));
    const result = await fetchLeagues();
    expect(result).toEqual(mockLeagues);
  });

  it("returns empty array on error (silent failure)", async () => {
    mockFetch.mockResolvedValueOnce(mockError(500));
    const result = await fetchLeagues();
    expect(result).toEqual([]);
  });

  it("calls /api/leagues endpoint", async () => {
    mockFetch.mockResolvedValueOnce(mockOk([]));
    await fetchLeagues();
    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).toContain("/api/leagues");
  });
});

// ── fetchCalendar ─────────────────────────────────────────────────────────

describe("fetchCalendar", () => {
  it("returns calendar data on success", async () => {
    const mockCal = { "2025-06-01": { total: 5, won: 3, lost: 1, pending: 1 } };
    mockFetch.mockResolvedValueOnce(mockOk(mockCal));
    const result = await fetchCalendar("2025-06");
    expect(result).toEqual(mockCal);
  });

  it("returns empty object on error (silent failure)", async () => {
    mockFetch.mockResolvedValueOnce(mockError(500));
    const result = await fetchCalendar("2025-06");
    expect(result).toEqual({});
  });

  it("includes month param in URL", async () => {
    mockFetch.mockResolvedValueOnce(mockOk({}));
    await fetchCalendar("2025-06");
    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).toContain("month=2025-06");
  });
});

// ── fetchHistory ──────────────────────────────────────────────────────────

describe("fetchHistory", () => {
  it("returns history predictions on success", async () => {
    const mockHistory = [
      { home: "Arsenal", away: "Chelsea", outcome: "won", actual_result: "H" },
    ];
    mockFetch.mockResolvedValueOnce(mockOk(mockHistory));
    const result = await fetchHistory("2025-06-01");
    expect(result).toEqual(mockHistory);
  });

  it("returns empty array on error", async () => {
    mockFetch.mockResolvedValueOnce(mockError(404));
    const result = await fetchHistory("2025-06-01");
    expect(result).toEqual([]);
  });

  it("includes date param in URL", async () => {
    mockFetch.mockResolvedValueOnce(mockOk([]));
    await fetchHistory("2025-06-01");
    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).toContain("date=2025-06-01");
  });
});

// ── fetchExplanation ──────────────────────────────────────────────────────

describe("fetchExplanation", () => {
  it("returns explanation on success", async () => {
    const mockExpl = {
      explanation: "Arsenal are in great form.",
      sources: ["llm"],
      model: "llama3",
      error: null,
    };
    mockFetch.mockResolvedValueOnce(mockOk(mockExpl));
    const result = await fetchExplanation("Arsenal", "Chelsea");
    expect(result).toEqual(mockExpl);
  });

  it("returns null explanation with error on failure", async () => {
    mockFetch.mockResolvedValueOnce(mockError(500));
    const result = await fetchExplanation("Arsenal", "Chelsea");
    expect(result.explanation).toBeNull();
    expect(result.error).toBe("fetch_failed");
  });

  it("includes home and away params in URL", async () => {
    mockFetch.mockResolvedValueOnce(mockOk({ explanation: null, sources: [], model: null, error: null }));
    await fetchExplanation("Arsenal", "Chelsea");
    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).toContain("home=Arsenal");
    expect(calledUrl).toContain("away=Chelsea");
  });
});

// ── fetchMatchAnalysis ────────────────────────────────────────────────────

describe("fetchMatchAnalysis", () => {
  const mockAnalysis = {
    xg_home: 1.5,
    xg_away: 1.2,
    elo: {
      home: 1600,
      away: 1500,
      gap: 100,
      gap_out_of: 400,
      leading: "Arsenal",
      label: "Slight Edge",
      description: "Home team holds a small advantage.",
      implied_win_prob: 0.62,
    },
    markets: [
      { id: "1x2", name: "Match Result", options: [{ label: "Home Win", code: "1", prob: 0.6 }] },
    ],
    recommended: { label: "Home Win", code: "1", prob: 0.6, market: "Match Result", market_id: "1x2" },
  };

  it("returns analysis data on success", async () => {
    mockFetch.mockResolvedValueOnce(mockOk(mockAnalysis));
    const result = await fetchMatchAnalysis("Arsenal", "Chelsea");
    expect(result.xg_home).toBe(1.5);
    expect(result.elo.label).toBe("Slight Edge");
  });

  it("throws on non-ok response", async () => {
    mockFetch.mockResolvedValueOnce(mockError(500));
    await expect(fetchMatchAnalysis("Arsenal", "Chelsea")).rejects.toThrow("Analysis failed");
  });

  it("includes home and away in URL", async () => {
    mockFetch.mockResolvedValueOnce(mockOk(mockAnalysis));
    await fetchMatchAnalysis("Arsenal", "Chelsea");
    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).toContain("home=Arsenal");
    expect(calledUrl).toContain("away=Chelsea");
  });
});

// ── fetchH2H ─────────────────────────────────────────────────────────────

describe("fetchH2H", () => {
  const mockH2H = {
    meetings: [
      {
        date: "2025-01-15",
        home_team: "Arsenal",
        away_team: "Chelsea",
        score: "2-1",
        result: "H",
        winner: "Arsenal",
      },
    ],
    summary: { total: 1, home_wins: 1, draws: 0, away_wins: 0, home_goals: 2, away_goals: 1, avg_goals: 3.0, btts_count: 0 },
    source: "api" as const,
  };

  it("returns H2H data on success", async () => {
    mockFetch.mockResolvedValueOnce(mockOk(mockH2H));
    const result = await fetchH2H("Arsenal", "Chelsea");
    expect(result.meetings).toHaveLength(1);
    expect(result.summary?.total).toBe(1);
  });

  it("throws on non-ok response", async () => {
    mockFetch.mockResolvedValueOnce(mockError(500));
    await expect(fetchH2H("Arsenal", "Chelsea")).rejects.toThrow("H2H failed");
  });

  it("includes home and away params in URL", async () => {
    mockFetch.mockResolvedValueOnce(mockOk(mockH2H));
    await fetchH2H("Arsenal", "Chelsea");
    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).toContain("home=Arsenal");
    expect(calledUrl).toContain("away=Chelsea");
  });
});

// ── triggerRefresh ────────────────────────────────────────────────────────

describe("triggerRefresh", () => {
  it("sends a POST request to /api/refresh", async () => {
    mockFetch.mockResolvedValueOnce(mockOk({}));
    await triggerRefresh();
    const [calledUrl, options] = mockFetch.mock.calls[0];
    expect(calledUrl).toContain("/api/refresh");
    expect((options as RequestInit).method).toBe("POST");
  });

  it("completes without throwing on success", async () => {
    mockFetch.mockResolvedValueOnce(mockOk({}));
    await expect(triggerRefresh()).resolves.toBeUndefined();
  });
});
