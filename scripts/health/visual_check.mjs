// Headless visual/UX health check for a live static site.
// Loads each page in a real browser and fails on regressions that pure HTTP
// probes can't see: broken images (naturalWidth==0), uncaught JS errors,
// same-origin 404s, and empty client-rendered lists.
//
// Env:
//   BASE_URL  e.g. https://thetorahpodcast.net
//   PAGES     comma-separated paths, e.g. "/,/rabbins.html,/paracha.html"
//   MIN_IMAGES (optional) minimum loaded <img> expected per page (default 0)
//
// Same-origin breakage => hard FAILURE. Cross-origin (YouTube thumbs, CDN) =>
// WARNING only, so a transient ytimg hiccup never pages the owner at 3am.
//
// Exit 1 on any failure; writes a `details`/`ok` pair to $GITHUB_OUTPUT.

import { chromium } from 'playwright';
import fs from 'fs';

const BASE = (process.env.BASE_URL || 'https://thetorahpodcast.net').replace(/\/$/, '');
const PAGES = (process.env.PAGES || '/').split(',').map(s => s.trim()).filter(Boolean);
const BASE_ORIGIN = new URL(BASE).origin;

const failures = [];
const warnings = [];

function sameOrigin(u) {
  try { return new URL(u, BASE).origin === BASE_ORIGIN; } catch { return false; }
}

const browser = await chromium.launch();
const ctx = await browser.newContext({ userAgent: 'site-health-visual (+monitoring)' });

for (const path of PAGES) {
  const url = BASE + path;
  const page = await ctx.newPage();
  const badResponses = [];
  const pageErrors = [];

  page.on('response', r => { if (r.status() >= 400) badResponses.push({ url: r.url(), status: r.status() }); });
  page.on('pageerror', e => pageErrors.push(String(e && e.message || e)));   // uncaught JS exception
  page.on('requestfailed', r => {
    const f = r.failure();
    // Aborted/blocked requests are noise; only count genuine network failures.
    if (f && !/ERR_ABORTED/.test(f.errorText)) badResponses.push({ url: r.url(), status: f.errorText });
  });

  try {
    const resp = await page.goto(url, { waitUntil: 'networkidle', timeout: 45000 });
    if (!resp || resp.status() >= 400) {
      failures.push(`${path}: page returned ${resp ? resp.status() : 'no response'}`);
      await page.close();
      continue;
    }
  } catch (e) {
    failures.push(`${path}: navigation error: ${e.message}`);
    await page.close();
    continue;
  }

  // Let client-side JS finish rendering lists (bubbles, channel sections).
  await page.waitForTimeout(1800);

  // Broken images: rendered but failed to decode.
  const imgs = await page.evaluate(() => [...document.images].map(im => ({
    src: im.currentSrc || im.src || '',
    hasSrc: !!im.getAttribute('src'),
    ok: im.complete && im.naturalWidth > 0,
  })));
  let loaded = 0;
  for (const im of imgs) {
    if (im.ok) { loaded++; continue; }
    if (!im.hasSrc) continue; // src stripped by onerror = intentional placeholder, not a regression
    const msg = `${path}: broken image -> ${im.src}`;
    (sameOrigin(im.src) ? failures : warnings).push(msg);
  }

  const minImg = parseInt(process.env.MIN_IMAGES || '0', 10);
  if (minImg > 0 && loaded < minImg) {
    failures.push(`${path}: only ${loaded} images loaded (expected >= ${minImg}) - render likely broken`);
  }

  // 4xx/5xx and network failures observed while loading the page.
  for (const br of badResponses) {
    const msg = `${path}: ${br.status} -> ${br.url}`;
    (sameOrigin(br.url) ? failures : warnings).push(msg);
  }

  // Uncaught JS exceptions (the "stuck on Chargement…" class of incident).
  for (const pe of pageErrors) failures.push(`${path}: uncaught JS error: ${pe}`);

  // Page-specific render assertions.
  if (path === '/rabbins.html') {
    const n = await page.evaluate(() => document.querySelectorAll('.bubble-img').length);
    if (n === 0) failures.push(`${path}: no rabbi bubbles rendered`);
    const gray = await page.evaluate(() =>
      [...document.querySelectorAll('.bubble-img')].filter(im => !im.getAttribute('src')).length);
    if (gray > 0) failures.push(`${path}: ${gray} rabbi bubble(s) with no image (gray placeholder)`);
  }

  await page.close();
}

await browser.close();

if (warnings.length) console.log('WARNINGS (non-blocking):\n' + warnings.map(w => '- ' + w).join('\n') + '\n');

const out = process.env.GITHUB_OUTPUT;
if (failures.length) {
  const details = failures.map(f => '- ' + f).join('\n');
  console.log('VISUAL HEALTH FAILED:\n' + details);
  if (out) fs.appendFileSync(out, `details<<EOF\n${details}\nEOF\nok=false\n`);
  process.exitCode = 1;
} else {
  console.log(`Visual check passed for ${PAGES.length} page(s).`);
  if (out) fs.appendFileSync(out, 'ok=true\n');
}
