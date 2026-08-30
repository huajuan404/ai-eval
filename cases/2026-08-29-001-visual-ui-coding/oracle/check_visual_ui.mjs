import {execFileSync} from "node:child_process";
import {mkdir, readFile, writeFile} from "node:fs/promises";
import {join} from "node:path";

import {similarityFromNormalizedRmse} from "./visual_metric.mjs";
import {findScreenshotSurface} from "./surface_guard.mjs";

function argumentsMap(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 2) result[argv[index].replace(/^--/, "")] = argv[index + 1];
  return result;
}

const args = argumentsMap(process.argv.slice(2));
const outputDir = join(args["work-root"], "ai_eval", "screenshots");
const reportPath = join(args["work-root"], "ai_eval_check_report.json");
const floor = Number(args.floor);
const contract = JSON.parse(await readFile(args.contract, "utf8"));
await mkdir(outputDir, {recursive: true});

class CdpClient {
  constructor(url) {
    this.socket = new WebSocket(url);
    this.nextId = 1;
    this.pending = new Map();
    this.waiters = new Map();
  }

  async open() {
    await new Promise((resolve, reject) => {
      this.socket.addEventListener("open", resolve, {once: true});
      this.socket.addEventListener("error", reject, {once: true});
    });
    this.socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.id) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        this.pending.delete(message.id);
        if (message.error) pending.reject(new Error(message.error.message));
        else pending.resolve(message.result);
        return;
      }
      const listeners = this.waiters.get(message.method) || [];
      this.waiters.delete(message.method);
      for (const resolve of listeners) resolve(message.params);
    });
  }

  call(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, {resolve, reject});
      this.socket.send(JSON.stringify({id, method, params}));
    });
  }

  waitFor(method, timeoutMs = 15000) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`Timed out waiting for ${method}`)), timeoutMs);
      const done = (value) => { clearTimeout(timer); resolve(value); };
      this.waiters.set(method, [...(this.waiters.get(method) || []), done]);
    });
  }

  close() {
    this.socket.close();
  }
}

async function createPage(cdpUrl) {
  const response = await fetch(`${cdpUrl}/json/new?${encodeURIComponent("about:blank")}`, {method: "PUT"});
  if (!response.ok) throw new Error(`Cannot create Chrome target: ${response.status}`);
  const target = await response.json();
  const client = new CdpClient(target.webSocketDebuggerUrl);
  await client.open();
  await client.call("Page.enable");
  await client.call("Runtime.enable");
  return client;
}

async function closeBrowser(cdpUrl) {
  try {
    const response = await fetch(`${cdpUrl}/json/version`);
    const version = await response.json();
    const socket = new WebSocket(version.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      socket.addEventListener("open", resolve, {once: true});
      socket.addEventListener("error", reject, {once: true});
    });
    socket.send(JSON.stringify({id: 1, method: "Browser.close"}));
    await new Promise((resolve) => {
      const timer = setTimeout(resolve, 1000);
      socket.addEventListener("close", () => { clearTimeout(timer); resolve(); }, {once: true});
    });
  } catch {}
}

async function evaluate(client, expression) {
  const response = await client.call("Runtime.evaluate", {expression, awaitPromise: true, returnByValue: true});
  if (response.exceptionDetails) throw new Error(response.exceptionDetails.text || "Browser expression failed");
  return response.result.value;
}

async function setViewport(client, width, height) {
  await client.call("Emulation.setDeviceMetricsOverride", {width, height, deviceScaleFactor: 1, mobile: width < 600});
  await client.call("Emulation.setEmulatedMedia", {media: "screen", features: [{name: "prefers-color-scheme", value: "dark"}]});
  await client.call("Emulation.setTimezoneOverride", {timezoneId: "UTC"});
}

async function navigate(client, url) {
  const loaded = client.waitFor("Page.loadEventFired");
  await client.call("Page.navigate", {url});
  await loaded;
  await evaluate(client, "document.fonts.ready.then(() => true)");
  await evaluate(client, `(() => { const style = document.createElement("style"); style.textContent = "*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}"; document.head.appendChild(style); return true; })()`);
  await new Promise((resolve) => setTimeout(resolve, 250));
}

