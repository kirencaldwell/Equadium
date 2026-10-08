import './style.css';
import { api } from './api';
import { equation, tileHtml } from './tiles';
import type { GameState, Mode, Placed, Preview, Tile } from './types';

// ─────────────────────────────────────────────
// State
// ─────────────────────────────────────────────
type Selection = { kind: 'rack'; index: number } | { kind: 'eq' } | { kind: 'placed'; r: number; c: number } | null;

const FREE_EQUALS: Tile = { symbol: '=', points: 0, expr_multiplier: 1 };

const S = {
    screen: 'home' as 'home' | 'game',
    gameId: null as string | null,
    game: null as GameState | null,
    placed: [] as Placed[],
    selected: null as Selection,
    rackOrder: [] as number[],
    preview: null as Preview | null,
    busy: null as null | 'submitting' | 'thinking',
    modal: null as null | 'help' | 'swap' | 'pass' | 'handoff' | 'over',
    swapPick: new Set<number>(),
    handoffFor: null as string | null,
    zoom: 1,
    fresh: new Set<string>(),       // cells that just got a tile (for the pop animation)
    shake: false,
    toast: '',
    watch: { running: false, speed: 900, token: 0 },
};

const app = document.getElementById('app') as HTMLDivElement;
let previewToken = 0;
let lastModal: string | null = null;   // so the open animation plays once, not on every re-render
let toastTimer = 0;

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────
const NAMES: Record<string, string> = { Human: 'You', AI_Opponent: 'Computer', Newton_Bot: 'Newton', Leibniz_Bot: 'Leibniz' };
const display = (n: string) => NAMES[n] ?? n;

/** The seat a person is sitting in right now (null when only bots are playing). */
function actingPlayer(): string | null {
    const g = S.game;
    if (!g) return null;
    const humans = Object.entries(g.seats).filter(([, k]) => k === 'human').map(([n]) => n);
    if (humans.length === 0) return null;
    if (humans.length === 1) return humans[0];
    return g.current_player;
}

const myTurn = () => !!S.game && !S.game.game_over && actingPlayer() === S.game.current_player && !S.busy;

function myRack(): Tile[] {
    const g = S.game, me = actingPlayer();
    return g && me ? g.players.find(p => p.name === me)!.rack : [];
}

function direction(): 'H' | 'V' {
    if (S.placed.length < 2) return 'H';
    return S.placed.every(p => p.r === S.placed[0].r) ? 'H' : 'V';
}

function showToast(msg: string) {
    S.toast = msg;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => { S.toast = ''; const t = document.getElementById('toast'); if (t) t.classList.remove('show'); }, 2600);
    const t = document.getElementById('toast');
    if (t) { t.textContent = msg; t.classList.add('show'); }
}

function resetTurnState() {
    S.placed = [];
    S.selected = null;
    S.preview = null;
    S.swapPick.clear();
    S.rackOrder = myRack().map((_, i) => i);
    previewToken++;
}

// ─────────────────────────────────────────────
// Game lifecycle
// ─────────────────────────────────────────────
async function startGame(mode: Mode) {
    stopWatching();
    try {
        const { game_id } = await api.create(mode);
        S.gameId = game_id;
        S.game = await api.state(game_id);
    } catch (e) {
        showToast(`Couldn't start a game: ${(e as Error).message}`);
        return;
    }
    S.screen = 'game';
    S.modal = null;
    S.zoom = 1;
    S.fresh.clear();
    resetTurnState();
    render();
    centerBoard();
    if (mode === 'agent_vs_agent') startWatching();
}

async function refresh() {
    if (!S.gameId) return;
    const before = S.game;
    S.game = await api.state(S.gameId);
    markFresh(before, S.game);
    resetTurnState();
    if (S.game.game_over) S.modal = 'over';
}

/** Remember which cells changed so the new tiles can animate in. */
function markFresh(before: GameState | null, after: GameState) {
    S.fresh.clear();
    if (!before) return;
    for (let r = 0; r < after.board.height; r++)
        for (let c = 0; c < after.board.width; c++)
            if (after.board.grid[r][c] && !before.board.grid[r][c]) S.fresh.add(`${r},${c}`);
}

function goHome() {
    stopWatching();
    S.screen = 'home';
    S.modal = null;
    S.gameId = null;
    S.game = null;
    render();
}

