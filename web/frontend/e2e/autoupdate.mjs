/**
 * End-to-end check that an online game updates by itself, cheaply (same setup as e2e/navigation.mjs).
 *   node e2e/autoupdate.mjs
 */
import { chromium } from 'playwright-core';

const BASE = process.env.E2E_URL ?? 'http://localhost:3000';
const b = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
let pass = 0, fail = 0;
const check = (name, ok, extra = '') => { (ok ? pass++ : fail++); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : '  ' + extra}`); };
const polls = new WeakMap();
async function page(name) {
  const ctx = await b.newContext({ viewport: { width: 390, height: 800 } });
  const pg = await ctx.newPage();
  polls.set(pg, 0);
  pg.on('request', r => { if (/\/rooms\/[A-Z0-9]{5}\?since=/.test(r.url())) polls.set(pg, polls.get(pg) + 1); });
  await pg.goto(`${BASE}/`); await pg.waitForSelector('.home .modes');
  await pg.click('[data-act=online]'); await pg.waitForSelector('.online-title');
  await pg.fill('#name', name);
  return pg;
}
const turn = async pg => /Your turn/.test(await pg.evaluate(() => document.querySelector('#status')?.textContent ?? ''));

const a = await page('Ann');
await a.click('[data-act=online-create]'); await a.waitForSelector('.lobby');
const code = (await a.evaluate(() => location.hash)).split('/')[2];
const bb = await page('Bob');
await bb.fill('#code', code); await bb.click('[data-act=online-join]'); await bb.waitForSelector('.board');
await a.waitForSelector('.board'); await a.waitForTimeout(2500);
const mover = (await turn(a)) ? a : bb, waiter = mover === a ? bb : a;

// cost: count polls over 10 s on each side
polls.set(mover, 0); polls.set(waiter, 0);
await a.waitForTimeout(10000);
check('1 the player to move polls slowly (<= 3 requests in 10 s)', polls.get(mover) <= 3, String(polls.get(mover)));
check('2 the waiting player polls often enough to feel live (>= 3 in 10 s)', polls.get(waiter) >= 3, String(polls.get(waiter)));
check('3 ...but not wastefully (<= 7 in 10 s)', polls.get(waiter) <= 7, String(polls.get(waiter)));

// the opponent's move shows up without a refresh
await mover.click('[data-act=pass]'); await mover.waitForSelector('.modal.pass'); await mover.click('[data-act=pass-confirm]');
await waiter.waitForFunction(() => /Your turn/.test(document.querySelector('#status')?.textContent ?? ''), null, { timeout: 6000 });
check('4 the opponent\'s move appears with no refresh', await turn(waiter));

// a tab coming back to the foreground checks at once: simulate a hidden tab by pausing nothing, just fire the event
const before = polls.get(waiter);
await waiter.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
await waiter.waitForTimeout(400);
check('5 returning to the tab triggers an immediate check', polls.get(waiter) > before, `${before} -> ${polls.get(waiter)}`);
console.log(`${pass} passed, ${fail} failed`); await b.close(); process.exit(fail ? 1 : 0);
