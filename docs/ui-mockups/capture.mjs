/**
 * Capture each mockup tab as JPEG (dark theme).
 *
 * Prerequisites:
 *   - Mockup server:  cd docs/ui-mockups && python3 -m http.server 8765 --bind 0.0.0.0
 *   - Playwright:     npm i playwright && npx playwright install firefox
 *
 * Usage:
 *   MOCKUP_URL=http://127.0.0.1:8765/index.html node capture.mjs
 */
import { firefox } from "playwright";
import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASE = process.env.MOCKUP_URL || "http://127.0.0.1:8765/index.html";
const OUT = process.env.OUT_DIR || path.join(__dirname, "screenshots");

const SLIDES = [
  "sign-in",
  "chat",
  "models",
  "model-detail",
  "knowledge",
  "agents",
  "usage",
];

const browser = await firefox.launch({ headless: true });
const page = await browser.newPage({
  viewport: { width: 1280, height: 900 },
  deviceScaleFactor: 2,
  colorScheme: "dark",
});

await page.goto(BASE, { waitUntil: "networkidle" });
await page.addStyleTag({
  content: `
    .deck-header, .deck-footer, .slide-label, .actions { display: none !important; }
    .deck-main { padding: 24px !important; gap: 0 !important; }
    .slide { margin: 0 !important; }
    body.deck { background: #06070a !important; }
    :root {
      color-scheme: dark !important;
      --surface-0: #0a0b0f !important;
      --surface-1: #12141a !important;
      --surface-2: #1a1d26 !important;
      --border: #262a35 !important;
      --border-strong: #333846 !important;
      --text: #e8eaf0 !important;
      --text-muted: #9aa1b1 !important;
      --text-faint: #6b7280 !important;
      --accent: #6d8cff !important;
      --accent-soft: rgba(109, 140, 255, 0.12) !important;
      --private: #4ade80 !important;
      --warning: #fbbf24 !important;
      --danger: #f87171 !important;
    }
  `,
});

fs.mkdirSync(OUT, { recursive: true });
for (const id of SLIDES) {
  const slide = page.locator(`#${id}`);
  await slide.scrollIntoViewIfNeeded();
  await page.waitForTimeout(200);
  const dest = path.join(OUT, `${id}.jpg`);
  await slide.locator(".browser").screenshot({
    path: dest,
    type: "jpeg",
    quality: 92,
  });
  console.log("wrote", dest);
}

await browser.close();