// ─────────────────────────────────────────────
// Turn actions
// ─────────────────────────────────────────────
async function submitAction(run: () => Promise<{ status: string; error: string | null; score_delta: number; agent_moves: { player: string; action: string; score_delta: number; tiles: string[] | null }[] }>, okMessage: (r: Awaited<ReturnType<typeof run>>) => string) {
    if (!myTurn()) return;
    const me = actingPlayer()!;
    const hotSeat = S.game!.mode === 'human_vs_human';
    S.busy = S.game!.mode === 'human_vs_agent' ? 'thinking' : 'submitting';
    render();
    try {
        const res = await run();
        if (res.status !== 'success') {
            S.busy = null;
            S.shake = true;
            render();
            S.shake = false;
            showToast(res.error ?? 'That move was not accepted');
            return;
        }
        S.busy = null;
        await refresh();
        for (const m of res.agent_moves) showToast(describeMove(m.player, m.action, m.tiles?.length ?? 0, m.score_delta));
        if (hotSeat && !S.game!.game_over) { S.modal = 'handoff'; S.handoffFor = S.game!.current_player; }
        else if (!res.agent_moves.length && !S.game!.game_over) showToast(okMessage(res));
        render();
        centerOnLastMove();
        void me;
    } catch (e) {
        S.busy = null;
        render();
        showToast((e as Error).message);
    }
}

function describeMove(player: string, action: string, tiles: number, score: number): string {
    const who = display(player);
    if (action === 'play') return `${who} played ${tiles} tile${tiles === 1 ? '' : 's'} for +${score}`;
    if (action === 'swap') return `${who} swapped tiles`;
    return `${who} passed`;
}

const play = () => submitAction(
    () => api.play(S.gameId!, actingPlayer()!, S.placed, direction()),
    r => `+${r.score_delta} points`,
);

const swap = () => submitAction(
    () => api.swap(S.gameId!, actingPlayer()!, [...S.swapPick]),
    () => 'Tiles swapped',
);

const pass = () => submitAction(() => api.pass(S.gameId!, actingPlayer()!), () => 'Turn passed');

// ─────────────────────────────────────────────
// Placing tiles
// ─────────────────────────────────────────────
const usedRackIdx = () => new Set(S.placed.filter(p => p.src !== 'eq').map(p => p.src as number));
const freeEqualsAvailable = () => !S.placed.some(p => p.src === 'eq') && (S.game?.equals_pile_count ?? 0) > 0;

function placeSelectedAt(r: number, c: number) {
    if (!S.game || !myTurn() || S.game.board.grid[r][c] || S.placed.some(p => p.r === r && p.c === c)) return;
    const sel = S.selected;
    if (!sel) return;
    if (sel.kind === 'placed') {
        const p = S.placed.find(q => q.r === sel.r && q.c === sel.c);
        if (p) { p.r = r; p.c = c; }
    } else if (sel.kind === 'eq') {
        S.placed.push({ r, c, tile: FREE_EQUALS, src: 'eq' });
    } else {
        S.placed.push({ r, c, tile: myRack()[sel.index], src: sel.index });
    }
    S.selected = null;
    S.fresh = new Set([`${r},${c}`]);
    afterPlacementChange();
}

function recall(r: number, c: number) {
    S.placed = S.placed.filter(p => !(p.r === r && p.c === c));
    S.selected = null;
    afterPlacementChange();
}

function recallAll() {
    S.placed = [];
    S.selected = null;
    afterPlacementChange();
}

function afterPlacementChange() {
    S.preview = null;
    render();
    schedulePreview();
}

let previewTimer = 0;
function schedulePreview() {
    window.clearTimeout(previewTimer);
    if (!S.placed.length || !S.gameId) return;
    const token = ++previewToken;
    previewTimer = window.setTimeout(async () => {
        try {
            const p = await api.validate(S.gameId!, S.placed, direction());
            if (token !== previewToken) return;   // a newer edit superseded this one
            S.preview = p;
            updateDock();
        } catch { /* preview is best-effort */ }
    }, 120);
}

