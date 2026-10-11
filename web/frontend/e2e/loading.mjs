/**
 * End-to-end check that a slow or failing server is never shown as "no games / no stats": the Continue list and the
 * stats screen say they couldn't load, retry by themselves (or on a tap), and fill in once the server answers.
 * The server's answers are faked here. Same setup as e2e/navigation.mjs.
 *   node e2e/loading.mjs
 */
import { chromium } from 'playwright-core';

const BASE = process.env.E2E_URL ?? 'http://localhost:3000';
const b = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
let pass = 0, fail = 0; const errs = [];
const check = (name, ok, extra = '') => { (ok ? pass++ : fail++); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : '  ' + extra}`); };
const pg = await (await b.newContext({ viewport: { width: 390, height: 800 } })).newPage();
pg.on('pageerror', e => errs.push(e.message));
await pg.goto(`${BASE}/`); await pg.waitForSelector('.home .modes');

const game = { kind: 'solo', id: 'g1', code: null, mode: 'human_vs_agent', status: 'active', seat: 'Human', token: null, current_player: 'Human', your_turn: true,
  players: [{ name: 'Human', score: 40 }, { name: 'AI_Opponent', score: 12 }], turns_played: 4, updated_at: new Date().toISOString() };
let gamesCalls = 0, serverUp = false;
await pg.route('**/me/games', route => { gamesCalls++; serverUp ? route.fulfill({ json: { games: [game] } }) : route.fulfill({ status: 503, json: { detail: 'down' } }); });
await pg.route('**/me/stats', route => serverUp
  ? route.fulfill({ json: { games: 3, wins: 2, losses: 1, ties: 0, forfeits: 0, win_rate: .67, avg_score: 120, best_score: 200, best_play: 40, win_streak: 1, vs_computer: { games: 3, wins: 2 }, vs_humans: { games: 0, wins: 0 }, recent: [] } })
  : route.fulfill({ status: 503, json: { detail: "Couldn't load your stats right now." } }));
const text = sel => pg.evaluate(s => document.querySelector(s)?.textContent?.replace(/\s+/g, ' ').trim() ?? '', sel);

await pg.evaluate(() => { const { S, loadSavedGames } = window.__equadium; S.user = { id: 'u1', name: 'Ann Lee', email: 'a@b.co', avatar: '' }; return loadSavedGames(); });
await pg.waitForSelector('.saved .load-error');
check('1 a failing server shows "couldn\'t load", not an empty home screen', /Couldn't load your games/.test(await text('.saved')), await text('.saved'));
check('2 ...with a way to retry now', !!(await pg.$('.saved [data-act=reload-saved]')));
serverUp = true;
await pg.waitForSelector('.saved-card', { timeout: 6000 });
check('3 it retries by itself and the game appears once the server answers', /vs Computer/.test(await text('.saved')) && gamesCalls >= 2, `calls=${gamesCalls}`);

// stats
serverUp = false;
await pg.evaluate(() => window.__equadium.openStats());
await pg.waitForSelector('.load-error');
check('4 stats that fail to load say so (not "No finished games yet")', /Couldn't load your stats/.test(await text('.stats')) && !/No finished games/.test(await text('.stats')), await text('.stats'));
serverUp = true;
await pg.click('[data-act=retry-stats]'); await pg.waitForSelector('.stat-grid');
check('5 Try again loads them', /Win rate/.test(await text('.stats')));

// coming back to the page re-checks the list
await pg.evaluate(() => { document.querySelector('[data-act=home]').click(); });
await pg.waitForSelector('.home .modes');
const before = gamesCalls;
await pg.evaluate(() => window.dispatchEvent(new Event('focus')));
await pg.waitForTimeout(400);
check('6 returning to the app checks the list again', gamesCalls > before, `${before} -> ${gamesCalls}`);
check('7 no page errors', errs.length === 0, errs.join('; '));
console.log(`${pass} passed, ${fail} failed`); await b.close(); process.exit(fail ? 1 : 0);
