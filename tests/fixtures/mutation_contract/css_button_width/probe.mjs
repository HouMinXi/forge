import assert from "node:assert/strict";
import { readdirSync } from "node:fs";
import { createRequire } from "node:module";

function findMount(marker) {
  for (const entry of readdirSync("/opt")) {
    if (!entry.startsWith("node-")) continue;
    let names;
    try {
      names = readdirSync("/opt/" + entry);
    } catch {
      continue;
    }
    if (names.includes(marker)) return "/opt/" + entry;
  }
  throw new Error("no /opt mount contains " + marker);
}

const playwright = findMount("playwright-core");
const chrome = findMount("chrome");
const require = createRequire(import.meta.url);
const { chromium } = require(playwright + "/playwright-core/index.js");

const browser = await chromium.launch({
  executablePath: chrome + "/chrome",
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
