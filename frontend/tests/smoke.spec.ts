import { expect, test } from "@playwright/test";

test("Scratchpad app loads with a fixed chat workspace", async ({ page }) => {
  await page.goto("/app");

  await expect(page).toHaveTitle("Scratchpad");
  await expect(
    page.getByPlaceholder("Send a message..."),
  ).toBeVisible();
  await expect(page.locator(".app-workspace")).toBeVisible();
  await expect(page.locator(".app-workspace")).toHaveCSS(
    "overflow",
    "hidden",
  );

  await page.screenshot({
    path: "test-results/scratchpad-app-smoke.png",
    fullPage: true,
  });
});
