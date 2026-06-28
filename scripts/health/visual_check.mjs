// Headless visual/UX health check for a live static site.
// Loads each page in a real browser and fails on regressions that pure HTTP
// probes can't see: broken images (loaded but naturalWidth==0), uncaught JS
// errors, same-origin 404s, and empty client-rendered lists.
//
// Env:
//   BASE_URL  e.g. https://thetorahpodcast.net
//   PAGES     comma-separated paths, e.g. "/,/rabbins.html,/paracha.html"
//
// Failure model (kept false-positive-free on purpose):
//   FAIL  = page !2xx, uncaught JS error, same-origin 4xx/5xx response,
//           same-origin image that LOADED but failed to decode (naturalWidth==0),
//           or a page-specific render assertion (e.g. rabbi bubble with no image).
//   WARN  = cross-origin broken/slow image (YouTube/CDN hiccup), slow same-origin
//           image still pending after settle. Never blocks.
//
// Exit 1 on any failure; writes `details`/`ok` to $GITHUB_OUTPUT.

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
  page.on('pageerror', e => pageErrors.push(String((e && e.message) || e)));   // uncaught JS exception
  page.on('requestfailed', r => {
    const f = r.failure();
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

  // Let client JS render lists, then scroll to trigger lazy-loaded images so we
  // actually exercise them instead of leaving them un-requested off-screen.
  await page.waitForTimeout(1500);
  await page.evaluate(async () => {
    for (let y = 0; y <= document.body.scrollHeight; y += window.innerHeight) {
      window.scrollTo(0, y);
      await new Promise(r => setTimeout(r, 120));
    }
    window.scrollTo(0, 0);
  });
  try { await page.waitForLoadState('networkidle', { timeout: 15000 }); } catch {}
  await page.waitForTimeout(800);

  // Image health: distinguish loaded-but-broken from still-pending (lazy/slow).
  const imgs = await page.evaluate(() => [...document.images].map(im => ({
    src: im.currentSrc || im.src || '',
    hasSrc: !!im.getAttribute('src'),
    complete: im.complete,
    nw: im.naturalWidth,
  })));
  for (const im of imgs) {
    if (!im.hasSrc) continue;                      // src stripped by onerror = intentional placeholder
    if (im.complete && im.nw > 0) continue;        // loaded fine
    const so = sameOrigin(im.src);
    if (im.complete && im.nw === 0) {
      // Definitively failed to load/decode.
      (so ? failures : warnings).push(`${path}: broken image -> ${im.src}`);
    } else {
      // Still pending after settle: treat as slow, warn only (avoids flaky fails).
      warnings.push(`${path}: image still loading -> ${im.src}`);
    }
  }

  // 4xx/5xx and network failures observed while loading the page.
  for (const br of badResponses) {
    const msg = `${path}: ${br.status} -> ${br.url}`;
    (sameOrigin(br.url) ? failures : warnings).push(msg);
  }

  // Uncaught JS exceptions (the "stuck on Chargement…" class of incident).
  for (const pe of pageErrors) failures.push(`${path}: uncaught JS error: ${pe}`);

  // Page-specific render assertions.
  if (path === '/' ) {
    const sections = await page.evaluate(() =>
      document.querySelectorAll('#app .channel, #app .channel-header, #app section').length);
    if (sections === 0) failures.push(`${path}: homepage rendered no channel sections (render broken?)`);
  }
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

if (warnings.length) {
  const shown = warnings.slice(0, 6).map(w => '- ' + w).join('\n');
  const more = warnings.length > 6 ? `\n  …and ${warnings.length - 6} more` : '';
  console.log(`WARNINGS (non-blocking, ${warnings.length}):\n${shown}${more}\n`);
}

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
