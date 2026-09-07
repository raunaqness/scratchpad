# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: linkedin-draft.spec.ts >> creates a LinkedIn draft through the tone choice
- Location: tests/linkedin-draft.spec.ts:3:1

# Error details

```
Test timeout of 120000ms exceeded.
```

```
Error: locator.click: Test timeout of 120000ms exceeded.
Call log:
  - waiting for getByRole('button', { name: 'Send message' })
    - locator resolved to <button disabled type="button" data-size="icon" data-state="closed" data-variant="default" aria-label="Send message" data-slot="tooltip-trigger" class="inline-flex shrink-0 items-center justify-center gap-2 text-sm font-medium whitespace-nowrap transition-all outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 [&_…>…</button>
  - attempting click action
    2 × waiting for element to be visible, enabled and stable
      - element is not enabled
    - retrying click action
    - waiting 20ms
    2 × waiting for element to be visible, enabled and stable
      - element is not enabled
    - retrying click action
      - waiting 100ms
    77 × waiting for element to be visible, enabled and stable
       - element is not enabled
     - retrying click action
       - waiting 500ms
    - waiting for "http://127.0.0.1:3001/app" navigation to finish...
    - navigated to "http://127.0.0.1:3001/app"
    - waiting for element to be visible, enabled and stable
  - element was detached from the DOM, retrying
    - locator resolved to <button disabled type="button" data-size="icon" data-state="closed" data-variant="default" aria-label="Send message" data-slot="tooltip-trigger" class="inline-flex shrink-0 items-center justify-center gap-2 text-sm font-medium whitespace-nowrap transition-all outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 [&_…>…</button>
  - attempting click action
    2 × waiting for element to be visible, enabled and stable
      - element is not enabled
    - retrying click action
    - waiting 20ms
    2 × waiting for element to be visible, enabled and stable
      - element is not enabled
    - retrying click action
      - waiting 100ms
    137 × waiting for element to be visible, enabled and stable
        - element is not enabled
      - retrying click action
        - waiting 500ms
    - waiting for "http://127.0.0.1:3001/app" navigation to finish...
    - navigated to "http://127.0.0.1:3001/app"
    - waiting for element to be visible, enabled and stable
  - element was detached from the DOM, retrying
    - locator resolved to <button disabled type="button" data-size="icon" data-state="closed" data-variant="default" aria-label="Send message" data-slot="tooltip-trigger" class="inline-flex shrink-0 items-center justify-center gap-2 text-sm font-medium whitespace-nowrap transition-all outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 [&_…>…</button>
  - attempting click action
    2 × waiting for element to be visible, enabled and stable
      - element is not enabled
    - retrying click action
    - waiting 20ms
    2 × waiting for element to be visible, enabled and stable
      - element is not enabled
    - retrying click action
      - waiting 100ms
    4 × waiting for element to be visible, enabled and stable
      - element is not enabled
    - retrying click action
      - waiting 500ms

```

# Page snapshot

```yaml
- generic [active] [ref=f2e1]:
  - navigation [ref=f2e2]:
    - link "Signal home" [ref=f2e3] [cursor=pointer]:
      - /url: /
      - generic [ref=f2e4]: S
      - generic [ref=f2e5]: Signal
    - generic [ref=f2e6]:
      - link "About" [ref=f2e7] [cursor=pointer]:
        - /url: /about
      - button "Switch to dark mode" [ref=f2e8] [cursor=pointer]
  - main [ref=f2e12]:
    - generic [ref=f2e13]:
      - button "New Thread" [ref=f2e14]
      - generic [ref=f2e18]:
        - heading "How can I help you today?" [level=1] [ref=f2e20]
        - generic [ref=f2e21]:
          - generic [ref=f2e23]:
            - textbox "Message input" [ref=f2e24]:
              - /placeholder: Send a message...
            - generic [ref=f2e25]:
              - button "Add Attachment" [ref=f2e26]
              - generic [ref=f2e28]:
                - button "Send message" [disabled]
          - generic [ref=f2e29]:
            - button "Draft a LinkedIn post from three product facts" [ref=f2e31]:
              - generic [ref=f2e32]: Draft a LinkedIn post
              - generic [ref=f2e33]: from three product facts
            - button "Learn what Signal needs before drafting" [ref=f2e35]:
              - generic [ref=f2e36]: Learn what Signal needs
              - generic [ref=f2e37]: before drafting
```

# Test source

```ts
  1  | import { expect, test } from "@playwright/test";
  2  | 
  3  | test("creates a LinkedIn draft through the tone choice", async ({ page }) => {
  4  |   test.setTimeout(120_000);
  5  |   await page.goto("/app");
  6  | 
  7  |   const composer = page.getByPlaceholder("Send a message...");
  8  |   await composer.fill(
  9  |     "I want to draft a LinkedIn post for my upcoming product Fujifilm X-T20.",
  10 |   );
> 11 |   await page.getByRole("button", { name: "Send message" }).click();
     |                                                            ^ Error: locator.click: Test timeout of 120000ms exceeded.
  12 | 
  13 |   await expect(
  14 |     page.getByText(/three concrete product facts/i).last(),
  15 |   ).toBeVisible({ timeout: 60_000 });
  16 | 
  17 |   await composer.fill(
  18 |     "It has a 40 MP camera. It has weather sealing. It supports 1 TB storage.",
  19 |   );
  20 |   await page.getByRole("button", { name: "Send message" }).click();
  21 | 
  22 |   await expect(
  23 |     page.getByText(/tone of the draft/i).last(),
  24 |   ).toBeVisible({ timeout: 60_000 });
  25 | 
  26 |   const boldOption = page.locator("label.choice-option").filter({
  27 |     hasText: "Bold",
  28 |   });
  29 |   await expect(boldOption).toBeVisible();
  30 |   await boldOption.click();
  31 |   await page.getByRole("button", { name: "Proceed" }).click();
  32 | 
  33 |   const artifact = page.locator(".artifact-panel");
  34 |   await expect(artifact).toBeVisible({ timeout: 60_000 });
  35 |   await expect(artifact.locator(".artifact-copy")).toContainText(
  36 |     "Fujifilm X-T20",
  37 |     { timeout: 60_000 },
  38 |   );
  39 | });
  40 | 
```