// ─────────────────────────────────────────────
// Watching bots
// ─────────────────────────────────────────────
function startWatching() {
    S.watch.running = true;
    const token = ++S.watch.token;
    void (async () => {
        while (S.watch.running && token === S.watch.token && S.game && !S.game.game_over) {
            await new Promise(r => setTimeout(r, S.watch.speed));
            if (!S.watch.running || token !== S.watch.token) return;
            try {
                await api.agentStep(S.gameId!);
                await refresh();
                render();
                centerOnLastMove();
            } catch { S.watch.running = false; }
        }
        S.watch.running = false;
        render();
    })();
}

function stopWatching() {
    S.watch.running = false;
    S.watch.token++;
}

// ─────────────────────────────────────────────
// Rendering
// ─────────────────────────────────────────────
function render() {
    const base = window.matchMedia('(max-width: 480px)').matches ? 42 : 46;
    document.documentElement.style.setProperty('--cell-size', `${Math.round(base * S.zoom)}px`);
    const scroller = document.querySelector('.board-scroll') as HTMLElement | null;
    const pos = scroller ? { x: scroller.scrollLeft, y: scroller.scrollTop } : null;
    app.innerHTML = S.screen === 'home' ? homeHtml() : gameHtml();
    app.insertAdjacentHTML('beforeend', modalHtml());
    lastModal = S.modal;
    app.insertAdjacentHTML('beforeend', `<div id="toast" class="toast ${S.toast ? 'show' : ''}">${S.toast}</div>`);
    const next = document.querySelector('.board-scroll') as HTMLElement | null;
    if (next && pos) { next.scrollLeft = pos.x; next.scrollTop = pos.y; }
}

function homeHtml(): string {
    const card = (mode: Mode | '', icon: string, title: string, blurb: string, disabled = false) => `
        <button class="mode-card" ${disabled ? 'disabled' : `data-act="start" data-mode="${mode}"`}>
            <span class="mode-icon">${icon}</span>
            <span class="mode-text"><strong>${title}</strong><small>${blurb}</small></span>
            ${disabled ? '<em class="soon">Soon</em>' : '<span class="chev">›</span>'}
        </button>`;
    return `
    <main class="home">
        <div class="home-tiles" aria-hidden="true">
            ${[['d/dx(', 3, 2], ['x**2', 2, 1], [')', 0, 1], ['=', 0, 1], ['2', 1, 1], ['x', 1, 1]].map(([s, p, m]) => tileHtml({ symbol: s as string, points: p as number, expr_multiplier: m as number }, 'big')).join('')}
        </div>
        <h1 class="wordmark">Equadium</h1>
        <p class="tagline">A calculus game</p>
        <div class="modes">
            ${card('human_vs_agent', '🧮', 'Play the Computer', 'Solo. Out-build the bot.')}
            ${card('human_vs_human', '🤝', 'Pass &amp; Play', 'Two players, one screen.')}
            ${card('agent_vs_agent', '🤖', 'Watch the Bots', 'Two bots battle it out.')}
            ${card('', '🌐', 'Online', 'Play friends anywhere.', true)}
        </div>
        <button class="link" data-act="help">How to play</button>
    </main>`;
}

function scoreboardHtml(g: GameState): string {
    return `<div class="scoreboard">${g.players.map(p => {
        const active = p.name === g.current_player && !g.game_over;
        const bot = g.seats[p.name] === 'agent';
        return `<div class="score ${active ? 'active' : ''}">
            <span class="who">${display(p.name)}${bot ? ' <i>bot</i>' : ''}</span>
            <span class="pts">${p.score}</span>
        </div>`;
    }).join('')}<div class="bag"><strong>${g.bag_count}</strong><span>in bag</span></div></div>`;
}

function statusText(g: GameState): string {
    if (S.busy === 'thinking') return 'Computer is thinking…';
    if (g.game_over) return 'Game over';
    const lm = g.last_move;
    const base = g.mode === 'agent_vs_agent'
        ? `${display(g.current_player)} to move`
        : g.current_player === actingPlayer() ? (g.mode === 'human_vs_human' ? `${display(g.current_player)}'s turn` : 'Your turn') : '';
    if (lm && lm.ok && S.placed.length === 0) {
        const last = describeMove(lm.player, lm.action, lm.tiles?.length ?? 0, lm.score_delta);
        return base ? `${last} · ${base}` : last;
    }
    return base;
}

