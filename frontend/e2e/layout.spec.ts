import { test, expect } from "./fixtures";
import type { Page } from "@playwright/test";

// Pages that render signed out (the dashboard waits for a Clerk session)
const PAGES = ["/", "/daily", "/optimizer", "/history"];

/** Which navigation each screen should show (see AppShell). */
const NAV_FOR: Record<string, "dock" | "rail" | "sidebar"> = {
  "phone": "dock",
  "phone-landscape": "rail",
  "tablet": "rail",
  "tablet-landscape": "sidebar",
  "laptop": "sidebar",
  "monitor": "sidebar",
};

const navs = (page: Page) => ({
  // The dock is the only nav inside a fixed bottom container; the rail and sidebar are asides
  dock: page.locator("div.fixed > nav[aria-label='Main']"),
  rail: page.locator("aside.w-rail nav[aria-label='Main']"),
  sidebar: page.locator("aside.w-60 nav[aria-label='Main']"),
});

for (const path of PAGES) {
  test(`${path} fits the screen and shows the right menu`, async ({ page }, info) => {
    await page.goto(path);
    await page.waitForLoadState("networkidle");

    const want = NAV_FOR[info.project.name];
    const n = navs(page);
    for (const kind of ["dock", "rail", "sidebar"] as const) {
      if (kind === want) await expect(n[kind]).toBeVisible();
      else await expect(n[kind]).toBeHidden();
    }

    // Nothing pushes the page sideways
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, "horizontal overflow in px").toBeLessThanOrEqual(1);
  });
}

test("menu items are big enough to tap", async ({ page }, info) => {
  await page.goto("/history");
  const want = NAV_FOR[info.project.name];
  const links = navs(page)[want].getByRole("link");
  const count = await links.count();
  expect(count).toBeGreaterThan(0);
  for (let i = 0; i < count; i++) {
    const box = await links.nth(i).boundingBox();
    expect(box, `menu item ${i}`).not.toBeNull();
    expect(box!.height).toBeGreaterThanOrEqual(44);
    expect(box!.width).toBeGreaterThanOrEqual(44);
  }
});

test("the menu marks the page you're on and moves with you", async ({ page }, info) => {
  await page.goto("/history");
  const nav = navs(page)[NAV_FOR[info.project.name]];
  await expect(nav.locator("[aria-current='page']")).toHaveAttribute("href", "/history");
  await nav.locator("a[href='/daily']").click();
  await expect(page).toHaveURL(/\/daily$/);
  await expect(nav.locator("[aria-current='page']")).toHaveAttribute("href", "/daily");
});

test("nothing sits under the phone menu bar at the end of the page", async ({ page }, info) => {
  test.skip(NAV_FOR[info.project.name] !== "dock", "phone portrait only");
  await page.goto("/history");
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.waitForTimeout(400);
  const dockTop = (await navs(page).dock.boundingBox())!.y;
  const contentBottom = await page.evaluate(() => {
    const kids = Array.from(document.querySelectorAll("main > div > *"));
    return Math.max(...kids.map(k => k.getBoundingClientRect().bottom));
  });
  expect(contentBottom).toBeLessThanOrEqual(dockTop + 1);
});

test.describe("reduced motion", () => {
  test("page content appears without waiting on animations", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/history");
    // The full fade-in takes 400ms; with reduced motion it must be done almost at once
    await expect(page.locator("main > div")).toHaveCSS("opacity", "1", { timeout: 150 });
  });
});
