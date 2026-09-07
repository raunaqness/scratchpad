import { expect, test } from "@playwright/test";

test("creates a LinkedIn draft through the tone choice", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto("/app");

  const composer = page.getByPlaceholder("Send a message...");
  await composer.fill(
    "I want to draft a LinkedIn post for my upcoming product Fujifilm X-T20.",
  );
  await page.getByRole("button", { name: "Send message" }).click();

  await expect(
    page.getByText(/three concrete product facts/i).last(),
  ).toBeVisible({ timeout: 60_000 });

  await composer.fill(
    "It has a 40 MP camera. It has weather sealing. It supports 1 TB storage.",
  );
  await page.getByRole("button", { name: "Send message" }).click();

  await expect(
    page.getByText(/tone of the draft/i).last(),
  ).toBeVisible({ timeout: 60_000 });

  const boldOption = page.locator("label.choice-option").filter({
    hasText: "Bold",
  });
  await expect(boldOption).toBeVisible();
  await boldOption.click();
  await page.getByRole("button", { name: "Proceed" }).click();

  const artifact = page.locator(".artifact-panel");
  await expect(artifact).toBeVisible({ timeout: 60_000 });
  await expect(artifact.locator(".artifact-copy")).toContainText(
    "Fujifilm X-T20",
    { timeout: 60_000 },
  );
});