function boardHtml(g: GameState): string {
    const { width, height, grid } = g.board;
    const lastCells = new Set((g.last_move?.cells ?? []).map(([r, c]) => `${r},${c}`));
    const midR = Math.floor(height / 2), midC = Math.floor(width / 2);
    const placedAt = new Map(S.placed.map(p => [`${p.r},${p.c}`, p]));
    const valid = S.preview?.valid;
    const canDrop = myTurn();
    let html = `<div class="board" style="grid-template-columns:repeat(${width}, var(--cell-size))">`;
    for (let r = 0; r < height; r++) {
        for (let c = 0; c < width; c++) {
            const key = `${r},${c}`;
            const t = grid[r][c];
            const p = placedAt.get(key);
            const adj = !t && !p && ((r > 0 && grid[r - 1][c]) || (r < height - 1 && grid[r + 1][c]) || (c > 0 && grid[r][c - 1]) || (c < width - 1 && grid[r][c + 1]));
            const cls = ['cell', adj ? 'adj' : '', r === midR && c === midC ? 'start' : ''].join(' ');
            let inner = '';
            if (t) inner = tileHtml(t, `${lastCells.has(key) ? 'last' : ''} ${S.fresh.has(key) ? 'pop' : ''}`);
            else if (p) {
                const sel = S.selected?.kind === 'placed' && S.selected.r === r && S.selected.c === c;
                inner = tileHtml(p.tile, `pending ${valid ? 'ok' : ''} ${sel ? 'selected' : ''} ${S.fresh.has(key) ? 'pop' : ''}`);
            }
            const drag = p && canDrop ? ' draggable="true"' : '';
            html += `<div class="${cls}" data-r="${r}" data-c="${c}"${drag}${canDrop && !t ? ' data-drop="1"' : ''}>${inner}</div>`;
        }
    }
    return html + '</div>';
}

function rackHtml(): string {
    const rack = myRack();
    const used = usedRackIdx();
    const tiles: string[] = [];
    if (S.game && (S.game.equals_pile_count > 0 || S.placed.some(p => p.src === 'eq'))) {
        const avail = freeEqualsAvailable();
        const sel = S.selected?.kind === 'eq';
        tiles.push(`<button class="slot free-eq ${avail ? '' : 'used'} ${sel ? 'selected' : ''}" data-act="pick-eq" ${avail ? 'draggable="true"' : 'disabled'} aria-label="Free equals tile">
            ${tileHtml(FREE_EQUALS)}<small>free</small></button>`);
    }
    for (const i of S.rackOrder) {
        if (i >= rack.length) continue;
        const isUsed = used.has(i);
        const sel = S.selected?.kind === 'rack' && S.selected.index === i;
        tiles.push(`<button class="slot ${isUsed ? 'used' : ''} ${sel ? 'selected' : ''}" data-act="pick" data-i="${i}" ${isUsed ? 'disabled' : 'draggable="true"'}>${tileHtml(rack[i])}</button>`);
    }
    return `<div class="rack" id="rack">${tiles.join('')}</div>`;
}

function previewHtml(): string {
    const p = S.preview;
    if (!S.placed.length) {
        return `<div class="preview hint">${S.game && myTurn() ? 'Tap a tile, then tap a square. Build equations that connect to the board.' : ''}</div>`;
    }
    if (!p) return '<div class="preview hint">…</div>';
    if (p.valid) {
        return `<div class="preview good"><span class="eqs">${p.equations.map(equation).join('<span class="sep">·</span>')}</span><span class="pill">+${p.score}</span></div>`;
    }
    return `<div class="preview hint">${p.reason ? humanReason(p.reason) : 'Keep building…'}</div>`;
}

function humanReason(reason: string): string {
    if (reason.startsWith("'") && reason.includes('not a valid equation')) return 'Both sides must be equal.';
    return reason.replace(/\s*\(.*\)$/, '');
}

