import type { Tile } from './types';

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

/** Visual family of a tile; drives its colour. */
export function tileKind(symbol: string): 'calc' | 'fn' | 'eq' | 'var' | 'num' | 'op' {
    if (symbol === 'd/dx(' || symbol === 'int(') return 'calc';
    if (symbol === '=') return 'eq';
    if (['e^x', 'exp(', 'sin(x)', 'cos(x)', 'ln(x)'].includes(symbol)) return 'fn';
    if (['x', 'a', 'b', 'k', 'C', '(x+a)', '(x+b)', 'x**2', 'x**3', 'x**4'].includes(symbol)) return 'var';
    if (/^\d/.test(symbol)) return 'num';
    return 'op';
}

/** Typeset a tile symbol (or a whole equation string) as HTML. */
export function math(src: string): string {
    return esc(src)
        .replace(/-/g, '\u2212')   // a real minus sign
        .replace(/x\*\*(\d)/g, 'x<sup>$1</sup>')
        .replace(/e\^x/g, 'e<sup>x</sup>')
        .replace(/(\d)\/([\dx])/g, '<span class="frac"><i>$1</i><i>$2</i></span>')
        .replace(/d\/dx\(/g, '<span class="ddx">d/dx</span>(')
        .replace(/int\(/g, '<svg class="integral" viewBox="0 0 12 28" aria-label="integral"><path d="M9.4 4.4C9.2 2.4 7.9 1.3 6.6 1.3 4.9 1.3 4.5 2.9 4.5 5.2V22.8C4.5 25.1 4.1 26.7 2.4 26.7 1.1 26.7 .8 25.6 .6 24.4"/><circle cx="9.7" cy="5.4" r="1.15"/><circle cx="1.6" cy="23.9" r="1.15"/></svg>(')
        .replace(/\bexp\(/g, '<span class="fn">exp</span>(')
        .replace(/\b(sin|cos|ln)\(x\)/g, '<span class="fn">$1</span>(x)');
}

/** Typeset a whole equation for the live preview ("x**2+2x=x**2+2x"). */
export function equation(src: string): string {
    return math(src.replace(/=/g, ' = ').replace(/\+/g, ' + '));
}

export function tileHtml(tile: Tile, extra = ''): string {
    const kind = tileKind(tile.symbol);
    const pts = tile.points > 0 ? `<b class="pts">${tile.points}</b>` : '';
    const mult = tile.expr_multiplier > 1 ? `<b class="mult">×${tile.expr_multiplier}</b>` : '';
    const long = tile.symbol.length > 3 && !/^(x\*\*|1\/)/.test(tile.symbol) ? ' long' : '';
    return `<div class="tile ${kind}${long} ${extra}"><span class="face">${math(tile.symbol)}</span>${pts}${mult}</div>`;
}

const OPERATORS = new Set(['+', '-', '=']);
const OPENERS = new Set(['d/dx(', 'int(', 'exp(', '(']);

/**
 * An equation spelled out tile by tile, easy to read: operators get room, and two tiles that multiply are
 * separated by a dot ("3 · x³ · x²"), so it is clear where one tile ends and the next begins.
 */
export function equationFromTiles(symbols: string[]): string {
    let html = '', prev: string | null = null;
    for (const t of symbols) {
        const op = OPERATORS.has(t), closer = t === ')';
        const prevEndsValue = prev !== null && !OPERATORS.has(prev) && !OPENERS.has(prev);
        if (op) html += `<span class="op">${t === '-' ? '\u2212' : t}</span>`;
        else {
            if (!closer && prevEndsValue) html += '<i class="dot">\u00b7</i>';
            html += `<span class="term">${math(t)}</span>`;
        }
        prev = t;
    }
    return `<span class="eq">${html}</span>`;
}
