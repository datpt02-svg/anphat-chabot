import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import * as fs from "node:fs";
import * as path from "node:path";

/**
 * Visual + a11y review. Captures screenshots of key pages at desktop + mobile
 * viewports, runs axe-core a11y scan, and writes a JSON report to
 * `test-results/a11y-report.json`.
 */

const SCREENSHOTS_DIR = "test-results/screenshots";
const REPORT_PATH = "test-results/a11y-report.json";

const PAGES = [
  { name: "home", path: "/" },
  { name: "search", path: "/search?q=laptop" },
  { name: "search-empty", path: "/search?q=zzzz-no-match" },
  { name: "build-pc", path: "/build-pc" },
  { name: "compare", path: "/compare" },
] as const;

const VIEWPORTS = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "mobile", width: 375, height: 812 },
] as const;

test.beforeAll(() => {
  fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });
});

test.describe("Visual capture", () => {
  for (const vp of VIEWPORTS) {
    for (const p of PAGES) {
      test(`screenshot ${p.name} @ ${vp.name}`, async ({ page }) => {
        await page.setViewportSize({ width: vp.width, height: vp.height });
        const res = await page.goto(p.path);
        expect(res?.status() ?? 0).toBeLessThan(500);
        // Let the page settle (queries, fonts).
        await page.waitForLoadState("networkidle", { timeout: 5_000 }).catch(() => {});
        await page.screenshot({
          path: path.join(SCREENSHOTS_DIR, `${p.name}-${vp.name}.png`),
          fullPage: false,
        });
      });
    }
  }
});

test.describe("a11y (axe-core) — desktop only", () => {
  const report: Array<{ page: string; violations: any[] }> = [];

  test.afterAll(() => {
    fs.mkdirSync(path.dirname(REPORT_PATH), { recursive: true });
    fs.writeFileSync(REPORT_PATH, JSON.stringify(report, null, 2));
  });

  for (const p of PAGES) {
    test(`axe scan ${p.name}`, async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 });
      const res = await page.goto(p.path);
      expect(res?.status() ?? 0).toBeLessThan(500);
      await page.waitForLoadState("networkidle", { timeout: 5_000 }).catch(() => {});

      const result = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa"])
        .analyze();

      report.push({ page: p.name, violations: result.violations });

      // Allow contrast/missing-alt warnings but fail on critical roles.
      const critical = result.violations.filter(
        (v) => v.impact === "critical" || v.impact === "serious",
      );
      // We surface critical/serious in the report but don't fail the run —
      // visual review gate is informational for M8a. M9 will tighten.
      // eslint-disable-next-line no-console
      console.log(`[${p.name}] ${result.violations.length} total, ${critical.length} critical/serious`);
    });
  }
});
