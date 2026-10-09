/**
 * End-to-end check of forfeiting (see e2e/navigation.mjs for how to run; same setup, same env vars).
 *   node e2e/forfeit.mjs
 */
import { chromium } from 'playwright-core';

const BASE = process.env.E2E_URL ?? 'http://localhost:3000';
const b = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
let pass = 0, fail = 0; const errs = [];
const check = (name, ok, extra = '') => { (ok ? pass++ : fail++); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : '  ' + extra}`); };
async function newPage() {
  const ctx = await b.newContext({ viewport: { width: 390, height: 800 } });
  await ctx.route(/fonts\.(googleapis|gstatic)\.com|gstatic/, r => r.abort());
  const pg = await ctx.newPage();
  pg.on('pageerror', e => errs.push(e.message));
  pg.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|ERR_|net::/.test(m.text())) errs.push(m.text()); });
  await pg.goto(`${BASE}/`); await pg.waitForSelector('.home .modes');
  return pg;
}
const text = (pg, sel) => pg.evaluate(s => document.querySelector(s)?.textContent?.replace(/\s+/g, ' ').trim() ?? '', sel);
const has = (pg, sel) => pg.evaluate(s => !!document.querySelector(s), sel);
const settle = pg => pg.waitForTimeout(500);

// ── vs the computer
let pg = await newPage();
await pg.click('[data-mode=human_vs_agent]'); await pg.waitForSelector('.board');
check('1 the game has a forfeit button', await has(pg, '.bar [data-act=forfeit]'));
await pg.click('.bar [data-act=forfeit]'); await pg.waitForSelector('.modal.forfeit');
check('2 it asks first and says what it costs', /loss for you and a win for the Computer/.test(await text(pg, '.modal.forfeit')), await text(pg, '.modal.forfeit'));
await pg.goBack(); await settle(pg);
check('3 the browser Back button closes the question and the game goes on', !(await has(pg, '.modal')) && await has(pg, '.board'));
await pg.click('.bar [data-act=forfeit]'); await pg.waitForSelector('.modal.forfeit');
await pg.click('.modal [data-act=close]'); await settle(pg);
check('4 "Keep playing" closes it without forfeiting', !(await has(pg, '.modal')) && await has(pg, '.bar [data-act=forfeit]'));
await pg.click('.bar [data-act=forfeit]'); await pg.waitForSelector('.modal.forfeit');
await pg.click('[data-act=forfeit-confirm]'); await pg.waitForSelector('.modal.over');
const over = await text(pg, '.modal.over');
check('5 forfeiting ends the game as a loss for you', /You forfeited/.test(over) && /counts as a loss/.test(over), over);
check('6 the Computer is shown as the winner, whatever the score', await pg.evaluate(() => /Computer/.test(document.querySelector('.final .win')?.textContent ?? '')));
check('7 no forfeit button once the game is over', !(await has(pg, '.bar [data-act=forfeit]')));

// ── pass & play: the player to move gives up
pg = await newPage();
await pg.click('[data-mode=human_vs_human]'); await pg.waitForSelector('.board');
await pg.click('.bar [data-act=forfeit]'); await pg.waitForSelector('.modal.forfeit');
check('8 pass & play names who is giving up', /Player1 gives up/.test(await text(pg, '.modal.forfeit')), await text(pg, '.modal.forfeit'));
await pg.click('[data-act=forfeit-confirm]'); await pg.waitForSelector('.modal.over');
const over2 = await text(pg, '.modal.over');
check('9 …and the other player wins', /Player1 forfeited/.test(over2) && /Player2/.test(await text(pg, '.final .win')), over2);

// ── watching bots: nobody to forfeit
pg = await newPage();
await pg.click('[data-mode=agent_vs_agent]'); await pg.waitForSelector('.board');
check('10 no forfeit button when watching bots', !(await has(pg, '.bar [data-act=forfeit]')));

// ── online: two separate browsers
const host = await newPage(), guest = await newPage();
await host.click('[data-act=online]'); await host.waitForSelector('.online-title');
await host.click('[data-act=online-create]'); await host.waitForSelector('.lobby .code');
const code = (await text(host, '.lobby .code')).trim();
check('11 no forfeit button in the lobby while waiting for an opponent', !(await has(host, '.bar [data-act=forfeit]')));
await guest.click('[data-act=online]'); await guest.waitForSelector('.online-title');
await guest.fill('#code', code); await guest.click('[data-act=online-join]');
await guest.waitForSelector('.board'); await host.waitForSelector('.board', { timeout: 10000 });
check('12 both players have the forfeit button', await has(host, '.bar [data-act=forfeit]') && await has(guest, '.bar [data-act=forfeit]'));
await guest.click('.bar [data-act=forfeit]'); await guest.waitForSelector('.modal.forfeit');
check('13 the online prompt names the opponent', /win for/.test(await text(guest, '.modal.forfeit')), await text(guest, '.modal.forfeit'));
await guest.click('[data-act=forfeit-confirm]'); await guest.waitForSelector('.modal.over');
check('14 the forfeiter sees a loss', /You forfeited/.test(await text(guest, '.modal.over')), await text(guest, '.modal.over'));
await host.waitForSelector('.modal.over', { timeout: 10000 });    // arrives by polling
const hostOver = await text(host, '.modal.over');
check('15 the opponent is told, and wins', /forfeited\. You win!/.test(hostOver), hostOver);

console.log(`\n${pass} passed, ${fail} failed; page errors: ${JSON.stringify(errs)}`);
await b.close();
process.exit(fail ? 1 : 0);