function actionsHtml(g: GameState): string {
    const can = myTurn();
    const playable = can && !!S.preview?.valid;
    return `<div class="actions">
        <button class="ghost" data-act="shuffle" ${can ? '' : 'disabled'} aria-label="Shuffle">⇄<small>Shuffle</small></button>
        <button class="ghost" data-act="recall" ${can && S.placed.length ? '' : 'disabled'} aria-label="Recall">↺<small>Recall</small></button>
        <button class="ghost" data-act="swap" ${can && g.bag_count > 0 && !S.placed.length ? '' : 'disabled'} aria-label="Swap">⇅<small>Swap</small></button>
        <button class="ghost" data-act="pass" ${can && !S.placed.length ? '' : 'disabled'} aria-label="Pass">⏭<small>Pass</small></button>
        <button class="primary" data-act="play" ${playable ? '' : 'disabled'}>Play${playable ? ` · +${S.preview!.score}` : ''}</button>
    </div>`;
}

function watchBarHtml(): string {
    const sp = S.watch.speed;
    const opt = (ms: number, label: string) => `<button class="seg ${sp === ms ? 'on' : ''}" data-act="speed" data-ms="${ms}">${label}</button>`;
    return `<div class="watch-bar">
        <button class="primary small" data-act="watch-toggle">${S.watch.running ? '❚❚ Pause' : '▶ Play'}</button>
        <button class="ghost wide" data-act="watch-step" ${S.watch.running || S.game?.game_over ? 'disabled' : ''}>Step</button>
        <div class="segs">${opt(1800, 'Slow')}${opt(900, 'Med')}${opt(250, 'Fast')}</div>
    </div>`;
}

function dockHtml(g: GameState): string {
    if (g.mode === 'agent_vs_agent') return `<div class="dock">${watchBarHtml()}</div>`;
    return `<div class="dock" id="dock">${previewHtml()}${rackHtml()}${actionsHtml(g)}</div>`;
}

/** Re-render just the dock (preview arrives asynchronously; don't redraw the board for it). */
function updateDock() {
    const dock = document.getElementById('dock');
    if (!dock || !S.game) return;
    dock.outerHTML = dockHtml(S.game);
    // pending tiles turn green when valid
    document.querySelectorAll('.tile.pending').forEach(el => el.classList.toggle('ok', !!S.preview?.valid));
}

function gameHtml(): string {
    const g = S.game!;
    return `
    <div class="game ${S.shake ? 'shake' : ''} ${S.busy ? 'busy' : ''}">
        <header class="bar">
            <button class="icon" data-act="home" aria-label="Menu">‹</button>
            <span class="wordmark small">Equadium</span>
            <button class="icon" data-act="help" aria-label="How to play">?</button>
        </header>
        ${scoreboardHtml(g)}
        <div class="status ${S.busy === 'thinking' ? 'thinking' : ''}" id="status">${statusText(g)}</div>
        <div class="board-wrap">
            <div class="board-scroll" id="board-scroll">${boardHtml(g)}</div>
            <div class="zoom">
                <button class="icon round" data-act="zoom-in" aria-label="Zoom in">+</button>
                <button class="icon round" data-act="zoom-out" aria-label="Zoom out">−</button>
                <button class="icon round" data-act="center" aria-label="Centre board">◎</button>
            </div>
        </div>
        ${dockHtml(g)}
    </div>`;
}

