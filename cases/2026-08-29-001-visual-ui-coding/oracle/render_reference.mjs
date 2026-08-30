import {spawn} from "node:child_process";
import {mkdir} from "node:fs/promises";
import {createServer} from "node:net";
import {dirname, join} from "node:path";
import {fileURLToPath} from "node:url";

import {chromium} from "./reference-app/node_modules/playwright/index.mjs";

const oracleDir = dirname(fileURLToPath(import.meta.url));
const appDir = join(oracleDir, "reference-app");
const outputDir = join(oracleDir, "..", "input", "reference");

async function freePort() {
  return await new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close(() => resolve(port));
    });
  });
}

async function waitUntilReady(url) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Reference app did not start: ${url}`);
}

const port = await freePort();
const server = spawn("npm", ["run", "start", "--", "--port", String(port)], {
  cwd: appDir,
  env: {...process.env, NEXT_TELEMETRY_DISABLED: "1"},
  stdio: "inherit",
});

try {
  const baseUrl = `http://127.0.0.1:${port}`;
  await waitUntilReady(baseUrl);
  await mkdir(outputDir, {recursive: true});
  const browser = await chromium.launch({headless: true});
  const specs = [
    ["01-overview-desktop.png", "/", {width: 1440, height: 1000}],
    ["02-runs-filter-desktop.png", "/runs?filter=open", {width: 1440, height: 1000}],
    ["03-run-detail-desktop.png", "/runs/run-4821", {width: 1440, height: 1000}],
    ["04-overview-mobile-menu.png", "/?menu=open", {width: 390, height: 844}],
  ];
  for (const [filename, path, viewport] of specs) {
    const page = await browser.newPage({viewport, colorScheme: "dark", timezoneId: "UTC"});
    await page.goto(`${baseUrl}${path}`, {waitUntil: "networkidle"});
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({path: join(outputDir, filename), fullPage: false, animations: "disabled"});
    await page.close();
  }
  await browser.close();
} finally {
  server.kill("SIGTERM");
}
