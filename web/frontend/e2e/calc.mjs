/**
 * End-to-end check of the calculus cheat sheet (same setup as e2e/navigation.mjs).
 *   node e2e/calc.mjs
 */
import { chromium } from 'playwright-core';

const BASE = process.env.E2E_URL ?? 'http://localhost:3000';
const b = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
let pass = 0, fail = 0; const errs = [];
const check = (name, ok, extra = '') => { (ok ? pass++ : fail++); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : '  ' + extra}`); };
const ctx = await b.newContext({ viewport: { width: 390, height: 800 } });
const pg = await ctx.newPage();
pg.on('pageerror', e => errs.push(e.message));
await pg.goto(`${BASE}/`); await pg.waitForSelector('.home .modes');
await pg.click('[data-mode=human_vs_agent]'); await pg.waitForSelector('.board');
check('1 the game bar has a cheat-sheet button', await pg.$('.bar [data-act=calc]') !== null);
await pg.click('.bar [data-act=calc]'); await pg.waitForSelector('.modal.calc');
check('2 it lists the table rows', (await pg.$$('.modal.calc .ct-row')).length >= 10);
const box = await pg.evaluate(() => { const m = document.querySelector('.modal.calc').getBoundingClientRect(); return m.width <= innerWidth && m.right <= innerWidth; });
check('3 it fits a phone screen', box);
await pg.goBack(); await pg.waitForTimeout(400);
check('4 Back closes it and stays in the game', !(await pg.$('.modal.calc')) && !!(await pg.$('.board')));
await pg.click('.bar [data-act=calc]'); await pg.waitForSelector('.modal.calc');
await pg.click('.modal.calc [data-act=close]'); await pg.waitForTimeout(400);
check('5 the Close button works', !(await pg.$('.modal.calc')));
check('6 no page errors', errs.length === 0, errs.join('; '));
console.log(`${pass} passed, ${fail} failed`); await b.close(); process.exit(fail ? 1 : 0);
