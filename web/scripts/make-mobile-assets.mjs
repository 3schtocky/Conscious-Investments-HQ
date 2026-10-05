// Renders the Home Screen icons and iPhone launch screens from public/logo-mark.png.
// Needs a Chrome and puppeteer-core:  cd web && npm i --no-save puppeteer-core && node scripts/make-mobile-assets.mjs
import puppeteer from "puppeteer-core";
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const mark = `data:image/png;base64,${readFileSync(resolve(root, "public/logo-mark.png")).toString("base64")}`;
const CHROME = process.env.CHROME ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const INK = "#2E2C29", PAPER = "#ECEAE1", DARK = "#1b1a18", CLAY = "#d27030";

// Launch screens: iPhone 12 mini / 13 mini, 12 to 14, 15 / 16, 16 Pro, 14 Pro Max era, 15 Plus / 16 Plus, 16 Pro Max.
const SPLASH = [[375, 812], [390, 844], [393, 852], [402, 874], [428, 926], [430, 932], [440, 956]];

const icon = (px, scale, bg) => `<body style="margin:0;width:${px}px;height:${px}px;background:${bg};display:grid;place-items:center">
  <div style="width:${px * scale}px;height:${px * scale}px;background:${INK};-webkit-mask:url(${mark}) center/contain no-repeat;mask:url(${mark}) center/contain no-repeat"></div></body>`;

const splash = (w, h) => `<link href="https://fonts.googleapis.com/css2?family=Silkscreen:wght@400;700&display=swap" rel="stylesheet">
<body style="margin:0;width:${w}px;height:${h}px;background:${DARK};display:flex;flex-direction:column;align-items:center;justify-content:center;gap:${w * 0.045}px">
  <div style="width:${w * 0.3}px;height:${w * 0.3}px;background:${PAPER};-webkit-mask:url(${mark}) center/contain no-repeat;mask:url(${mark}) center/contain no-repeat"></div>
  <div style="font:700 ${w * 0.036}px/1 Silkscreen,monospace;color:${PAPER};letter-spacing:${w * 0.002}px">CONSCIOUS INVESTMENTS <span style="color:${CLAY}">HQ</span></div>
</body>`;

const browser = await puppeteer.launch({ executablePath: CHROME, headless: "new", args: ["--no-sandbox"] });
const page = await browser.newPage();
async function shot(html, w, h, file) {
  await page.setViewport({ width: w, height: h, deviceScaleFactor: 1 });
  await page.setContent(html, { waitUntil: "load", timeout: 60000 });
  await Promise.race([page.evaluate(() => document.fonts.ready), new Promise((r) => setTimeout(r, 6000))]);   // the launch screens use Silkscreen from Google Fonts
  mkdirSync(dirname(resolve(root, file)), { recursive: true });
  writeFileSync(resolve(root, file), await page.screenshot({ type: "png" }));
  console.log("wrote", file);
}
await shot(icon(192, 0.8, PAPER), 192, 192, "public/icon-192.png");
await shot(icon(512, 0.8, PAPER), 512, 512, "public/icon-512.png");
await shot(icon(512, 0.56, PAPER), 512, 512, "public/icon-maskable-512.png");   // keeps the mark inside the 80% safe zone
for (const [w, h] of SPLASH) await shot(splash(w * 3, h * 3), w * 3, h * 3, `public/splash/${w}x${h}.png`);
await browser.close();
