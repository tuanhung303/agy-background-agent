import { chromium } from "/Users/__blitzzz/.npm/_npx/e41f203b7505f1fb/node_modules/playwright/index.mjs";

// Global hard timeout: 60s ceiling to prevent zombie background processes
const hardTimer = setTimeout(() => {
  console.error("HARD TIMEOUT: Script exceeded 60s execution limit");
  process.exit(1);
}, 60000);
if (hardTimer && typeof hardTimer.unref === "function") {
  hardTimer.unref();
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({
      viewport: { width: 1560, height: 1080 },
      ignoreHTTPSErrors: true,
    });
    const page = await context.newPage();

    page.on("console", msg => {
      if (msg.type() === "error") console.error("Console Error:", msg.text());
    });

    const baseUrl = "https://seeda-staging.pages.dev";
    console.log("1. Authenticating at /auth...");
    await page.goto(`${baseUrl}/auth?cb=${Date.now()}`, { waitUntil: "domcontentloaded", timeout: 20000 });
    await page.fill('input[type="email"]', "auronsanjr+globalrescue@gmail.com");
    await page.fill('input[type="password"]', "auronsanjr+globalrescue@gmail.com");
    await page.click('button[type="submit"]');
    await page.waitForTimeout(3000);

    console.log("2. Navigating to Performance tab...");
    await page.goto(`${baseUrl}/?tab=performance`, { waitUntil: "domcontentloaded", timeout: 20000 });
    await page.waitForTimeout(4000);

    console.log("3. Waiting for Performance table element...");
    await page.locator('text=MNTN CTV').first().waitFor({ state: 'attached', timeout: 20000 });
    await page.waitForTimeout(2000);
    console.log("Performance table successfully rendered!");

    const artifactDir = "/Users/__blitzzz/.gemini/antigravity-cli/brain/fa8d65d5-16fd-4120-808e-af1b0b57a1da";

    // Screenshot default
    await page.screenshot({ path: `${artifactDir}/granular_channel_default_verified.png`, fullPage: false });
    console.log("Captured default verified screenshot.");

    // Check toggle buttons
    const desktopToggles = page.locator('.hidden.xl\\:block button[title*="channel in plan" i], .hidden.xl\\:block button[title*="channel from plan" i]');
    const count = await desktopToggles.count();
    console.log(`Found ${count} desktop toggle buttons.`);

    if (count > 0) {
      console.log("4. Toggling off MNTN CTV...");
      await desktopToggles.first().click({ force: true });
      await page.waitForTimeout(3000);

      // Screenshot reallocated
      await page.screenshot({ path: `${artifactDir}/granular_channel_reallocated_verified.png`, fullPage: false });
      console.log("Captured reallocated verified screenshot.");

      // 5. Reload page to verify persistence
      console.log("5. Reloading page to test persistence...");
      await page.reload({ waitUntil: "domcontentloaded", timeout: 20000 });
      await page.waitForTimeout(4000);
      await page.locator('text=MNTN CTV').first().waitFor({ state: 'attached', timeout: 20000 });

      // Screenshot persisted reload
      await page.screenshot({ path: `${artifactDir}/granular_channel_persisted_verified.png`, fullPage: false });
      console.log("Captured persisted reload screenshot.");
    }

    console.log("Live verification complete!");
  } finally {
    await browser.close();
  }
}

main()
  .then(() => process.exit(0))
  .catch((err) => {
    console.error("Verification failed:", err);
    process.exit(1);
  });
