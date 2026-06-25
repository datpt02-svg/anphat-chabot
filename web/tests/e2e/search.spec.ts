import { test, expect } from "@playwright/test";

test.describe("Search / PLP", () => {
  test("renders facet sidebar and sort controls", async ({ page }) => {
    const res = await page.goto("/search");
    expect(res?.status()).toBeLessThan(400);
    // aside[aria-label="Bộ lọc"]
    await expect(page.locator('aside[aria-label="Bộ lọc"]')).toBeVisible();
    // sortbar — has a select or button with "Sắp xếp" or similar
    const sort = page.locator('[data-testid="sort-bar"], select, [aria-label*="sort" i], [aria-label*="sắp" i]').first();
    await expect(sort).toBeAttached();
  });

  test("renders empty state when no results", async ({ page }) => {
    await page.goto("/search?q=zzzzzzzz-no-match-xyz&limit=1");
    // Wait for either empty state or result list — empty state uses "Không tìm thấy sản phẩm"
    await expect(page.getByText(/không tìm thấy sản phẩm/i)).toBeVisible({ timeout: 15_000 });
  });

  test("URL is source of truth — direct deep link works", async ({ page }) => {
    const url = "/search?category=laptop&sort=price_asc&price_max=30000000";
    await page.goto(url);
    await expect(page).toHaveURL(new RegExp("category=laptop"));
    await expect(page).toHaveURL(/sort=price_asc/);
  });
});
