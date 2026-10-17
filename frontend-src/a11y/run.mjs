// Accessibility audit (spec 14.2, B26): axe-core WCAG 2.1 A/AA on every panel view, light
// and dark theme, desktop and 360 px. Needs Chrome/Chromium; no npm dependencies besides
// axe-core. Exit code 1 on any violation.
//   node a11y/run.mjs [path-to-chrome]

import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { createServer } from "node:http";
import { extname, join, resolve } from "node:path";

const ROOT = resolve(import.meta.dirname, "../..");
const CHROME = process.argv[2] || process.env.CHROME || "google-chrome";
const VIEWS = ["overview", "doctor", "history", "autopilot", "map", "network", "diagnostics", "settings", "detail"];
const VARIANTS = [{ w: 1280, dark: false }, { w: 1280, dark: true }, { w: 360, dark: false }];
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript" };

const server = createServer((req, res) => {
  const path = join(ROOT, decodeURIComponent(new URL(req.url, "http://x").pathname));
  let body;
  try {
    body = readFileSync(path);
  } catch {
    res.writeHead(404).end();
    return;
  }
  res.writeHead(200, { "Content-Type": TYPES[extname(path)] ?? "text/plain" });
  res.end(body);
}).listen(0, "127.0.0.1");
await new Promise((r) => server.once("listening", r));
const port = server.address().port;

const profile = mkdtempSync(join(tmpdir(), "sentinel-a11y-"));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--remote-debugging-port=0", "--user-data-dir=" + profile, "about:blank"], { stdio: ["ignore", "ignore", "pipe"] });
const wsUrl = await new Promise((ok, fail) => {
  chrome.stderr.on("data", (d) => { const m = /DevTools listening on (ws:\/\/\S+)/.exec(String(d)); if (m) ok(m[1]); });
  setTimeout(() => fail(new Error("chrome did not start")), 15000);
});
const browser = new WebSocket(wsUrl);
await new Promise((r) => browser.addEventListener("open", r));
let id = 0;
const pending = new Map();
browser.addEventListener("message", (e) => {
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
});
const send = (method, params = {}, sessionId) => new Promise((r) => { const i = ++id; pending.set(i, r); browser.send(JSON.stringify({ id: i, method, params, sessionId })); });
const { result: { targetId } } = await send("Target.createTarget", { url: "about:blank" });
const { result: { sessionId } } = await send("Target.attachToTarget", { targetId, flatten: true });
const page = (m, p) => send(m, p, sessionId);
const axe = readFileSync(join(ROOT, "frontend-src/node_modules/axe-core/axe.min.js"), "utf8");

let failures = 0;
for (const v of VARIANTS) {
  await page("Emulation.setDeviceMetricsOverride", { width: v.w, height: 900, deviceScaleFactor: 1, mobile: v.w < 600 });
  for (const view of VIEWS) {
    const route = view === "detail" ? "doctor" : view;
    await page("Page.navigate", { url: `http://127.0.0.1:${port}/frontend-src/a11y/harness.html?view=${route}${v.dark ? "&dark=1" : ""}` });
    await new Promise((r) => setTimeout(r, 900));
    if (view === "detail") {
      await page("Runtime.evaluate", { expression: "window.__panel.shadowRoot.querySelector('.item').click()" });
      await new Promise((r) => setTimeout(r, 500));
    }
    if (process.env.A11Y_SELF_CHECK) {
      await page("Runtime.evaluate", { expression: `(() => { const r = window.__panel.shadowRoot;
        const d = document.createElement("div"); d.innerHTML = '<img src="x.png"><button></button><p style="color:#bbb;background:#fff">low contrast</p>';
        r.querySelector("main").appendChild(d); })()` });
    }
    await page("Runtime.evaluate", { expression: axe });
    const { result } = await page("Runtime.evaluate", {
      awaitPromise: true, returnByValue: true,
      expression: `axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] })
        .then(r => r.violations.map(v => ({ id: v.id, impact: v.impact, nodes: v.nodes.slice(0, 3).map(n => n.target.join(" ") + " :: " + (n.failureSummary || "").split("\\n").slice(1, 2).join("")) })))`,
    });
    const violations = result.result.value || [];
    const label = `${view.padEnd(11)} ${v.w}px ${v.dark ? "dark " : "light"}`;
    if (violations.length) {
      failures += violations.length;
      console.log(`✖ ${label}`);
      for (const x of violations) console.log(`    ${x.impact} ${x.id}\n      ${x.nodes.join("\n      ")}`);
    } else {
      console.log(`✔ ${label}`);
    }
  }
}
browser.close();
chrome.kill();
await new Promise((r) => chrome.once("exit", r));
server.close();
rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
console.log(failures ? `\n${failures} violation(s)` : "\nNo WCAG 2.1 AA violations found by axe-core");
process.exit(failures ? 1 : 0);