function modalHtml(): string {
    const g = S.game;
    let body = '';
    switch (S.modal) {
        case 'help':
            body = `<h2>How to play</h2>
            <ol class="rules">
                <li><b>Build equations</b> across and down, like a crossword. Both sides must be equal: <span class="eg">${equation('x+x=2x')}</span></li>
                <li>Every play must <b>connect</b> to tiles already on the board.</li>
                <li>You get <b>one free <span class="eg">=</span></b> each turn.</li>
                <li><span class="eg">${equation('d/dx(')}</span> and <span class="eg">∫(</span> tiles multiply your score by <b>×2</b> and <b>×3</b>. An integral needs <span class="eg">+C</span>. Stack two <span class="eg">${equation('d/dx(')}</span> tiles for a second derivative and <b>×4</b>.</li>
                <li>The game ends when someone runs out of tiles, or nobody can play.</li>
            </ol>
            <button class="primary" data-act="close">Got it</button>`;
            break;
        case 'swap': {
            const rack = myRack();
            body = `<h2>Swap tiles</h2><p class="muted">Choose tiles to throw back. Ends your turn.</p>
            <div class="rack swap">${S.rackOrder.filter(i => i < rack.length).map(i => `<button class="slot ${S.swapPick.has(i) ? 'selected' : ''}" data-act="swap-pick" data-i="${i}">${tileHtml(rack[i])}</button>`).join('')}</div>
            <div class="modal-actions"><button class="ghost wide" data-act="close">Cancel</button>
            <button class="primary" data-act="swap-confirm" ${S.swapPick.size ? '' : 'disabled'}>Swap ${S.swapPick.size || ''}</button></div>`;
            break;
        }
        case 'pass':
            body = `<h2>Skip your turn?</h2><p class="muted">You keep your tiles and score nothing.</p>
            <div class="modal-actions"><button class="ghost wide" data-act="close">Cancel</button><button class="primary" data-act="pass-confirm">Pass</button></div>`;
            break;
        case 'handoff':
            body = `<h2>${display(S.handoffFor ?? '')}, you're up</h2><p class="muted">Pass the device. Tiles are hidden until you tap.</p>
            <button class="primary" data-act="close">I'm ready</button>`;
            break;
        case 'over': {
            if (!g) break;
            const tie = g.winners.length > 1;
            const me = g.mode === 'human_vs_agent' ? 'Human' : null;
            const headline = tie ? "It's a tie" : me ? (g.winners[0] === me ? 'You win!' : 'Computer wins') : `${display(g.winners[0])} wins`;
            const why: Record<string, string> = { 'rack empty': 'Someone used every tile.', stalled: 'Nobody could play.', 'bag empty+stuck': 'The bag ran out and nobody could play.', 'turn limit': 'Turn limit reached.' };
            body = `<h2>${headline}</h2><p class="muted">${why[g.end_reason ?? ''] ?? ''}</p>
            <div class="final">${[...g.players].sort((a, b) => b.score - a.score).map(p => `<div class="${g.winners.includes(p.name) ? 'win' : ''}"><span>${display(p.name)}</span><strong>${p.score}</strong></div>`).join('')}</div>
            <div class="modal-actions"><button class="ghost wide" data-act="home">Menu</button><button class="primary" data-act="start" data-mode="${g.mode}">Play again</button></div>`;
            break;
        }
        default:
            return '';
    }
    const dismissable = S.modal === 'help' || S.modal === 'swap' || S.modal === 'pass';
    const enter = lastModal !== S.modal ? 'enter' : '';
    return `<div class="scrim ${S.modal} ${enter}" ${dismissable ? 'data-act="close-scrim"' : ''}><div class="modal ${S.modal} ${enter}" role="dialog">${body}</div></div>`;
}

// ─────────────────────────────────────────────
// Board navigation
// ─────────────────────────────────────────────
function scrollToCell(r: number, c: number, smooth = true) {
    const sc = document.getElementById('board-scroll');
    const cell = document.querySelector(`.cell[data-r="${r}"][data-c="${c}"]`) as HTMLElement | null;
    if (!sc || !cell) return;
    sc.scrollTo({
        left: cell.offsetLeft - sc.clientWidth / 2 + cell.offsetWidth / 2,
        top: cell.offsetTop - sc.clientHeight / 2 + cell.offsetHeight / 2,
        behavior: smooth ? 'smooth' : 'auto',
    });
}

function centerBoard() {
    if (!S.game) return;
    scrollToCell(Math.floor(S.game.board.height / 2), Math.floor(S.game.board.width / 2), false);
}

function centerOnLastMove() {
    const cells = S.game?.last_move?.cells;
    if (!cells?.length) return;
    const [r, c] = cells[Math.floor(cells.length / 2)];
    scrollToCell(r, c);
}

function setZoom(z: number) {
    S.zoom = Math.max(0.6, Math.min(1.6, z));
    const sc = document.getElementById('board-scroll');
    const cx = sc ? (sc.scrollLeft + sc.clientWidth / 2) / sc.scrollWidth : 0.5;
    const cy = sc ? (sc.scrollTop + sc.clientHeight / 2) / sc.scrollHeight : 0.5;
    render();
    const next = document.getElementById('board-scroll');
    if (next) {
        next.scrollLeft = cx * next.scrollWidth - next.clientWidth / 2;
        next.scrollTop = cy * next.scrollHeight - next.clientHeight / 2;
    }
}

