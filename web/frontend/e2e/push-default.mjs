/**
 * End-to-end check of "notifications are on by default": the browser's permission prompt appears from the first
 * online tap of a signed-in player, only once, never after a refusal or an opt-out. Needs the API started with
 * VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY set (any values; nothing is sent). Same setup as e2e/navigation.mjs.
 *   VAPID_PUBLIC_KEY=x VAPID_PRIVATE_KEY=y python -m uvicorn web.api.main:app --port 8000
 *   node e2e/push-default.mjs
 */
import { chromium } from 'playwright-core';

const BASE = process.env.E2E_URL ?? 'http://localhost:3000';
const b = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
let pass = 0, fail = 0; const errs = [];
const check = (name, ok, extra = '') => { (ok ? pass++ : fail++); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : '  ' + extra}`); };

/** A signed-in player on a browser whose notification permission starts as `permission` and answers `answer` when asked. */
async function player(permission, answer, { optout = false } = {}) {
  const ctx = await b.newContext({ viewport: { width: 390, height: 800 } });
  await ctx.addInitScript(({ permission, answer, optout }) => {
    let perm = permission;
    window.__asks = 0;
    Object.defineProperty(Notification, 'permission', { get: () => perm, configurable: true });
    Notification.requestPermission = async () => { window.__asks++; perm = answer; return answer; };
    if (optout) localStorage.setItem('equadium.push.optout', '1');
  }, { permission, answer, optout });
  const pg = await ctx.newPage();
  pg.on('pageerror', e => errs.push(e.message));
  await pg.goto(`${BASE}/`); await pg.waitForSelector('.home .modes');
  await pg.evaluate(async () => {
    const { S, refreshPushStatus } = window.__equadium;
    S.user = { id: 'u1', name: 'Ann Lee', email: 'ann@example.com', avatar: '' };
    await refreshPushStatus();
  });
  return pg;
}
const asks = pg => pg.evaluate(() => window.__asks);
const status = pg => pg.evaluate(() => window.__equadium.S.push);
async function startOnline(pg) {
  await pg.click('[data-act=online]'); await pg.waitForSelector('.online-title');
  await pg.fill('#name', 'Ann'); await pg.click('[data-act=online-create]'); await pg.waitForSelector('.lobby');
}

let pg = await player('default', 'denied');
check('1 a fresh browser starts with notifications "off" (not yet asked)', (await status(pg)) === 'off', await status(pg));
check('2 nothing is asked just for signing in', (await asks(pg)) === 0);
await startOnline(pg);
check('3 starting an online game shows the permission prompt', (await asks(pg)) === 1, String(await asks(pg)));
await pg.waitForTimeout(500);
check('4 declining is respected: the bell says blocked', (await status(pg)) === 'blocked', await status(pg));
await pg.click('[data-act=home]').catch(() => {});

pg = await player('default', 'default');       // the prompt is dismissed without an answer
await startOnline(pg);
const first = await asks(pg);
await pg.evaluate(() => { window.__equadium.S.screen = 'home'; window.__equadium.render(); });
await pg.click('[data-act=online]'); await pg.waitForSelector('.online-title');
await pg.fill('#name', 'Ann'); await pg.click('[data-act=online-create]'); await pg.waitForSelector('.lobby');
check('5 a dismissed prompt is not repeated on the next game', first === 1 && (await asks(pg)) === 1, `${first} then ${await asks(pg)}`);

pg = await player('default', 'granted', { optout: true });
await startOnline(pg);
check('6 someone who switched notifications off is never prompted', (await asks(pg)) === 0);

pg = await player('denied', 'denied');
check('7 already blocked in browser settings: status is blocked', (await status(pg)) === 'blocked');
await startOnline(pg);
check('8 and no prompt is attempted', (await asks(pg)) === 0);

check('9 no page errors', errs.length === 0, errs.join('; '));
console.log(`${pass} passed, ${fail} failed`); await b.close(); process.exit(fail ? 1 : 0);
