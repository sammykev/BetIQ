import { test as base, expect } from "@playwright/test";

/** Every page, with the backend and third parties answered locally. */
export const test = base.extend({
  page: async ({ page }, use) => {
    await page.route(/^https?:\/\/(?!127\.0\.0\.1:3100)/, async route => {
      const url = new URL(route.request().url());
      // The backend is "down": every page must still lay out its shell and error states
      if (url.host === "127.0.0.1:9") {
        return route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"unavailable"}' });
      }
      return route.abort();
    });
    // A returning visitor: the home page shows its shell without waiting on Clerk
    await page.addInitScript(() => localStorage.setItem("betiq-signed-in", "1"));
    await use(page);
  },
});

export { expect };
