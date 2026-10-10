/**
 * End-to-end check of pinch-to-zoom on the board (same setup as e2e/navigation.mjs).
 *   node e2e/pinch.mjs
 */
import { chromium } from 'playwright-core';

const BASE = process.env.E2E_URL ?? 'http://localhost:3000';
const b = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
let pass = 0, fail = 0; const errs = [];
const check = (name, ok, extra = '') => { (ok ? pass++ : fail++); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : '  ' + extra}`); };
const ctx = await b.newContext({ viewport: { width: 390, height: 800 }, hasTouch: true, isMobile: true });
const pg = await ctx.newPage();
pg.on('pageerror', e => errs.push(e.message));
await pg.goto(`${BASE}/`); await pg.waitForSelector('.home .modes');
await pg.click('[data-mode=human_vs_agent]'); await pg.waitForSelector('.board');
await pg.waitForTimeout(400);
const cdp = await ctx.newCDPSession(pg);
const cell = () => pg.evaluate(() => document.querySelector('.cell').getBoundingClientRect().width);
const box = await pg.evaluate(() => { const r = document.getElementById('board-scroll').getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; });
const pinch = async (from, to, steps = 8) => {
  const pts = g => [{ x: box.x - g / 2, y: box.y }, { x: box.x + g / 2, y: box.y }];
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: pts(from) });
  for (let i = 1; i <= steps; i++) await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: pts(from + (to - from) * i / steps) });
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await pg.waitForTimeout(150);
};
check('1 touch screens have no +/- zoom buttons', await pg.evaluate(() => ['zoom-in', 'zoom-out'].every(a => getComputedStyle(document.querySelector(`[data-act=${a}]`)).display === 'none')));
check('2 the recentre button stays', await pg.evaluate(() => getComputedStyle(document.querySelector('[data-act=center]')).display !== 'none'));
const c0 = await cell();
await pinch(100, 200);
const c1 = await cell();
check('3 spreading two fingers zooms in', c1 > c0 * 1.5, `${c0} -> ${c1}`);
await pinch(200, 60);
const c2 = await cell();
check('4 pinching in zooms back out', c2 < c1 * 0.6, `${c1} -> ${c2}`);
await pinch(300, 20, 12); await pinch(300, 20, 12);
const small = await cell();
check('5 zoom out has a floor', small >= 20, String(small));
await pinch(20, 400, 12); await pinch(20, 400, 12); await pinch(20, 400, 12);
const big = await cell();
check('6 zoom in has a ceiling', big <= 100 && big > c0, String(big));
check('7 the page itself did not zoom', await pg.evaluate(() => (window.visualViewport?.scale ?? 1) === 1));
await pg.tap('.rack .slot:not(.used)'); await pg.waitForTimeout(150);
check('8 tapping still selects a tile afterwards', !!(await pg.$('.rack .slot.selected')));
check('9 no page errors', errs.length === 0, errs.join('; '));
console.log(`${pass} passed, ${fail} failed`); await b.close(); process.exit(fail ? 1 : 0);
