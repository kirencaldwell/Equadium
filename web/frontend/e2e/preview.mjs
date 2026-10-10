/**
 * End-to-end check of the equation preview: the play's equation is spelled out tile by tile, and every tile of
 * the line it makes (existing ones too) is lit green. Same setup as e2e/navigation.mjs.
 *   node e2e/preview.mjs
 */
import { chromium } from 'playwright-core';

const BASE = process.env.E2E_URL ?? 'http://localhost:3000';
const b = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
let pass = 0, fail = 0; const errs = [];
const check = (name, ok, extra = '') => { (ok ? pass++ : fail++); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : '  ' + extra}`); };
const pg = await (await b.newContext({ viewport: { width: 390, height: 800 } })).newPage();
pg.on('pageerror', e => errs.push(e.message));
await pg.goto(`${BASE}/`); await pg.waitForSelector('.home .modes');
await pg.click('[data-mode=human_vs_human]'); await pg.waitForSelector('.board');
await pg.waitForTimeout(400);

// Play "x = x" to the right of the centre x, through the real server preview
const sym = s => pg.evaluate(async (s) => {
  const { S } = window.__equadium; const rack = S.game.players.find(p => p.name === S.game.current_player).rack;
  return rack.findIndex(t => t.symbol === s);
}, s);
const cell = await pg.evaluate(() => { const c = document.querySelector('.cell.start'); return { r: +c.dataset.r, c: +c.dataset.c }; });
// rig the rack through the API so the test does not depend on the draw
const ok = await pg.evaluate(async ({ r, c }) => {
  const { S, render } = window.__equadium;
  const eq = { symbol: '=', points: 0, expr_multiplier: 1 }, x = { symbol: 'x', points: 1, expr_multiplier: 1 };
  S.placed = [{ r, c: c + 1, tile: eq, src: 'eq' }, { r, c: c + 2, tile: x, src: 0 }];
  const res = await fetch(`/games/${S.gameId}/validate_move`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tiles_to_play: S.placed.map(p => ({ r: p.r, c: p.c, tile: p.tile })), direction: 'H' }) });
  S.preview = await res.json(); render(); return S.preview;
}, cell);
check('1 the server describes the equation tile by tile', JSON.stringify(ok.equation_tiles) === JSON.stringify([['x', '=', 'x']]), JSON.stringify(ok));
check('2 the preview shows the operator spaced out', !!(await pg.$('.preview .eq .op')));
check('3 the existing x on the board is lit green too', await pg.evaluate(({ r, c }) => document.querySelector(`.cell[data-r="${r}"][data-c="${c}"] .tile`).classList.contains('eq-ok'), cell));
check('4 and so are the pending tiles', (await pg.$$('.tile.pending.ok')).length === 2);
check('5 tiles elsewhere are not lit', (await pg.$$('.tile.eq-ok')).length === 1);

// a longer line: multiplying neighbours are separated by dots
await pg.evaluate(() => { const { S, render } = window.__equadium; S.preview = { valid: true, reason: null, equations: ['3x**3x**2=x**2x**33(1/3)3'], equation_tiles: [['3', 'x', 'x**3', 'x**2', '=', 'x**2', 'x**3', '3', '1/3', '3']], score: 16 }; render(); });
const dots = await pg.evaluate(() => document.querySelectorAll('.preview .eq .dot').length);
check('6 neighbouring multiplying tiles get dots between them (3 · x · x³ · x² = x² · x³ · 3 · ⅓ · 3)', dots === 7, String(dots));
await pg.evaluate(() => { const { S, render } = window.__equadium; S.preview = { valid: true, reason: null, equations: ['d/dx(x**2)+x=3x'], equation_tiles: [['d/dx(', 'x**2', ')', '+', 'x', '=', '3', 'x']], score: 9 }; render(); });
check('7 no dot right after an opening bracket or before a closing one, or around operators', (await pg.evaluate(() => document.querySelectorAll('.preview .eq .dot').length)) === 1);   // only 3 · x
await pg.screenshot({ path: process.env.SHOT ?? '/tmp/preview.png', clip: { x: 0, y: 560, width: 390, height: 240 } });
await pg.evaluate(() => { const { S, render } = window.__equadium; S.preview = { valid: false, reason: "'xx=2x' is not a valid equation (Valid)", equations: [], equation_tiles: [], reason_tiles: ['x', 'x', '=', '2', 'x'], score: 0 }; render(); });
check('8 a failing line is spelled out too', (await pg.evaluate(() => document.querySelectorAll('.preview.bad .eq .dot').length)) === 2);
check('9 no page errors', errs.length === 0, errs.join('; '));
console.log(`${pass} passed, ${fail} failed`); await b.close(); process.exit(fail ? 1 : 0);
