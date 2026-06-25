import { test, expect } from "@playwright/test";

test.describe("Home page", () => {
  test("loads and renders brand link", async ({ page }) => {
    const res = await page.goto("/");
    expect(res?.status()).toBeLessThan(400);

    // Header brand link.
    await expect(page.getByRole("link", { name: /an phát pc/i }).first()).toBeVisible();

    // Body did not crash.
    await expect(page.locator("body")).toBeVisible();
  });

  test("header search submits and navigates to /search with q param", async ({ page }) => {
    await page.goto("/");
    // Pick the header search (form[role=search] — both header + hero have it; first is header).
    const searchForm = page.locator('form[role="search"]').first();
    const input = searchForm.locator('input[name="q"]');
    await input.fill("laptop");
    await input.press("Enter");
    await expect(page).toHaveURL(/\/search\?.*q=laptop/);
  });
});
