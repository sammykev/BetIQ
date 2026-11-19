import { test, expect } from "./fixtures";

// The code checker with a sample report (the API answered locally): it lays
// out at every size, the three tickets and the refine settings work, and a
// screenshot of each state is kept (test-results/).
const pick = (home: string, away: string, label: string, prob: number, odds: number) => ({
  home, away, date: "2026-11-22", time: "15:00", league: "Premier League", market: "1x2", market_name: "1X2",
  code: "1", label, prob, odds, odds_source: "sportybet",
});
const slip = (picks: ReturnType<typeof pick>[], chance: number, odds: number, target: number, er: number, left: string[] = []) => ({
  picks, games: picks.length, total_odds: odds, win_chance: chance, unmodelled: 0, estimated_prices: 0, from_odds: 0,
  target_chance: target, left_out: left, left_because: "chance", expected_return: er, bonus: 0, bonus_top: 0.03,
});
const REPORT = {
  code: "TEST01",
  legs: [
    { home: "Arsenal", away: "Chelsea", date: "2026-11-22", time: "15:00", league: "Premier League", market: "1X2",
      pick: "Home", odds: 1.9, active: true, our_prob: 0.55, chance: 0.55, chance_from: "model", implied: 0.526,
      value: 0.045, verdict: "fair", suggestion: { market_name: "Double chance", label: "Arsenal or draw", prob: 0.78, odds: 1.25, odds_source: "sportybet" } },
    { home: "Leeds United", away: "Everton", date: "2026-11-22", time: "17:30", league: "Premier League", market: "Over/Under",
      pick: "Over 2.5", odds: 2.05, active: true, our_prob: 0.44, chance: 0.44, chance_from: "model", implied: 0.488,
      value: -0.1, verdict: "risky", suggestion: null },
    { home: "Nobody FC", away: "Someone United", date: "2026-11-22", time: "19:00", league: "Somewhere League", market: "1X2",
      pick: "Away", odds: 2.4, active: true, our_prob: null, chance: 0.396, chance_from: "odds", implied: 0.417,
      value: null, verdict: "not_modelled", note: "We don't predict this match: its chance is from SportyBet's odds", suggestion: null },
  ],
  original: { games: 3, total_odds: 9.35, win_chance: 0.096, unmodelled: 1, from_odds: 1 },
  same_odds: null, safest: null,
  tickets: {
    safe: slip([pick("Arsenal", "Chelsea", "Arsenal or draw", 0.78, 1.25), pick("Leeds United", "Everton", "Over 0.5 goals", 0.9, 1.08)],
               0.702, 1.35, 0.5, 0.95, ["Nobody FC v Someone United"]),
    conservative: slip([pick("Arsenal", "Chelsea", "Arsenal or draw", 0.78, 1.25), pick("Leeds United", "Everton", "Leeds or draw", 0.62, 1.55),
                        pick("Nobody FC", "Someone United", "Away", 0.396, 2.4)], 0.255, 4.65, 0.25, 0.92),
    risky: null,
  },
  ticket_notes: { risky: "At 12.5% these games pay at most 9.30x, under your 20.00x minimum." },
  settings: { strictness: "balanced", min_odds: null, max_odds: null, max_leg_odds: null, keep_all: false,
              keep_strong: null, main_markets: false, value: false },
  record: { safe: { aim: 0.5, landed: 12, lost: 11, open: 3, said: 0.52, rate: 0.5217 },
            conservative: { aim: 0.25, landed: 4, lost: 14, open: 2, said: 0.26, rate: 0.2222 },
            risky: { aim: 0.125, landed: 1, lost: 9, open: 1, said: 0.13, rate: 0.1 } },
};

// The site's service worker sends API calls itself (public/sw.js), past the stubs: off here
test.use({ serviceWorkers: "block" });

const CORS = { "access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "*" };

test.beforeEach(async ({ page }) => {
  // This test's own answers: the sample report for the code checker, 503 for the rest of the API
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3100)/, async route => {
    const url = new URL(route.request().url());
    if (url.host !== "127.0.0.1:9") return route.abort();
    if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers: CORS });
    if (url.pathname === "/api/optimizer/code") {
      return route.fulfill({ status: 200, headers: CORS, contentType: "application/json", body: JSON.stringify(REPORT) });
    }
    return route.fulfill({ status: 503, headers: CORS, contentType: "application/json", body: '{"detail":"unavailable"}' });
  });
});

test("the code checker: a report, three tickets and the refine settings", async ({ page }, info) => {
  await page.goto("/preview");
  await expect(page.getByText("From the odds", { exact: true })).toBeVisible({ timeout: 15_000 });
  const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(await overflow(), "horizontal overflow in px").toBeLessThanOrEqual(1);
  await page.screenshot({ path: info.outputPath("1-report.png"), fullPage: true });

  // Opened from a code: the tickets show straight away, the conservative one first
  await expect(page.getByRole("tab", { name: /Conservative/ })).toBeVisible();
  const shown = page.locator("[role=tabpanel]:not([hidden])");
  await expect(shown.getByText(/₦100 on this ticket returns about/)).toBeVisible();
  await page.getByRole("tab", { name: /Risky/ }).click();
  await expect(page.getByText(/under your 20.00x minimum/)).toBeVisible();
  await page.getByRole("tab", { name: /Safe/ }).click();
  await expect(page.getByText(/Left out to keep the chance at 50%/)).toBeVisible();
  await expect(page.getByText(/landed 12 of 23/)).toBeVisible();
  expect(await overflow(), "horizontal overflow in px").toBeLessThanOrEqual(1);
  await page.screenshot({ path: info.outputPath("2-tickets.png"), fullPage: true });

  await page.getByRole("button", { name: /Refine settings/ }).click();
  await expect(page.getByRole("radio", { name: /Strict/ })).toBeVisible();
  await page.getByRole("radio", { name: /Loose/ }).click();
  await page.getByLabel("Value mode").check();
  expect(await overflow(), "horizontal overflow in px").toBeLessThanOrEqual(1);
  await page.screenshot({ path: info.outputPath("3-settings.png"), fullPage: true });
});
