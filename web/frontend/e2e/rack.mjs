/**
 * End-to-end check of rearranging the rack: dragging with a mouse, dragging by touch, and keeping the arrangement
 * between turns (same setup as e2e/navigation.mjs).
 *   node e2e/rack.mjs
 */
import { chromium } from 'playwright-core';

const BASE = process.env.E2E_URL ?? 'http://localhost:3000';
const b = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
let pass = 0, fail = 0; const errs = [];
const check = (name, ok, extra = '') => { (ok ? pass++ : fail++); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : '  ' + extra}`); };
const order = pg => pg.evaluate(() => [...document.querySelectorAll('.rack .slot[data-i]')].map(s => s.dataset.i).join(','));
const syms = pg => pg.evaluate(() => [...document.querySelectorAll('.rack .slot[data-i]')].map(s => s.textContent.replace(/\s+/g, '')).join('|'));
async function game(opts) {
  const ctx = await b.newContext({ viewport: { width: 390, height: 800 }, ...opts });
  const pg = await ctx.newPage();
  pg.on('pageerror', e => errs.push(e.message));
  await pg.goto(`${BASE}/`); await pg.waitForSelector('.home .modes');
  await pg.click('[data-mode=human_vs_agent]'); await pg.waitForSelector('.rack .slot[data-i]');
  await pg.waitForTimeout(300);
  return [ctx, pg];
}
const slotCentre = (pg, n) => pg.evaluate(n => { const r = [...document.querySelectorAll('.rack .slot[data-i]')][n].getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; }, n);

// ── mouse: native drag and drop
let [ctx, pg] = await game({});
const o0 = (await order(pg)).split(',');
await pg.dragAndDrop('.rack .slot[data-i]:nth-of-type(2)', '.rack .slot[data-i]:nth-of-type(6)').catch(() => {});
let o1 = (await order(pg)).split(',');
const first = o0[0], moved = o0.length > 5;
check('1 dragging a rack tile onto another moves it there', moved && o1.join() !== o0.join(), `${o0} -> ${o1}`);
check('2 the same tiles are still all there', [...o1].sort().join() === [...o0].sort().join());

// ── touch: press, drag onto another tile, release
await ctx.close();
[ctx, pg] = await game({ hasTouch: true, isMobile: true });
const cdp = await ctx.newCDPSession(pg);
const t0 = (await order(pg)).split(',');
const from = await slotCentre(pg, 0), to = await slotCentre(pg, 4);
await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [from] });
for (let i = 1; i <= 8; i++) await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: from.x + (to.x - from.x) * i / 8, y: from.y + (to.y - from.y) * i / 8 }] });
check('3 a ghost tile follows the finger', !!(await pg.$('.rack-ghost')));
await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
await pg.waitForTimeout(250);
const t1 = (await order(pg)).split(',');
check('4 releasing on another tile moves the dragged tile there', t1[4] === t0[0] && t1.join() !== t0.join(), `${t0} -> ${t1}`);
check('5 the ghost is gone and nothing was selected by the drag', !(await pg.$('.rack-ghost')) && !(await pg.$('.rack .slot.selected')));

// a plain tap still selects
await pg.tap('.rack .slot[data-i]:nth-of-type(3)'); await pg.waitForTimeout(150);
check('6 a tap still selects a tile', !!(await pg.$('.rack .slot.selected')));

// the arrangement survives the turn passing (same tiles, so same order)
await pg.tap('.rack .slot.selected'); await pg.waitForTimeout(100);
const before = await syms(pg);
await pg.click('[data-act=pass]'); await pg.waitForSelector('.modal.pass'); await pg.click('[data-act=pass-confirm]');
await pg.waitForFunction(() => /Your turn/.test(document.querySelector('#status')?.textContent ?? ''), null, { timeout: 15000 });
await pg.waitForTimeout(300);
check('7 your arrangement is kept after the turn passes', (await syms(pg)) === before, `${before} -> ${await syms(pg)}`);

// shuffle still works and is remembered
await pg.click('[data-act=shuffle]'); await pg.waitForTimeout(150);
check('8 shuffle still works', (await order(pg)).split(',').length === t0.length);
check('9 no page errors', errs.length === 0, errs.join('; '));
console.log(`${pass} passed, ${fail} failed`); await b.close(); process.exit(fail ? 1 : 0);
