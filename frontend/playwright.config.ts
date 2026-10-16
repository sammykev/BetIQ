import { defineConfig, devices } from "@playwright/test";

/*
 * Layout checks at the sizes people actually use, upright and on their side.
 * Run with `npm run test:e2e` (builds and starts the site on port 3100).
 * The API and Clerk are stubbed in e2e/fixtures.ts, so no backend is needed.
 */
const PORT = 3100;
// Well-formed but fake Clerk keys: the pages render signed out. Production-style
// keys, since a development key sends every first visit off to Clerk to set up
const FAKE_PK = "pk_live_" + Buffer.from("clerk.example.com$").toString("base64");

const chromium = devices["Desktop Chrome"];
const touch = { ...chromium, hasTouch: true, isMobile: true };

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
    launchOptions: {
      // Every outside request is stubbed or blocked, so no proxy is ever needed
      args: ["--no-proxy-server"],
      ...(process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {}),
    },
  },
  projects: [
    { name: "phone",            use: { ...touch, viewport: { width: 390, height: 844 }, deviceScaleFactor: 3 } },
    { name: "phone-landscape",  use: { ...touch, viewport: { width: 844, height: 390 }, deviceScaleFactor: 3 } },
    { name: "tablet",           use: { ...touch, viewport: { width: 820, height: 1180 }, deviceScaleFactor: 2 } },
    { name: "tablet-landscape", use: { ...touch, viewport: { width: 1180, height: 820 }, deviceScaleFactor: 2 } },
    { name: "laptop",           use: { ...chromium, viewport: { width: 1366, height: 768 } } },
    { name: "monitor",          use: { ...chromium, viewport: { width: 1920, height: 1080 } } },
  ],
  webServer: {
    command: `npm run build && npx next start -p ${PORT}`,
    url: `http://127.0.0.1:${PORT}/history`,
    timeout: 240_000,
    reuseExistingServer: !process.env.CI,
    env: {
      NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: FAKE_PK,
      CLERK_SECRET_KEY: ["sk", "live", "0".repeat(40)].join("_"),
      // Nothing listens here; the browser's calls are answered by the stubs
      NEXT_PUBLIC_API_URL: "http://127.0.0.1:9",
      NEXT_TELEMETRY_DISABLED: "1",
      // Read when the chat and email routes load; never called in these tests
      GROQ_API_KEY: "test",
      RESEND_API_KEY: "re_test",
    },
  },
});
