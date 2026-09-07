import { expect, test } from "@playwright/test";

test("Signal app loads with a fixed chat workspace", async ({ page }) => {
  await page.goto("/app");

  await expect(page).toHaveTitle("Signal");
  await expect(
    page.getByPlaceholder("Send a message..."),
  ).toBeVisible();
  await expect(page.locator(".app-workspace")).toBeVisible();
  await expect(page.locator(".app-workspace")).toHaveCSS(
    "overflow",
    "hidden",
  );

  await page.screenshot({
    path: "test-results/signal-app-smoke.png",
    fullPage: true,
  });
});
