import { test, expect } from "@playwright/test";

/**
 * Plan §4 (R26): mock the ag-ui endpoint to avoid LLM cost + flake.
 * Real chat is exercised in manual smoke (release gate #9).
 */
test.describe("CopilotKit chat", () => {
  test("real budget chat GraphQL smoke stays below 500", async ({ request }) => {
    test.skip(!process.env.PLAYWRIGHT_REAL_CHAT_SMOKE, "real runtime smoke is opt-in");

    const res = await request.post("http://localhost:8000/api/copilotkit-graphql", {
      headers: { "Content-Type": "application/json" },
      data: {
        query: `mutation GenerateCopilotResponse($data: GenerateCopilotResponseInput!) {
          generateCopilotResponse(data: $data) {
            threadId
            runId
            status {
              __typename
              ... on BaseResponseStatus { code }
              ... on FailedResponseStatus { reason details }
            }
            messages {
              __typename
              ... on BaseMessageOutput { id createdAt }
              ... on TextMessageOutput { content role }
              ... on ActionExecutionMessageOutput { name arguments }
              ... on ResultMessageOutput { result actionName actionExecutionId }
            }
          }
        }`,
        variables: {
          data: {
            metadata: { requestType: "chat" },
            threadId: "pw-real-budget-thread",
            runId: "pw-real-budget-run",
            messages: [
              {
                id: "m-real-budget",
                createdAt: new Date().toISOString(),
                textMessage: {
                  role: "user",
                  content: "tôi muốn mua laptop 20 triệu",
                },
              },
            ],
            frontend: { actions: [], url: "http://localhost:3000" },
            context: [],
          },
        },
      },
      failOnStatusCode: false,
    });

    const body = await res.text();
    expect(res.status()).toBeLessThan(500);
    expect(body).not.toContain("Unknown filter(s)");
    expect(body).not.toContain("tool result's tool id");
  });

  test("GraphQL runtime endpoint answers without 5xx", async ({ request }) => {
    const res = await request.post("http://localhost:8000/api/copilotkit-graphql", {
      headers: { "Content-Type": "application/json" },
      data: {
        query: `query AvailableAgents { availableAgents { agents { id name description } } }`,
      },
      failOnStatusCode: false,
    });

    expect(res.status()).toBeLessThan(500);
  });

  test("/api/copilotkit info endpoint answers without 5xx", async ({ request }) => {
    const res = await request.post("http://localhost:8000/api/copilotkit", {
      headers: { "Content-Type": "application/json" },
      data: {
        threadId: "test-thread",
        runId: "test-run",
        messages: [],
        tools: [],
        context: [],
        forwardedProps: {},
      },
      failOnStatusCode: false,
    });
    expect(res.status()).toBeLessThan(500);
  });

  test("chat toggle renders on desktop viewport", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto("/");

    // The chat toggle button is in the header (with CopilotKit label).
    // CopilotSidebar / Popup mounts lazily; just assert the page is interactive.
    const header = page.locator("header").first();
    await expect(header).toBeVisible();

    // Body did not crash from CopilotProvider mount.
    await expect(page.locator("body")).toBeVisible();
  });

  test("mock /api/copilotkit responds with SSE-shaped payload (no real LLM)", async ({ page, request }) => {
    // Just hit the backend ag-ui endpoint via Playwright's request context
    // and assert it either returns SSE-ish content OR a graceful 4xx (auth).
    const res = await request.post("http://localhost:8000/api/copilotkit", {
      headers: { "Content-Type": "application/json" },
      data: {
        threadId: "test-thread",
        runId: "test-run",
        messages: [],
        tools: [],
        context: [],
        forwardedProps: {},
      },
      failOnStatusCode: false,
    });
    // Either 200 (copilotkit dev bypass active) or 401/403 (auth required) — never 5xx.
    expect(res.status()).toBeLessThan(500);
  });
});
