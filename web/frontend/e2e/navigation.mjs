/**
 * End-to-end check of browser Back/Forward behaviour (see src/nav.ts). Not part of CI: it needs a running
 * app and a browser.
 *
 *   # terminal 1: API              python -m uvicorn web.api.main:app --port 8000     (from the repo root)
 *   # terminal 2: web app          cd web/frontend && npm run dev                      (guest mode is enough)
 *   # terminal 3:
 *   cd web/frontend
 *   npm install --no-save playwright-core && npx playwright-core install chromium
 *   node e2e/navigation.mjs                 # E2E_URL=http://localhost:3000 and CHROMIUM_PATH are optional
 *
 * Exits non-zero if any check fails.
 */
import { chromium } from 'playwright-core';

const BASE = process.env.E2E_URL ?? 'http://localhost:3000';
const ch = process.env.CHROMIUM_PATH || undefined;   // undefined = Playwright's own browser
const b = await chromium.launch({ executablePath: ch });
let pass = 0, fail = 0; const errs = [];
const check = (name, ok, extra='') => { (ok ? pass++ : fail++); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : '  ' + extra}`); };
async function newPage() {
  const ctx = await b.newContext({ viewport: { width: 390, height: 800 } });
  await ctx.route(/fonts\.(googleapis|gstatic)\.com|gstatic/, r => r.abort());
  const pg = await ctx.newPage();
  pg.on('pageerror', e => errs.push(e.message));
  pg.on('console', m => { if (m.type()==='error' && !/Failed to load resource|ERR_|net::/.test(m.text())) errs.push(m.text()); });
  await pg.goto(`${BASE}/`); await pg.waitForSelector('.home .modes');
  return pg;
}
const hash = pg => pg.evaluate(() => location.hash);
const onHome = pg => pg.evaluate(() => !!document.querySelector('.home .modes'));
const inGame = pg => pg.evaluate(() => !!document.querySelector('.board'));
const modal = pg => pg.evaluate(() => !!document.querySelector('.modal'));
const settle = pg => pg.waitForTimeout(500);

// ── A: solo game, back and forward
let pg = await newPage();
await pg.click('[data-mode=human_vs_agent]'); await pg.waitForSelector('.board');
const h1 = await hash(pg);
check('A1 game gets its own URL', /^#\/game\/.+/.test(h1), h1);
await pg.goBack(); await settle(pg);
check('A2 Back from a game shows the menu (does not leave the site)', await onHome(pg) && (await hash(pg)) === '' && pg.url().includes(new URL(BASE).host));
await pg.goForward(); await pg.waitForSelector('.board'); await settle(pg);
check('A3 Forward restores the same game', (await hash(pg)) === h1 && await inGame(pg));

// ── B: Back closes a pop-up first
await pg.click('[data-act=swap]'); await pg.waitForSelector('.modal');
await pg.goBack(); await settle(pg);
check('B1 Back closes the swap pop-up and stays in the game', !(await modal(pg)) && await inGame(pg) && (await hash(pg)) === h1);
await pg.goBack(); await settle(pg);
check('B2 a second Back goes to the menu', await onHome(pg));

// ── C: help pop-up on the menu
await pg.click('[data-act=help]'); await pg.waitForSelector('.modal');
await pg.goBack(); await settle(pg);
check('C1 Back closes help and stays on the menu', !(await modal(pg)) && await onHome(pg));

// ── D: closing a pop-up with its own button leaves no stray history entry
await pg.click('[data-mode=human_vs_agent]'); await pg.waitForSelector('.board'); await settle(pg);   // (opening a pop-up cleared the forward history, so start a game again)
await pg.click('[data-act=swap]'); await pg.waitForSelector('.modal');
await pg.click('.modal [data-act=close]'); await settle(pg);
check('D1 Cancel closes the pop-up', !(await modal(pg)) && await inGame(pg));
await pg.goBack(); await settle(pg);
check('D2 then ONE Back reaches the menu (no leftover pop-up entry)', await onHome(pg), await hash(pg));

// ── E: online form
await pg.click('[data-act=online]'); await pg.waitForSelector('.online-title');
check('E1 online form has a URL', (await hash(pg)) === '#/online');
await pg.goBack(); await settle(pg);
check('E2 Back from the form shows the menu', await onHome(pg));
await pg.click('[data-act=online]'); await pg.waitForSelector('.online-title');
await pg.click('[data-act=online-create]'); await pg.waitForSelector('.lobby, .board');
check('E3 creating an online game goes to its room URL', /^#\/room\/[A-Z0-9]{5}$/.test(await hash(pg)), await hash(pg));
await pg.goBack(); await settle(pg);
check('E4 Back from an online game skips the form and shows the menu', await onHome(pg), await hash(pg));

// ── F: reload keeps you where you were
await pg.goForward(); await pg.waitForSelector('.lobby, .board'); await settle(pg);
const roomHash = await hash(pg);
await pg.reload(); await pg.waitForSelector('.lobby, .board', { timeout: 10000 });
check('F1 reloading an online game restores it', (await hash(pg)) === roomHash && !(await onHome(pg)));
await pg.goBack(); await settle(pg);
check('F2 Back after a reload still reaches the menu', await onHome(pg));

// ── G: in-app back arrow behaves like Back
pg = await newPage();
await pg.click('[data-mode=human_vs_agent]'); await pg.waitForSelector('.board');
const g1 = await hash(pg);
const lenBefore = await pg.evaluate(() => history.length);
await pg.click('.bar [data-act=home]'); await settle(pg);
check('G1 the in-app arrow returns to the menu', await onHome(pg) && (await hash(pg)) === '');
check('G2 …without growing the history', (await pg.evaluate(() => history.length)) === lenBefore);
await pg.goForward(); await pg.waitForSelector('.board'); await settle(pg);
check('G3 Forward after the arrow restores the game', (await hash(pg)) === g1);

// ── H: reload on a solo game, then Back
await pg.reload(); await pg.waitForSelector('.board', { timeout: 10000 });
check('H1 reloading a solo game restores it', (await hash(pg)) === g1 && await inGame(pg));
await pg.goBack(); await settle(pg);
check('H2 Back after reload reaches the menu', await onHome(pg));

// ── I: game over -> Play again replaces the finished game in history
pg = await newPage();
await pg.click('[data-mode=human_vs_agent]'); await pg.waitForSelector('.board');
const firstGame = await hash(pg);
await pg.click('.bar [data-act=forfeit]'); await pg.waitForSelector('.modal.forfeit');      // the quickest way to end a game
await pg.click('[data-act=forfeit-confirm]');
await pg.waitForSelector('.modal.over');
await pg.click('.modal [data-act=start]'); await pg.waitForSelector('.board'); await settle(pg);
const secondGame = await hash(pg);
check('I1 Play again starts a new game', secondGame !== firstGame && /^#\/game\//.test(secondGame));
await pg.goBack(); await settle(pg);
check('I2 Back skips the finished game and shows the menu', await onHome(pg), await hash(pg));

// ── J: a game that is gone falls back to the menu (cold load of a stale link, and typing it into a running page)
{
  const ctx = await b.newContext({ viewport: { width: 390, height: 800 } });
  await ctx.route(/fonts\.(googleapis|gstatic)\.com|gstatic/, r => r.abort());
  const p2 = await ctx.newPage(); p2.on('pageerror', e => errs.push(e.message));
  await p2.goto(`${BASE}/#/game/does-not-exist`);
  await p2.waitForFunction(() => location.hash === '' && !!document.querySelector('.home .modes'), null, { timeout: 10000 });
  check('J1 a stale game link opens the menu instead of a broken screen', true);
  check('J2 …with an explanation', /isn't available|not found|expired|Game not found/i.test(await p2.evaluate(() => document.getElementById('toast')?.textContent ?? '')),
        await p2.evaluate(() => document.getElementById('toast')?.textContent ?? ''));
  await p2.goto(`${BASE}/#/game/also-missing`);
  await p2.waitForFunction(() => location.hash === '' && !!document.querySelector('.home .modes'), null, { timeout: 10000 });
  check('J3 same when the bad link is entered in a running page', true);
}

console.log(`\n${pass} passed, ${fail} failed; page errors: ${JSON.stringify(errs)}`);
await b.close();
process.exit(fail ? 1 : 0);