// ─────────────────────────────────────────────
// Events (delegated)
// ─────────────────────────────────────────────
app.addEventListener('click', (e) => {
    const target = e.target as HTMLElement;
    const actEl = target.closest('[data-act]') as HTMLElement | null;
    const cell = target.closest('.cell') as HTMLElement | null;

    if (actEl) {
        const act = actEl.dataset.act!;
        // clicks inside the modal body that bubble up to the scrim shouldn't dismiss it
        if (act === 'close-scrim' && target.closest('.modal')) return;
        switch (act) {
            case 'start': void startGame((actEl.dataset.mode as Mode)); break;
            case 'help': S.modal = 'help'; render(); break;
            case 'close': case 'close-scrim': S.modal = S.game?.game_over ? 'over' : null; render(); break;
            case 'home': goHome(); break;
            case 'pick': {
                if (!myTurn()) break;
                const i = Number(actEl.dataset.i);
                S.selected = S.selected?.kind === 'rack' && S.selected.index === i ? null : { kind: 'rack', index: i };
                render(); break;
            }
            case 'pick-eq':
                if (!myTurn()) break;
                S.selected = S.selected?.kind === 'eq' ? null : { kind: 'eq' };
                render(); break;
            case 'shuffle': {
                const o = [...S.rackOrder];
                for (let i = o.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [o[i], o[j]] = [o[j], o[i]]; }
                S.rackOrder = o; render(); break;
            }
            case 'recall': recallAll(); break;
            case 'play': void play(); break;
            case 'swap': S.swapPick.clear(); S.modal = 'swap'; render(); break;
            case 'swap-pick': {
                const i = Number(actEl.dataset.i);
                if (S.swapPick.has(i)) S.swapPick.delete(i); else S.swapPick.add(i);
                render(); break;
            }
            case 'swap-confirm': S.modal = null; void swap(); break;
            case 'pass': S.modal = 'pass'; render(); break;
            case 'pass-confirm': S.modal = null; void pass(); break;
            case 'zoom-in': setZoom(S.zoom + 0.15); break;
            case 'zoom-out': setZoom(S.zoom - 0.15); break;
            case 'center': centerBoard(); break;
            case 'speed': S.watch.speed = Number(actEl.dataset.ms); render(); break;
            case 'watch-toggle': if (S.watch.running) { stopWatching(); render(); } else startWatching(); render(); break;
            case 'watch-step':
                void api.agentStep(S.gameId!).then(refresh).then(() => { render(); centerOnLastMove(); });
                break;
        }
        return;
    }

    if (cell && S.screen === 'game') {
        const r = Number(cell.dataset.r), c = Number(cell.dataset.c);
        const placed = S.placed.find(p => p.r === r && p.c === c);
        if (placed) {
            // tap a pending tile: pick it up (to move) — tap again to send it home
            if (S.selected?.kind === 'placed' && S.selected.r === r && S.selected.c === c) recall(r, c);
            else if (myTurn()) { S.selected = { kind: 'placed', r, c }; render(); }
        } else {
            placeSelectedAt(r, c);
        }
    }
});

// Drag and drop (desktop). Touch devices use tap-to-place.
app.addEventListener('dragstart', (e) => {
    const el = e.target as HTMLElement;
    const slot = el.closest('.slot[data-act]') as HTMLElement | null;
    const cell = el.closest('.cell[draggable="true"]') as HTMLElement | null;
    if (slot) {
        S.selected = slot.dataset.act === 'pick-eq' ? { kind: 'eq' } : { kind: 'rack', index: Number(slot.dataset.i) };
    } else if (cell) {
        S.selected = { kind: 'placed', r: Number(cell.dataset.r), c: Number(cell.dataset.c) };
    } else return;
    e.dataTransfer?.setData('text/plain', 'tile');
});
app.addEventListener('dragover', (e) => { if ((e.target as HTMLElement).closest('.cell[data-drop]')) e.preventDefault(); });
app.addEventListener('drop', (e) => {
    const cell = (e.target as HTMLElement).closest('.cell[data-drop]') as HTMLElement | null;
    if (!cell) return;
    e.preventDefault();
    placeSelectedAt(Number(cell.dataset.r), Number(cell.dataset.c));
});

document.addEventListener('keydown', (e) => {
    if (S.screen !== 'game' || S.modal) return;
    if (e.key === 'Enter' && S.preview?.valid && myTurn()) void play();
    if (e.key === 'Escape') recallAll();
});

render();
