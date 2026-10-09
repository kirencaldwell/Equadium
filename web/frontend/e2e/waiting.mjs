/**
 * End-to-end check that you can try tiles while it is the opponent's turn (but not submit them), and
 * that they are cleared when the opponent moves. Same setup as e2e/navigation.mjs.
 *   node e2e/waiting.mjs
 */
import { chromium } from 'playwright-core';

const BASE = process.env.E2E_URL ?? 'http://localhost:3000';
const b = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
let pass = 0, fail = 0; const errs = [];
const check = (name, ok, extra = '') => { (ok ? pass++ : fail++); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : '  ' + extra}`); };
async function page(name) {
  const ctx = await b.newContext({ viewport: { width: 390, height: 800 } });
  const pg = await ctx.newPage();
  pg.on('pageerror', e => errs.push(e.message));
  await pg.goto(`${BASE}/`); await pg.waitForSelector('.home .modes');
  await pg.click('[data-act=online]'); await pg.waitForSelector('.online-title');
  await pg.fill('#name', name);
  return pg;
}
const text = (pg, sel) => pg.evaluate(s => document.querySelector(s)?.textContent?.replace(/\s+/g, ' ').trim() ?? '', sel);

const a = await page('Ann');
await a.click('[data-act=online-create]'); await a.waitForSelector('.lobby');
const code = (await a.evaluate(() => location.hash)).split('/')[2];
const bb = await page('Bob');
await bb.fill('#code', code); await bb.click('[data-act=online-join]'); await bb.waitForSelector('.board');
await a.waitForSelector('.board'); await a.waitForTimeout(1800);

const turn = async pg => /Your turn/.test(await text(pg, '#status'));
const mover = (await turn(a)) ? a : bb, waiter = mover === a ? bb : a;
check('0 exactly one player is to move', (await turn(a)) !== (await turn(bb)));

// the waiting player picks a tile and puts it next to the centre tile
const centre = '.cell.start';
await waiter.click('.rack .slot:not(.used)'); await waiter.waitForTimeout(150);
check('1 a tile can be picked when it is not your turn', !!(await waiter.$('.rack .slot.selected')));
const target = await waiter.evaluate(() => { const c = document.querySelector('.cell.adj'); return c ? `.cell[data-r="${c.dataset.r}"][data-c="${c.dataset.c}"]` : null; });
await waiter.click(target); await waiter.waitForTimeout(500);
check('2 it can be placed on the board', !!(await waiter.$('.tile.pending')));
check('3 the Play button stays disabled', await waiter.evaluate(() => document.querySelector('[data-act=play]').disabled));
check('4 Swap and Pass stay disabled', await waiter.evaluate(() => ['swap', 'pass'].every(a => document.querySelector(`[data-act=${a}]`).disabled)));
check('5 Recall works', !(await waiter.evaluate(() => document.querySelector('[data-act=recall]').disabled)));

// the mover plays something so the board changes under the waiting player: a lone '=' is not legal, so pass
await mover.click('[data-act=pass]'); await mover.waitForSelector('.modal.pass'); await mover.click('[data-act=pass-confirm]');
await waiter.waitForFunction(() => /Your turn/.test(document.querySelector('#status')?.textContent ?? ''), null, { timeout: 8000 });
check('6 pending tiles are cleared when the opponent moves', !(await waiter.$('.tile.pending')));
check('7 the tile is back in the rack', !(await waiter.$('.rack .slot.used')));
check('8 no page errors', errs.length === 0, errs.join('; '));
console.log(`${pass} passed, ${fail} failed`); await b.close(); process.exit(fail ? 1 : 0);
