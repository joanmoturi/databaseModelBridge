import { expect, test } from "@playwright/test";

test("dashboard uses real proxied service responses and masked workspace access", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Service health" })).toBeVisible();
  await expect(page.getByText("Fresh · idle")).toBeVisible();
  await expect(page.getByLabel("Owner access credential")).toHaveAttribute("type", "password");
  const token = process.env.MODELBRIDGE_E2E_TOKEN;
  if (!token) throw new Error("Set a generated disposable workspace token for live browser testing");
  await page.getByLabel("Owner access credential").fill(token);
  await page.getByRole("button", { name: "Load workspace" }).click();
  const workspaceName = process.env.MODELBRIDGE_E2E_WORKSPACE_NAME;
  if (!workspaceName) throw new Error("Set the disposable workspace name for live browser testing");
  await expect(page.getByRole("heading", { name: workspaceName })).toBeVisible();
  await expect(page.getByRole("cell", { name: "workspace.bootstrap" })).toBeVisible();
  await expect(page.getByLabel("Owner access credential")).toHaveValue("");
  const storage = await page.evaluate(() => ({ ...localStorage, ...sessionStorage }));
  expect(JSON.stringify(storage).includes(token)).toBe(false);
});

test("proxy rejects undocumented paths instead of allowing arbitrary outbound requests", async ({ request }) => {
  expect((await request.get("/bridge/not-an-api-route")).status()).toBe(404);
  expect((await request.get("/bridge/health?target=http://example.org")).status()).toBe(404);
});
