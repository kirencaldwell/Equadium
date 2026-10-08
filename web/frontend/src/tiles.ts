import type { Tile } from './types';

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

/** Visual family of a tile; drives its colour. */
export function tileKind(symbol: string): 'calc' | 'fn' | 'eq' | 'var' | 'num' | 'op' {
    if (symbol === 'd/dx(' || symbol === 'int(') return 'calc';
    if (symbol === '=') return 'eq';
    if (['e^x', 'exp(', 'sin(x)', 'cos(x)'].includes(symbol)) return 'fn';
    if (['x', 'a', 'b', 'C', '(x+a)', '(x+b)', 'x**2', 'x**3', 'x**4'].includes(symbol)) return 'var';
    if (/^\d/.test(symbol)) return 'num';
    return 'op';
}

/** Typeset a tile symbol (or a whole equation string) as HTML. */
export function math(src: string): string {
    return esc(src)
        .replace(/x\*\*(\d)/g, 'x<sup>$1</sup>')
        .replace(/e\^x/g, 'e<sup>x</sup>')
        .replace(/(\d)\/(\d)/g, '<span class="frac"><i>$1</i><i>$2</i></span>')
        .replace(/d\/dx\(/g, '<span class="ddx">d/dx</span>(')
        .replace(/int\(/g, '<span class="integral">∫</span>(')
        .replace(/\bexp\(/g, '<span class="fn">exp</span>(')
        .replace(/\b(sin|cos)\(x\)/g, '<span class="fn">$1</span>(x)');
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
