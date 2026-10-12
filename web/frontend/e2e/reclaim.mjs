/**
 * End-to-end check of getting a guest's seat back: (1) a seat link restores the seat in a fresh browser,
 * (2) a guest who lost their token asks the host, who approves, and (3) a denied request does not.
 * Same setup as e2e/waiting.mjs.
 *   node e2e/reclaim.mjs
 */
import { chromium } from 'playwright-core';

const BASE = process.env.E2E_URL ?? 'http://localhost:3000';
const b = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
let pass = 0, fail = 0; const errs = [];
const check = (name, ok, extra = '') => { (ok ? pass++ : fail++); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : '  ' + extra}`); };
async function page(name, ctxOpts = {}) {
  const ctx = await b.newContext({ viewport: { width: 390, height: 800 }, ...ctxOpts });
  const pg = await ctx.newPage();
  pg.on('pageerror', e => errs.push(e.message));
  await pg.goto(`${BASE}/`); await pg.waitForSelector('.home .modes');
  await pg.click('[data-act=online]'); await pg.waitForSelector('.online-title');
  await pg.fill('#name', name);
  return pg;
}

const host = await page('Ann');
await host.click('[data-act=online-create]'); await host.waitForSelector('.lobby');
const code = (await host.evaluate(() => location.hash)).split('/')[2];
const guest = await page('Bob');
await guest.fill('#code', code); await guest.click('[data-act=online-join]'); await guest.waitForSelector('.board');
await host.waitForSelector('.board');

// 1. seat link: grab the token, open it in a brand new browser
const token = await guest.evaluate(() => JSON.parse(localStorage.getItem('equadium.online')).token);
check('0 guest has a seat-link button', !!(await guest.$('[data-act=copy-seat-link]')));
const fresh = await b.newContext({ viewport: { width: 390, height: 800 } });
const viaLink = await fresh.newPage(); viaLink.on('pageerror', e => errs.push(e.message));
await viaLink.goto(`${BASE}/?room=${code}&seat=${encodeURIComponent(token)}`);
await viaLink.waitForSelector('.board', { timeout: 8000 }).catch(() => {});
check('1 seat link restores the seat on a fresh device', !!(await viaLink.$('.board')));
check('2 the token is removed from the address bar', !/seat=/.test(viaLink.url()), viaLink.url());

// 2. a guest with nothing saved asks for the seat; the host approves
const lost = await page('Bob');
await lost.fill('#code', code); await lost.click('[data-act=online-join]');
await lost.waitForSelector('.modal.reclaim');
check('3 a full room offers the seat picker', (await lost.$$('[data-act=reclaim-pick]')).length === 2);
await lost.click('[data-act=reclaim-pick][data-seat]:has-text("Bob")');
await host.waitForSelector('.modal.approve', { timeout: 10000 });
check('4 the host is asked', /Bob/.test(await host.textContent('.modal.approve')));
await host.click('[data-act=reclaim-deny]');
await lost.waitForFunction(() => /recognise/.test(document.getElementById('toast')?.textContent ?? ''), null, { timeout: 10000 });
check('5 a denied request lets the guest ask again', !!(await lost.$('[data-act=reclaim-pick]')));
await lost.click('[data-act=reclaim-pick][data-seat]:has-text("Bob")');
await host.waitForSelector('.modal.approve', { timeout: 10000 });
await host.click('[data-act=reclaim-approve]');
await lost.waitForSelector('.board', { timeout: 10000 });
check('6 an approved guest is back in the game', !!(await lost.$('.board')));

console.log(errs.length ? `page errors: ${errs.join(' | ')}` : 'no page errors');
await b.close();
console.log(`${pass} passed, ${fail} failed`);
process.exit(fail || errs.length ? 1 : 0);
