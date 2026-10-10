/**
 * End-to-end check of the end-of-game messages: once the last tile is drawn the status says whose final turn it is,
 * and the result pop-up explains why the game ended. Same setup as e2e/navigation.mjs.
 *   node e2e/endgame.mjs
 */
import { chromium } from 'playwright-core';

const BASE = process.env.E2E_URL ?? 'http://localhost:3000';
const b = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
let pass = 0, fail = 0; const errs = [];
const check = (name, ok, extra = '') => { (ok ? pass++ : fail++); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : '  ' + extra}`); };
const pg = await (await b.newContext({ viewport: { width: 390, height: 800 } })).newPage();
pg.on('pageerror', e => errs.push(e.message));
await pg.goto(`${BASE}/`); await pg.waitForSelector('.home .modes');
await pg.click('[data-mode=human_vs_human]'); await pg.waitForSelector('.board'); await pg.waitForTimeout(300);
const status = () => pg.evaluate(() => document.getElementById('status').textContent.replace(/\s+/g, ' ').trim());
check('1 normally there is no final-turn message', !/final turn/i.test(await status()), await status());
await pg.evaluate(() => { const { S, render } = window.__equadium; S.game.final_turn = true; render(); });
check('2 after the last tile is drawn the status says it is a final turn', /Last tile drawn · .*final turn/i.test(await status()), await status());
await pg.evaluate(() => { const { S, render } = window.__equadium; S.game.game_over = true; S.game.end_reason = 'last tile drawn'; S.game.final_turn = false; S.game.winners = [S.game.players[0].name]; S.modal = 'over'; render(); });
const over = await pg.evaluate(() => document.querySelector('.modal.over')?.textContent.replace(/\s+/g, ' ') ?? '');
check('3 the result pop-up explains why the game ended', /last tile was drawn and the final turn has been played/i.test(over), over);
await pg.evaluate(() => { const { S, render } = window.__equadium; S.modal = 'help'; S.game.game_over = false; render(); });
check('4 how to play describes the rule', /last tile is drawn/i.test(await pg.evaluate(() => document.querySelector('.modal.help')?.textContent ?? '')));
check('5 no page errors', errs.length === 0, errs.join('; '));
console.log(`${pass} passed, ${fail} failed`); await b.close(); process.exit(fail ? 1 : 0);
