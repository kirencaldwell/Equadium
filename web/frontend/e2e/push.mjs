/**
 * End-to-end check of the push plumbing that needs no sign-in: the manifest, the service worker showing a pushed
 * message, and the bell staying hidden for guests. (Delivery from the server to a phone can't be tested here.)
 *   node e2e/push.mjs
 */
import { chromium } from 'playwright-core';

const BASE = process.env.E2E_URL ?? 'http://localhost:3000';
const b = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
let pass = 0, fail = 0; const errs = [];
const check = (name, ok, extra = '') => { (ok ? pass++ : fail++); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : '  ' + extra}`); };
const ctx = await b.newContext({ viewport: { width: 390, height: 800 } });
await ctx.grantPermissions(['notifications'], { origin: BASE });
const pg = await ctx.newPage();
pg.on('pageerror', e => errs.push(e.message));
await pg.goto(`${BASE}/`); await pg.waitForSelector('.home .modes');

const manifest = await pg.evaluate(async () => (await fetch('/manifest.webmanifest')).json());
check('1 the app has a web manifest with icons', manifest.name === 'Equadium' && manifest.icons.length >= 3 && manifest.display === 'standalone');
const icon = await pg.evaluate(async () => (await fetch('/icons/icon-192.png')).headers.get('content-type'));
check('2 the icons are served', icon === 'image/png', icon);

// register the service worker and push a message into it through the browser's own debugging hook
await pg.evaluate(async () => { await navigator.serviceWorker.register('/sw.js'); await navigator.serviceWorker.ready; });
const cdp = await ctx.newCDPSession(pg);
const regPromise = new Promise((resolve, reject) => {
  setTimeout(() => reject(new Error('no service worker registration seen')), 8000);
  cdp.on('ServiceWorker.workerRegistrationUpdated', ({ registrations }) => { const r = registrations.find(x => x.scopeURL.startsWith(BASE)); if (r) resolve(r.registrationId); });
});
await cdp.send('ServiceWorker.enable');
const regId = await regPromise;
await cdp.send('ServiceWorker.deliverPushMessage', { origin: new URL(BASE).origin, registrationId: regId,
  data: JSON.stringify({ title: 'Equadium', body: 'Ann played for 12 points. Your turn!', tag: 'room-ABCDE', url: '/#/room/ABCDE', code: 'ABCDE' }) });
await pg.waitForTimeout(800);
const shown = await pg.evaluate(async () => (await (await navigator.serviceWorker.getRegistration('/sw.js')).getNotifications()).map(n => ({ t: n.title, b: n.body, tag: n.tag, code: n.data?.code })));
check('3 a pushed message shows a notification', shown.length === 1 && shown[0].b === 'Ann played for 12 points. Your turn!', JSON.stringify(shown));
check('4 it carries the game code and tag', shown[0]?.tag === 'room-ABCDE' && shown[0]?.code === 'ABCDE');

check('5 no bell for guests', !(await pg.$('[data-act=push-bell]')));

// The signed-in parts of the UI can't be reached without a Google sign-in, so drive the app state directly (dev builds only).
await pg.click('[data-act=online]'); await pg.waitForSelector('.online-title');
await pg.fill('#name', 'Ann'); await pg.click('[data-act=online-create]'); await pg.waitForSelector('.lobby');
const code = (await pg.evaluate(() => location.hash)).split('/')[2];
const friend = await (await b.newContext({ viewport: { width: 390, height: 800 } })).newPage();
await friend.goto(`${BASE}/`); await friend.waitForSelector('.home .modes');
await friend.click('[data-act=online]'); await friend.waitForSelector('.online-title');
await friend.fill('#name', 'Bob'); await friend.fill('#code', code); await friend.click('[data-act=online-join]'); await friend.waitForSelector('.board');
await pg.waitForSelector('.board', { timeout: 10000 });
const signIn = push => pg.evaluate((st) => { const { S, render } = window.__equadium; S.user = { id: 'u1', name: 'Ann Lee', email: 'ann@example.com', avatar: '' }; S.push = st; render(); }, push);
await signIn('off');
check('6 a signed-in player in an online game sees the bell', !!(await pg.$('.bar [data-act=push-bell]')));
for (const st of ['unsupported', 'unavailable']) {
  await signIn(st);
  check(`7 no bell when notifications are ${st}`, !(await pg.$('[data-act=push-bell]')));
}
await signIn('on');
check('8 the bell shows when they are on', await pg.evaluate(() => !!document.querySelector('.bell.on')));
await pg.click('.bar [data-act=push-bell]'); await pg.waitForTimeout(300);
check('9 tapping the bell while on just says so', /Notifications are on/.test(await pg.evaluate(() => document.getElementById('toast')?.textContent ?? '')));
await signIn('needs-install'); await pg.click('.bar [data-act=push-bell]'); await pg.waitForTimeout(300);
check('10 on iPhone the bell explains Add to Home Screen', /Add to Home Screen/.test(await pg.evaluate(() => document.getElementById('toast')?.textContent ?? '')));
await signIn('off');
await pg.evaluate(() => { const { S, render } = window.__equadium; S.modal = 'account'; render(); });
check('11 the account menu has the notifications row', !!(await pg.$('.modal.account [data-act=push-toggle]')));
await pg.screenshot({ path: process.env.SHOT ?? '/tmp/push-account.png' });
check('12 no page errors', errs.length === 0, errs.join('; '));
console.log(`${pass} passed, ${fail} failed`); await b.close(); process.exit(fail ? 1 : 0);
