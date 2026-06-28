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
//   WARN  = cross-origin broken/slow image (YouTube/CDN hiccup), pending images.
//
// Resilience: a page that fails is re-checked once; only failures that REPRODUCE
// on both attempts are reported. This kills transient CI/network blips (e.g. a
// one-off slow utils.js load) without hiding deterministic bugs.
//
// Exit 1 on any reproduced failure; writes `details`/`ok` to $GITHUB_OUTPUT.

import { chromium } from 'playwright';
import fs from 'fs';

const BASE = (process.env.BASE_URL || 'https://thetorahpodcast.net').replace(/\/$/, '');
const PAGES = (process.env.PAGES || '/').split(',').map(s => s.trim()).filter(Boolean);
const BASE_ORIGIN = new URL(BASE).origin;

function sameOrigin(u) {
  try { return new URL(u, BASE).origin === BASE_ORIGIN; } catch { return false; }
}

async function checkPage(ctx, path) {
  const url = BASE + path;
  const failures = [];
  const warnings = [];
  const page = await ctx.newPage();
  const badResponses = [];
  const pageErrors = [];

  page.on('response', r => { if (r.status() >= 400) badResponses.push({ url: r.url(), status: r.status() }); });
  page.on('pageerror', e => pageErrors.push(String((e && e.message) || e)));
  page.on('requestfailed', r => {
    const f = r.failure();
    if (f && !/ERR_ABORTED/.test(f.errorText)) badResponses.push({ url: r.url(), status: f.errorText });
  });

  try {
    const resp = await page.goto(url, { waitUntil: 'networkidle', timeout: 45000 });
    if (!resp || resp.status() >= 400) {
      failures.push(`${path}: page returned ${resp ? resp.status() : 'no response'}`);
      await page.close();
      return { failures, warnings };
    }
  } catch (e) {
    failures.push(`${path}: navigation error: ${e.message}`);
    await page.close();
    return { failures, warnings };
  }

  // Render lists, then scroll to trigger lazy images so we actually exercise them.
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

  const imgs = await page.evaluate(() => [...document.images].map(im => ({
    src: im.currentSrc || im.src || '',
    hasSrc: !!im.getAttribute('src'),
    complete: im.complete,
    nw: im.naturalWidth,
  })));
  for (const im of imgs) {
    if (!im.hasSrc) continue;                 // src stripped by onerror = intentional placeholder
    if (im.complete && im.nw > 0) continue;   // loaded fine
    const so = sameOrigin(im.src);
    if (im.complete && im.nw === 0) (so ? failures : warnings).push(`${path}: broken image -> ${im.src}`);
    else warnings.push(`${path}: image still loading -> ${im.src}`);
  }

  for (const br of badResponses) {
    const msg = `${path}: ${br.status} -> ${br.url}`;
    (sameOrigin(br.url) ? failures : warnings).push(msg);
  }

  for (const pe of pageErrors) failures.push(`${path}: uncaught JS error: ${pe}`);

  if (path === '/') {
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
  return { failures, warnings };
}

const browser = await chromium.launch();
const ctx = await browser.newContext({ userAgent: 'site-health-visual (+monitoring)' });

const allFailures = [];
const allWarnings = [];

for (const path of PAGES) {
  let res = await checkPage(ctx, path);
  if (res.failures.length) {
    // Re-check once; keep only failures that reproduce (kills transient blips).
    const res2 = await checkPage(ctx, path);
    const set2 = new Set(res2.failures);
    const reproduced = res.failures.filter(f => set2.has(f));
    if (reproduced.length) allFailures.push(...reproduced);
    const flaky = res.failures.filter(f => !set2.has(f));
    if (flaky.length) allWarnings.push(...flaky.map(f => `[transient, not reproduced] ${f}`));
    allWarnings.push(...res2.warnings);
  } else {
    allWarnings.push(...res.warnings);
  }
}

await browser.close();

if (allWarnings.length) {
  const shown = allWarnings.slice(0, 8).map(w => '- ' + w).join('\n');
  const more = allWarnings.length > 8 ? `\n  …and ${allWarnings.length - 8} more` : '';
  console.log(`WARNINGS (non-blocking, ${allWarnings.length}):\n${shown}${more}\n`);
}

const out = process.env.GITHUB_OUTPUT;
if (allFailures.length) {
  const details = allFailures.map(f => '- ' + f).join('\n');
  console.log('VISUAL HEALTH FAILED:\n' + details);
  if (out) fs.appendFileSync(out, `details<<EOF\n${details}\nEOF\nok=false\n`);
  process.exitCode = 1;
} else {
  console.log(`Visual check passed for ${PAGES.length} page(s).`);
  if (out) fs.appendFileSync(out, 'ok=true\n');
}
