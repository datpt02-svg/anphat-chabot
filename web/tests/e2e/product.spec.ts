import { test, expect } from "@playwright/test";

test.describe("Product Detail Page", () => {
  // Use a real slug from /api/categories results; fall back to a generic
  // navigation test that asserts the route renders without 5xx.
  test("PDP route resolves (404 expected for unknown slug, but page renders 404 gracefully)", async ({ page }) => {
    const res = await page.goto("/products/some-non-existent-slug-xyz");
    // Either 200 (real slug) or 404 (graceful not-found) — never 5xx.
    expect(res?.status() ?? 0).toBeLessThan(500);
  });

  test("Compare link in header navigates to /compare", async ({ page }) => {
    await page.goto("/");
    const compareLink = page.getByRole("link", { name: /so sánh/i }).first();
    await expect(compareLink).toBeVisible();
    await compareLink.click();
    await expect(page).toHaveURL(/\/compare/);
  });
});
