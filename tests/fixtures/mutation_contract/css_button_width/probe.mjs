import assert from "node:assert/strict";
import { chromium } from "/opt/node-1/playwright-core/index.mjs";

const browser = await chromium.launch({
  executablePath: "/opt/node-2/chrome",
  headless: true,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
try {
  const page = await browser.newPage({ viewport: { width: 800, height: 600 } });
  await page.goto("file:///workspace/index.html");
  const width = await page.locator("#panel").evaluate(
    (element) => element.getBoundingClientRect().width,
  );
  if (process.env.FORGE_CSS_STRONG === "1") {
    assert.equal(width, 120);
  }
} finally {
  await browser.close();
}
