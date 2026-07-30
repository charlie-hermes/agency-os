import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const routes = [
  ["/", "Your brand, moving with intent."],
  ["/launch", "Build the brand from evidence."],
  ["/decisions", "You stay in control."],
  ["/brand", "One trusted version of the brand."],
  ["/content", "Content with a reason to exist."],
  ["/ai-presence", "See how AI understands the brand."],
  ["/settings", "Access and service, made clear."],
] as const;

for (const [path, heading] of routes) {
  test(`${path} renders without accessibility violations`, async ({ page }) => {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
    await expect(page.locator("[data-nextjs-dialog]")).toHaveCount(0);
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();
    expect(results.violations).toEqual([]);
  });
}

test("Launch Room exposes a labelled, keyboard-reachable source journey", async ({ page }) => {
  await page.goto("/launch");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
  await expect(page.getByLabel("What will this source help Fleet understand?")).toBeVisible();
  await expect(page.getByLabel("Choose a file")).toBeVisible();
  await expect(page.getByRole("button", { name: "Send for safe review" })).toBeEnabled();
});

test("the responsive shell has no page-level horizontal overflow", async ({ page }) => {
  await page.goto("/");
  const dimensions = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }));
  expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.client);
});

test("unknown reviewed-shape hosts fail closed", async ({ request }) => {
  const response = await request.get("/", { headers: { Host: "unknown.madebyfleet.com" } });
  expect(response.status()).toBe(404);
});