async function waitForValue(client, expression, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await evaluate(client, expression)) return true;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Browser state did not appear: ${expression}`);
}

async function clickControl(client, {label, text, mobileMenu = false}) {
  const expression = `(() => {
    const controls = [...document.querySelectorAll("a,button,[role=button],tr")].filter((element) => {
      const box = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return box.width > 0 && box.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    });
    const target = controls.find((element) => element.getAttribute("aria-label") === ${JSON.stringify(label || "")})
      || controls.find((element) => (element.textContent || "").trim() === ${JSON.stringify(text || "")})
      || controls.find((element) => (element.textContent || "").includes(${JSON.stringify(text || "")}) && ${mobileMenu ? "element.getBoundingClientRect().left < 90 && element.getBoundingClientRect().top < 110" : "true"});
    if (!target) return false;
    target.click();
    return true;
  })()`;
  if (!(await evaluate(client, expression))) throw new Error(`Control not found: ${label || text}`);
}

async function assertNoScreenshotSurface(client) {
  const snapshot = await evaluate(client, `(() => {
    const elements = [...document.querySelectorAll("img,canvas,video,*")].map((element) => {
      const tag = element.tagName.toLowerCase();
      const style = getComputedStyle(element);
      const box = element.getBoundingClientRect();
      return {
        tag,
        width: box.width,
        height: box.height,
        visible: style.visibility !== "hidden" && style.display !== "none",
        opacity: Number(style.opacity),
        backgroundImage: style.backgroundImage,
        source: element.getAttribute("src") || style.backgroundImage.slice(0, 120),
      };
    });
    return {viewportWidth: innerWidth, viewportHeight: innerHeight, elements};
  })()`);
  const suspect = findScreenshotSurface(
    snapshot.elements,
    snapshot.viewportWidth,
    snapshot.viewportHeight,
  );
  if (suspect) {
    throw new Error(`full-screen image reuse is not an implemented UI: ${suspect.tag} area=${suspect.areaRatio.toFixed(2)}`);
  }
}

async function screenshot(client, filename) {
  const result = await client.call("Page.captureScreenshot", {format: "png", fromSurface: true, captureBeyondViewport: false});
  await writeFile(join(outputDir, filename), Buffer.from(result.data, "base64"));
}

function similarity(reference, actual) {
  try {
    const output = execFileSync("magick", ["compare", "-metric", "RMSE", reference, actual, "null:"], {encoding: "utf8", stdio: ["ignore", "pipe", "pipe"]});
    return similarityFromNormalizedRmse(output || "0 (0)");
  } catch (error) {
    return similarityFromNormalizedRmse(String(error.stderr || ""));
  }
}

const specs = contract.screens;
const errors = [];
const interactionPassed = new Map(specs.map((spec) => [spec.id, false]));
let client;

try {
  client = await createPage(args["cdp-url"]);
  await setViewport(client, specs[0].width, specs[0].height);
  await navigate(client, `${args["base-url"]}/`);
  await waitForValue(client, `document.body.innerText.includes("System pulse") || document.body.innerText.includes("Overview")`);
  await assertNoScreenshotSurface(client);
  await screenshot(client, specs[0].filename);
  interactionPassed.set(specs[0].id, true);

  await clickControl(client, {text: "Runs"});
  await waitForValue(client, `location.pathname.includes("runs")`);
  await clickControl(client, {label: "Status filter", text: "Status"});
  await waitForValue(client, `document.body.innerText.includes("Completed") && document.body.innerText.includes("Failed") && document.body.innerText.includes("Running")`);
  await new Promise((resolve) => setTimeout(resolve, 200));
  await assertNoScreenshotSurface(client);
  await screenshot(client, specs[1].filename);
  interactionPassed.set(specs[1].id, true);

  await clickControl(client, {label: "Run run-4821", text: "run-4821"});
  await waitForValue(client, `location.pathname.includes("run-4821") && document.body.innerText.toLowerCase().includes("run detail")`);
  await assertNoScreenshotSurface(client);
  await screenshot(client, specs[2].filename);
  interactionPassed.set(specs[2].id, true);

  await setViewport(client, specs[3].width, specs[3].height);
  await navigate(client, `${args["base-url"]}/`);
  await clickControl(client, {label: "Open navigation", text: "", mobileMenu: true});
  await waitForValue(client, `document.body.innerText.includes("NAVIGATION") && document.body.innerText.includes("WORKSPACE")`);
  await new Promise((resolve) => setTimeout(resolve, 200));
  await assertNoScreenshotSurface(client);
  await screenshot(client, specs[3].filename);
  interactionPassed.set(specs[3].id, true);
} catch (error) {
  errors.push(String(error.message || error));
} finally {
  client?.close();
  await closeBrowser(args["cdp-url"]);
}

const items = [];
for (const spec of specs) {
  const referencePath = join(args.reference, spec.filename);
  const actualPath = join(outputDir, spec.filename);
  let score = 0;
  if (interactionPassed.get(spec.id)) {
    try {
      score = similarity(referencePath, actualPath);
    } catch (error) {
      errors.push(`${spec.id}: visual comparison failed: ${error.message || error}`);
    }
  }
  items.push({
    id: spec.id,
    expected: `visual similarity >= ${floor.toFixed(2)}`,
    actual: score.toFixed(4),
    correct: interactionPassed.get(spec.id) && score >= floor,
    slices: {interaction: interactionPassed.get(spec.id), visual_similarity: score},
  });
}

const correct = items.filter((item) => item.correct).length;
const report = {
  schema_version: 1,
  passed: errors.length === 0 && correct === items.length,
  summary: {
    evaluation_mode: "oracle_exact_match",
    correct,
    total: items.length,
    browser_version: args["browser-version"],
    visual_metric: "1 - normalized RMSE",
  },
  items,
  errors,
};
await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
for (const item of items) console.log(`${item.correct ? "PASS" : "FAIL"} ${item.id}: ${item.actual} (${item.expected})`);
for (const error of errors) console.error(`ERROR ${error}`);
process.exit(report.passed ? 0 : 1);
