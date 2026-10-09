import './style.css';
import { api, me, room } from './api';
import { authAvailable, initAuth, signInWithGoogle, signOut, takePendingRoom, type User } from './auth';
import { equation, tileHtml } from './tiles';
import { currentState, parseHash, pushModal, pushRoute, replaceRoute, sameRoute, type Route } from './nav';
import type { GameState, Mode, Placed, Preview, SavedGame, Stats, Tile } from './types';

// ─────────────────────────────────────────────
// State
// ─────────────────────────────────────────────
type Selection = { kind: 'rack'; index: number } | { kind: 'eq' } | { kind: 'placed'; r: number; c: number } | null;

interface OnlineSeat { code: string; seat: string; token: string }

const SAVE_KEY = 'equadium.online';
const NAME_KEY = 'equadium.name';
const POLL_MS = 1500;

function loadSaved(): OnlineSeat | null {
    try { return JSON.parse(localStorage.getItem(SAVE_KEY) ?? 'null'); } catch { return null; }
}
function saveSeat(o: OnlineSeat | null) {
    try { if (o) localStorage.setItem(SAVE_KEY, JSON.stringify(o)); else localStorage.removeItem(SAVE_KEY); } catch { /* storage unavailable */ }
}
function savedName(): string {
    try { return localStorage.getItem(NAME_KEY) ?? ''; } catch { return ''; }
}
function saveName(n: string) {
    try { localStorage.setItem(NAME_KEY, n); } catch { /* storage unavailable */ }
}
const joinLink = (code: string) => `${location.origin}${location.pathname}?room=${code}`;

const FREE_EQUALS: Tile = { symbol: '=', points: 0, expr_multiplier: 1 };

const S = {
    screen: 'home' as 'home' | 'online' | 'game' | 'stats',
    user: null as User | null,           // signed-in player (null = guest)
    saved: [] as SavedGame[],            // unfinished games to resume (signed-in only)
    stats: null as Stats | null,
    online: null as null | OnlineSeat,   // set while playing a remote game
    version: 0,                          // last server version seen (online polling)
    gameId: null as string | null,
    game: null as GameState | null,
    placed: [] as Placed[],
    selected: null as Selection,
    rackOrder: [] as number[],
    preview: null as Preview | null,
    busy: null as null | 'submitting' | 'thinking',
    modal: null as null | 'help' | 'swap' | 'pass' | 'forfeit' | 'handoff' | 'over' | 'account',
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
const display = (n: string) => {
    if (S.online) return n === S.online.seat ? 'You' : S.game?.labels?.[n] ?? 'Opponent';
    return NAMES[n] ?? n;
};

/** The seat a person is sitting in right now (null when only bots are playing). */
function actingPlayer(): string | null {
    const g = S.game;
    if (!g) return null;
    if (S.online) return S.online.seat;
    const humans = Object.entries(g.seats).filter(([, k]) => k === 'human').map(([n]) => n);
    if (humans.length === 0) return null;
    if (humans.length === 1) return humans[0];
    return g.current_player;
}

const myTurn = () => !!S.game && !S.game.game_over && (!S.online || !!S.game.joined) && actingPlayer() === S.game.current_player && !S.busy;

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
// Navigation: every screen is a history entry, so the browser's Back/Forward buttons (and a phone's back
// gesture) move between screens instead of leaving the site. See nav.ts for the URL scheme.
// ─────────────────────────────────────────────
const DISMISSABLE = new Set(['help', 'swap', 'pass', 'forfeit', 'account']);
let modalEntry = false;   // the top history entry is a pop-up we pushed, so Back should just close it
let swallowPop = 0;       // popstate events we caused ourselves (closing a pop-up); ignored by the handler

/** The route for what is on screen right now. */
function shownRoute(): Route {
    if (S.screen === 'game') {
        return S.online ? { screen: 'game', kind: 'room', code: S.online.code } : { screen: 'game', kind: 'solo', id: S.gameId ?? '' };
    }
    return { screen: S.screen };
}

/**
 * Records a user-initiated move to `route`. Leaving a game or the online form *replaces* its entry rather
 * than stacking it, so Back never lands on a dead screen (e.g. the finished game you just left, or the
 * form you filled in to start one). A move made from an open pop-up replaces the pop-up's entry.
 */
function go(route: Route) {
    if (modalEntry) { modalEntry = false; replaceRoute(route); return; }
    if (sameRoute(route, currentState().route)) return;
    if (S.screen === 'game' || S.screen === 'online') replaceRoute(route);
    else pushRoute(route);
}

function openModal(m: NonNullable<typeof S.modal>) {
    S.modal = m;
    if (DISMISSABLE.has(m) && !modalEntry) { pushModal(m); modalEntry = true; }   // Back now closes it
    render();
}

function closeModal() {
    S.modal = S.game?.game_over ? 'over' : null;
    if (modalEntry) { modalEntry = false; swallowPop++; history.back(); }          // drop the pop-up's entry
    render();
}

function leaveGame() {
    stopWatching();
    stopPolling();
    S.online = null;
    S.gameId = null;
    S.game = null;
}

function showHome() {
    leaveGame();
    S.screen = 'home';
    S.modal = null;
    S.toast = '';
    render();
    void loadSavedGames();
}

function showOnline() {
    go({ screen: 'online' });
    leaveGame();
    S.screen = 'online';
    S.modal = null;
    render();
}

/** Back to the menu: step back in history when we can, so the browser's stack stays what the user expects. */
function goHome() {
    if (currentState().idx > 0) { history.back(); return; }   // the popstate handler shows the home screen
    replaceRoute({ screen: 'home' });
    showHome();
}

/** A route we can't show (expired game, signed out): put the menu in its place without adding an entry. */
function failToHome() {
    replaceRoute({ screen: 'home' });
    showHome();
}

/** Shows `route` without touching history (used by Back/Forward and on page load). */
async function applyRoute(route: Route) {
    if (sameRoute(route, shownRoute())) {
        S.modal = S.game?.game_over ? 'over' : null;
        render();
        return;
    }
    S.modal = null;
    try {
        switch (route.screen) {
            case 'home': showHome(); break;
            case 'online': leaveGame(); S.screen = 'online'; render(); break;
            case 'stats': if (S.user) await openStats(false); else failToHome(); break;
            case 'game':
                if (route.kind === 'solo') await openSolo(route.id, false);
                else await openRoomByCode(route.code);
                break;
        }
    } catch (e) {
        failToHome();     // first: showing the menu clears toasts
        showToast((e as Error).message || "That game isn't available any more");
    }
}

/** Re-enters an online game we already sit in (Back/Forward or a reload); never silently joins a new one. */
async function openRoomByCode(code: string) {
    const known = loadSaved()?.code === code ? loadSaved()
        : S.saved.find(g => g.kind === 'room' && g.code === code && g.seat)
            ? { code, seat: S.saved.find(g => g.code === code)!.seat!, token: S.saved.find(g => g.code === code)!.token ?? '' }
            : null;
    if (!known) { leaveGame(); S.screen = 'online'; replaceRoute({ screen: 'online' }); render(); throw new Error('Join this game from the Online screen'); }
    await enterRoom(known, false);
}

window.addEventListener('popstate', (e) => {
    if (swallowPop > 0) { swallowPop--; return; }
    const st = e.state && e.state.v === 1 ? (e.state as ReturnType<typeof currentState>) : null;
    const target = st?.route ?? parseHash(location.hash);
    if (modalEntry) {                       // Back while a pop-up is open: close the pop-up, stay on the screen
        modalEntry = false;
        S.modal = S.game?.game_over ? 'over' : null;
        if (sameRoute(target, shownRoute())) { render(); return; }
    }
    if (st?.modal && DISMISSABLE.has(st.modal) && sameRoute(target, shownRoute())) {   // Forward onto a pop-up
        S.modal = st.modal as NonNullable<typeof S.modal>;
        modalEntry = true;
        render();
        return;
    }
    void applyRoute(target);
});

// ─────────────────────────────────────────────
// Game lifecycle
// ─────────────────────────────────────────────
async function startGame(mode: Mode) {
    stopWatching();
    stopPolling();
    S.online = null;
    try {
        const { game_id } = await api.create(mode);
        await openSolo(game_id);
    } catch (e) {
        showToast(`Couldn't start a game: ${(e as Error).message}`);
    }
}

/** Shows a solo game (new, or resumed from the account) from the server's copy. */
async function openSolo(id: string, push = true) {
    const game = await api.state(id);      // load first, so a failure leaves history untouched
    if (push) go({ screen: 'game', kind: 'solo', id });
    stopWatching();
    stopPolling();
    S.online = null;
    S.game = game;
    S.gameId = id;
    S.screen = 'game';
    S.modal = S.game.game_over ? 'over' : null;
    S.zoom = 1;
    S.fresh.clear();
    resetTurnState();
    render();
    centerBoard();
    if (S.game.mode === 'agent_vs_agent' && !S.game.game_over) startWatching();
}

// ─────────────────────────────────────────────
// Accounts: saved games and stats
// ─────────────────────────────────────────────
async function loadSavedGames() {
    if (!S.user) { S.saved = []; return; }
    try {
        S.saved = (await me.games()).games;
    } catch { /* offline or not signed in on the server: keep what we have */ }
    if (S.screen === 'home' && !S.modal) render();
}

async function openStats(push = true) {
    if (push) go({ screen: 'stats' });
    S.modal = null;
    S.screen = 'stats';
    S.stats = null;
    render();
    try {
        S.stats = await me.stats();
    } catch (e) {
        showToast((e as Error).message);
        goHome();
        return;
    }
    render();
}

async function resumeSaved(g: SavedGame) {
    try {
        if (g.kind === 'room' && g.code && g.seat) await enterRoom({ code: g.code, seat: g.seat, token: g.token ?? '' });
        else await openSolo(g.id);
    } catch (e) {
        showToast((e as Error).message);
    }
}

async function deleteSaved(g: SavedGame) {
    S.saved = S.saved.filter(x => x.id !== g.id);
    render();
    try { await api.remove(g.id); } catch (e) { showToast((e as Error).message); void loadSavedGames(); }
}

/** Registers the profile and warns if the server is up but not actually saving games (e.g. storage misconfigured). */
async function checkSaving() {
    try {
        const p = await me.profile();
        if (p.saving === 'memory') showToast("Heads up: the server isn't saving games right now, so progress won't carry over.");
    } catch { /* offline or not signed in on the server: nothing to report */ }
}

function onUserChange(user: User | null) {
    const changed = user?.id !== S.user?.id;
    S.user = user;
    if (!changed) return;   // token refreshes also land here
    S.saved = [];
    S.stats = null;
    if (user) void checkSaving();
    void loadSavedGames();
    if (!user && S.screen === 'stats') { failToHome(); return; }
    if (S.screen !== 'game') render();
}

async function fetchState(): Promise<GameState | null> {
    if (S.online) {
        const r = await room.state(S.online.code, S.online.token);
        S.version = r.version;
        return r.changed ? r.state : null;
    }
    return api.state(S.gameId!);
}

async function refresh() {
    if (!S.gameId) return;
    const before = S.game;
    const next = await fetchState();
    if (next) S.game = next;
    if (!S.game) return;
    markFresh(before, S.game);
    resetTurnState();
    if (S.game.game_over) { S.modal = 'over'; if (S.online) { saveSeat(null); stopPolling(); } void loadSavedGames(); }
}

/** Remember which cells changed so the new tiles can animate in. */
function markFresh(before: GameState | null, after: GameState) {
    S.fresh.clear();
    if (!before) return;
    for (let r = 0; r < after.board.height; r++)
        for (let c = 0; c < after.board.width; c++)
            if (after.board.grid[r][c] && !before.board.grid[r][c]) S.fresh.add(`${r},${c}`);
}

// ── Online games ────────────────────────────────────────────
let pollTimer = 0;

function stopPolling() {
    window.clearInterval(pollTimer);
    pollTimer = 0;
}

function startPolling() {
    stopPolling();
    pollTimer = window.setInterval(() => void pollOnce(), POLL_MS);
}

async function pollOnce() {
    const o = S.online;
    if (!o || S.screen !== 'game' || S.busy || document.hidden) return;
    try {
        const r = await room.state(o.code, o.token, S.version);
        if (!S.online || S.busy || !r.changed) return;
        S.version = r.version;
        const before = S.game;
        S.game = r.state;
        markFresh(before, S.game);
        const lm = S.game.last_move;
        const theirs = !!lm && lm.player !== o.seat && JSON.stringify(lm) !== JSON.stringify(before?.last_move);
        if (before && !before.joined && S.game.joined) showToast(`${display(otherSeat())} joined. Let's play!`);
        else if (theirs && lm) showToast(describeMove(lm.player, lm.action, lm.tiles?.length ?? 0, lm.score_delta));
        resetTurnState();
        if (S.game.game_over) { S.modal = 'over'; saveSeat(null); stopPolling(); }
        render();
        if (theirs) centerOnLastMove();
    } catch (e) {
        const msg = (e as Error).message;
        if (/not a player|No game with that code/i.test(msg)) {
            saveSeat(null); stopPolling(); S.online = null;
            showToast('That game has ended or expired');
            goHome();
        }
        // other errors (offline, server restarting): keep trying quietly
    }
}

const otherSeat = () => (S.online?.seat === 'Player1' ? 'Player2' : 'Player1');

async function enterRoom(seat: OnlineSeat, push = true) {
    let state: GameState;
    let version: number;
    try {
        const r = await room.state(seat.code, seat.token);
        if (!r.changed) throw new Error('Could not load the game');
        state = r.state;
        version = r.version;
    } catch (e) {
        saveSeat(null);                     // the room is gone: forget our seat so it stops offering "resume"
        throw e;
    }
    if (push) go({ screen: 'game', kind: 'room', code: seat.code });   // after loading, so a failure leaves history untouched
    stopWatching();
    stopPolling();
    S.online = seat;
    S.gameId = seat.code;
    S.version = version;
    S.game = state;
    saveSeat(seat);
    S.screen = 'game';
    S.modal = S.game.game_over ? 'over' : null;
    S.zoom = 1;
    S.fresh.clear();
    resetTurnState();
    render();
    centerBoard();
    if (!S.game.game_over) startPolling();
}

async function createOnline() {
    const name = readName();
    try { await enterRoom(await room.create(name)); } catch (e) { showToast((e as Error).message); }
}

async function joinOnline(code: string) {
    const name = readName();
    code = code.trim().toUpperCase();
    if (!code) { showToast('Enter a game code'); return; }
    try { await enterRoom(await room.join(code, name)); } catch (e) { showToast((e as Error).message); }
}

function readName(): string {
    const el = document.getElementById('name') as HTMLInputElement | null;
    const name = (el?.value ?? savedName()).trim();
    saveName(name);
    return name;
}

// ─────────────────────────────────────────────
// Turn actions
// ─────────────────────────────────────────────
async function submitAction(run: () => Promise<{ status: string; error: string | null; score_delta: number; agent_moves: { player: string; action: string; score_delta: number; tiles: string[] | null }[] }>, okMessage: (r: Awaited<ReturnType<typeof run>>) => string, anyTurn = false) {
    // Moves need it to be your turn; giving up does not.
    if (anyTurn ? !(S.game && !S.game.game_over && !S.busy) : !myTurn()) return;
    const me = actingPlayer()!;
    const hotSeat = S.game!.mode === 'human_vs_human' && !S.online;
    S.busy = S.game!.mode === 'human_vs_agent' && !anyTurn ? 'thinking' : 'submitting';
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
    if (action === 'forfeit') return `${who} forfeited`;
    return `${who} passed`;
}

const play = () => submitAction(
    () => S.online ? room.play(S.online.code, S.online.token, S.placed, direction())
                   : api.play(S.gameId!, actingPlayer()!, S.placed, direction()),
    r => `+${r.score_delta} points`,
);

const swap = () => submitAction(
    () => S.online ? room.swap(S.online.code, S.online.token, [...S.swapPick])
                   : api.swap(S.gameId!, actingPlayer()!, [...S.swapPick]),
    () => 'Tiles swapped',
);

/** Whether the signed-in/seated human can give up this game right now. */
const canForfeit = (g: GameState) => !g.game_over && g.mode !== 'agent_vs_agent' && (!S.online || g.joined);

const forfeit = () => submitAction(
    () => S.online ? room.forfeit(S.online.code, S.online.token) : api.forfeit(S.gameId!, actingPlayer()!),
    () => 'You forfeited the game',
    true,
);

const pass = () => submitAction(
    () => S.online ? room.pass(S.online.code, S.online.token) : api.pass(S.gameId!, actingPlayer()!),
    () => 'Turn passed',
);

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
            const p = S.online ? await room.validate(S.online.code, S.online.token, S.placed, direction())
                               : await api.validate(S.gameId!, S.placed, direction());
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
    app.innerHTML = S.screen === 'home' ? homeHtml() : S.screen === 'online' ? onlineHtml() : S.screen === 'stats' ? statsHtml() : gameHtml();
    app.insertAdjacentHTML('beforeend', modalHtml());
    lastModal = S.modal;
    app.insertAdjacentHTML('beforeend', `<div id="toast" class="toast ${S.toast ? 'show' : ''}">${S.toast}</div>`);
    const next = document.querySelector('.board-scroll') as HTMLElement | null;
    if (next && pos) { next.scrollLeft = pos.x; next.scrollTop = pos.y; }
}

function homeHtml(): string {
    const resume = loadSaved();
    const card = (mode: Mode | '', icon: string, title: string, blurb: string, disabled = false) => `
        <button class="mode-card" ${disabled ? 'disabled' : `data-act="start" data-mode="${mode}"`}>
            <span class="mode-icon">${icon}</span>
            <span class="mode-text"><strong>${title}</strong><small>${blurb}</small></span>
            ${disabled ? '<em class="soon">Soon</em>' : '<span class="chev">›</span>'}
        </button>`;
    return `
    <main class="home">
        ${profileChip()}
        <div class="home-tiles" aria-hidden="true">
            ${[['d/dx(', 3, 2], ['x**2', 2, 1], [')', 0, 1], ['=', 0, 1], ['2', 1, 1], ['x', 1, 1]].map(([s, p, m]) => tileHtml({ symbol: s as string, points: p as number, expr_multiplier: m as number }, 'big')).join('')}
        </div>
        <h1 class="wordmark">Equadium</h1>
        <p class="tagline">A calculus game</p>
        ${savedHtml()}
        <div class="modes">
            ${card('human_vs_agent', '🧮', 'Play the Computer', 'Solo. Out-build the bot.')}
            ${card('human_vs_human', '🤝', 'Pass &amp; Play', 'Two players, one screen.')}
            ${card('agent_vs_agent', '🤖', 'Watch the Bots', 'Two bots battle it out.')}
            <button class="mode-card" data-act="online">
                <span class="mode-icon">🌐</span>
                <span class="mode-text"><strong>Play a Friend Online</strong><small>Share a code, play from anywhere.</small></span>
                <span class="chev">›</span>
            </button>
            ${resume ? `<button class="mode-card resume" data-act="online-resume">
                <span class="mode-icon">▶</span>
                <span class="mode-text"><strong>Resume your game</strong><small>Code ${resume.code}</small></span>
                <span class="chev">›</span>
            </button>` : ''}
        </div>
        ${authAvailable && !S.user ? '<p class="muted small center">Sign in to keep your games and stats across devices.</p>' : ''}
        <button class="link" data-act="help">How to play</button>
    </main>`;
}

const initial = (u: User) => (u.name.trim()[0] ?? '?').toUpperCase();
const avatarHtml = (u: User) => u.avatar
    ? `<img class="avatar" src="${u.avatar}" alt="" referrerpolicy="no-referrer">`
    : `<span class="avatar letter">${initial(u)}</span>`;

function profileChip(): string {
    if (!authAvailable) return '';
    const u = S.user;
    return `<div class="home-top">${u
        ? `<button class="chip" data-act="account" aria-label="Account">${avatarHtml(u)}<span>${u.name.split(' ')[0]}</span></button>`
        : '<button class="chip signin" data-act="signin">Sign in</button>'}</div>`;
}

function ago(iso: string): string {
    const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
    if (mins < 2) return 'just now';
    if (mins < 60) return `${mins} min ago`;
    const hrs = Math.round(mins / 60);
    return hrs < 24 ? `${hrs} h ago` : `${Math.round(hrs / 24)} d ago`;
}

function savedHtml(): string {
    if (!S.user || !S.saved.length) return '';
    const cards = S.saved.map((g, i) => {
        const mine = g.players.find(p => p.name === g.seat);
        const theirs = g.players.find(p => p.name !== g.seat);
        const online = g.kind === 'room';
        const title = online ? `Online · ${g.code}` : g.mode === 'human_vs_agent' ? 'vs Computer' : 'Pass & Play';
        const opp = online ? (theirs?.label ?? 'Friend') : g.mode === 'human_vs_agent' ? 'Computer' : (theirs?.label ?? theirs?.name ?? '');
        const state = g.status === 'waiting' ? 'Waiting for a friend to join'
            : online ? (g.your_turn ? 'Your turn' : `${opp}'s turn`) : `${ago(g.updated_at)}`;
        const score = g.status === 'waiting' ? '' : `<span class="score-line">${mine?.score ?? 0}<i>–</i>${theirs?.score ?? 0}</span>`;
        return `<div class="saved-card ${g.your_turn ? 'turn' : ''}">
            <button class="saved-main" data-act="resume-saved" data-i="${i}">
                <span class="saved-text"><strong>${title}</strong><small>${state}</small></span>${score}
            </button>
            ${online ? '' : `<button class="saved-del" data-act="delete-saved" data-i="${i}" aria-label="Delete game">✕</button>`}
        </div>`;
    }).join('');
    return `<section class="saved"><h3>Your games</h3>${cards}</section>`;
}

function statsHtml(): string {
    const st = S.stats;
    const pct = (n: number) => `${Math.round(n * 100)}%`;
    const card = (label: string, value: string | number) => `<div class="stat"><strong>${value}</strong><span>${label}</span></div>`;
    let body: string;
    if (!st) body = '<p class="muted center">Loading…</p>';
    else if (!st.games) body = '<p class="muted center">No finished games yet. Finish a game against the computer or a friend and it will show up here.</p>';
    else body = `
        <div class="stat-grid">
            ${card('Games', st.games)}${card('Win rate', pct(st.win_rate))}${card('Record', `${st.wins}–${st.losses}${st.ties ? `–${st.ties}` : ''}`)}
            ${card('Avg score', st.avg_score)}${card('Best score', st.best_score)}${card('Best play', st.best_play)}
            ${card('Win streak', st.win_streak)}${card('vs Computer', `${st.vs_computer.wins}/${st.vs_computer.games}`)}${card('vs Friends', `${st.vs_humans.wins}/${st.vs_humans.games}`)}
        </div>
        <h3 class="recent-title">Recent games</h3>
        <div class="recent">${st.recent.map(r => `
            <div class="recent-row ${r.outcome}"><span class="pill ${r.outcome}">${r.outcome === 'win' ? 'W' : r.outcome === 'loss' ? 'L' : 'T'}</span>
            <span>${r.opponent === 'agent' ? 'Computer' : r.mode === 'online' ? 'Online friend' : 'Friend'}</span>
            <strong>${r.score}–${r.opp_score}</strong><small>${ago(r.finished_at)}</small></div>`).join('')}</div>`;
    return `<main class="home stats">
        <header class="stats-head"><button class="icon" data-act="home" aria-label="Back">‹</button><h2>Your stats</h2><span></span></header>
        ${body}</main>`;
}

function onlineHtml(): string {
    const incoming = new URLSearchParams(location.search).get('room')?.toUpperCase() ?? '';
    return `
    <main class="home online">
        <h2 class="online-title">Play a friend online</h2>
        <label class="field"><span>Your name</span>
            <input id="name" maxlength="16" autocomplete="nickname" placeholder="Player" value="${(savedName() || S.user?.name || '').replace(/"/g, '&quot;')}"></label>
        <div class="modes">
            <button class="mode-card" data-act="online-create">
                <span class="mode-icon">✨</span>
                <span class="mode-text"><strong>Start a new game</strong><small>Get a code to send to a friend.</small></span>
                <span class="chev">›</span>
            </button>
        </div>
        <div class="join-row">
            <input id="code" maxlength="5" autocapitalize="characters" autocomplete="off" spellcheck="false" placeholder="CODE" value="${incoming}">
            <button class="primary" data-act="online-join">Join</button>
        </div>
        <button class="link" data-act="home">‹ Back</button>
    </main>`;
}

function lobbyHtml(g: GameState): string {
    const code = S.online!.code;
    return `
    <div class="game">
        <header class="bar">
            <button class="icon" data-act="home" aria-label="Menu">‹</button>
            <span class="wordmark small">Equadium</span>
            <button class="icon" data-act="help" aria-label="How to play">?</button>
        </header>
        <main class="lobby">
            <p class="muted">Send this code or link to your friend:</p>
            <div class="code">${code}</div>
            <button class="primary" data-act="copy-link">Copy invite link</button>
            <p class="waiting">Waiting for ${display(otherSeat())} to join<span class="dots"></span></p>
            <p class="muted small">${g.labels?.[S.online!.seat] ? `You're playing as ${g.labels[S.online!.seat]}. ` : ''}${S.user ? 'Your game is saved to your account, so you can pick it up on any device.' : 'Your game is saved on this device, so you can leave and come back.'}</p>
        </main>
    </div>`;
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
    const base = S.online
        ? (g.current_player === S.online.seat ? 'Your turn' : `Waiting for ${display(g.current_player)}…`)
        : g.mode === 'agent_vs_agent'
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
    if (S.online && !g.joined) return lobbyHtml(g);
    return `
    <div class="game ${S.shake ? 'shake' : ''} ${S.busy ? 'busy' : ''}">
        <header class="bar">
            <button class="icon" data-act="home" aria-label="Menu">‹</button>
            <span class="wordmark small">Equadium</span>
            <span class="bar-right">
                ${canForfeit(g) ? '<button class="icon flag" data-act="forfeit" aria-label="Forfeit game" title="Forfeit">⚑</button>' : ''}
                <button class="icon" data-act="help" aria-label="How to play">?</button>
            </span>
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
        case 'forfeit': {
            const opp = g ? (S.online ? display(otherSeat()) : g.mode === 'human_vs_agent' ? 'the Computer' : display(g.players.find(p => p.name !== actingPlayer())?.name ?? 'your opponent')) : 'your opponent';
            const who = g && g.mode === 'human_vs_human' && !S.online ? `${display(actingPlayer() ?? '')} gives up. ` : '';
            body = `<h2>Forfeit this game?</h2><p class="muted">${who}It counts as a loss for ${g && g.mode === 'human_vs_human' && !S.online ? display(actingPlayer() ?? '') : 'you'} and a win for ${opp}, whatever the score.</p>
            <div class="modal-actions"><button class="ghost wide" data-act="close">Keep playing</button><button class="primary danger" data-act="forfeit-confirm">Forfeit</button></div>`;
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
            const me = S.online ? S.online.seat : g.mode === 'human_vs_agent' ? 'Human' : null;
            const gaveUp = g.end_reason === 'forfeit' ? g.forfeited_by : null;
            const headline = gaveUp ? (me ? (gaveUp === me ? 'You forfeited' : `${display(gaveUp)} forfeited. You win!`) : `${display(gaveUp)} forfeited`)
                : tie ? "It's a tie" : me ? (g.winners[0] === me ? 'You win!' : `${S.online ? display(g.winners[0]) : 'Computer'} wins`) : `${display(g.winners[0])} wins`;
            const why: Record<string, string> = { 'rack empty': 'Someone used every tile.', stalled: 'Nobody could play.', 'bag empty+stuck': 'The bag ran out and nobody could play.', 'turn limit': 'Turn limit reached.', forfeit: me && gaveUp === me ? 'That counts as a loss.' : 'The game ended by forfeit.' };
            body = `<h2>${headline}</h2><p class="muted">${why[g.end_reason ?? ''] ?? ''}${S.user && (S.online || g.mode === 'human_vs_agent') ? ' Added to your stats.' : ''}</p>
            <div class="final">${[...g.players].sort((a, b) => b.score - a.score).map(p => `<div class="${g.winners.includes(p.name) ? 'win' : ''}"><span>${display(p.name)}</span><strong>${p.score}</strong></div>`).join('')}</div>
            <div class="modal-actions"><button class="ghost wide" data-act="home">Menu</button>${S.online ? '<button class="primary" data-act="online">New online game</button>' : `<button class="primary" data-act="start" data-mode="${g.mode}">Play again</button>`}</div>`;
            break;
        }
        case 'account': {
            const u = S.user;
            if (!u) break;
            body = `<div class="account-head">${avatarHtml(u)}<div><strong>${u.name}</strong><small>${u.email}</small></div></div>
            <p class="muted small">Your games and stats are saved to this account and follow you to any device.</p>
            <button class="primary" data-act="stats">View my stats</button>
            <button class="ghost wide full" data-act="signout">Sign out</button>`;
            break;
        }
        default:
            return '';
    }
    const dismissable = S.modal === 'help' || S.modal === 'swap' || S.modal === 'pass' || S.modal === 'forfeit' || S.modal === 'account';
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
            case 'online': showOnline(); break;
            case 'online-create': void createOnline(); break;
            case 'online-join': void joinOnline((document.getElementById('code') as HTMLInputElement).value); break;
            case 'online-resume': { const saved = loadSaved(); if (saved) void enterRoom(saved).catch(err => showToast((err as Error).message)); break; }
            case 'copy-link':
                void navigator.clipboard?.writeText(joinLink(S.online!.code))
                    .then(() => showToast('Invite link copied'), () => showToast(`Share this code: ${S.online!.code}`));
                break;
            case 'signin': void signInWithGoogle().catch(err => showToast((err as Error).message)); break;
            case 'signout': closeModal(); void signOut(); break;
            case 'account': openModal('account'); break;
            case 'stats': void openStats(); break;
            case 'resume-saved': { const g = S.saved[Number(actEl.dataset.i)]; if (g) void resumeSaved(g); break; }
            case 'delete-saved': { const g = S.saved[Number(actEl.dataset.i)]; if (g) void deleteSaved(g); break; }
            case 'help': openModal('help'); break;
            case 'close': case 'close-scrim': closeModal(); break;
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
            case 'swap': S.swapPick.clear(); openModal('swap'); break;
            case 'swap-pick': {
                const i = Number(actEl.dataset.i);
                if (S.swapPick.has(i)) S.swapPick.delete(i); else S.swapPick.add(i);
                render(); break;
            }
            case 'swap-confirm': closeModal(); void swap(); break;
            case 'pass': openModal('pass'); break;
            case 'forfeit': openModal('forfeit'); break;
            case 'forfeit-confirm': closeModal(); void forfeit(); break;
            case 'pass-confirm': closeModal(); void pass(); break;
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

// Boot: restore any saved sign-in, then work out where to start: an invite link (?room=CODE) wins, otherwise
// the URL's route, so a reload (or following a link) lands where the user was.
void (async () => {
    S.user = await initAuth(onUserChange);
    if (S.user) { void checkSaving(); void loadSavedGames(); }
    const pending = takePendingRoom();   // an invite link survives the Google sign-in redirect
    if (pending && !new URLSearchParams(location.search).get('room')) history.replaceState(null, '', `${location.pathname}?room=${pending}#/online`);

    const invite = new URLSearchParams(location.search).get('room')?.toUpperCase();
    let route: Route = parseHash(location.hash);
    if (invite) {
        const saved = loadSaved();
        route = saved && saved.code === invite
            ? { screen: 'game', kind: 'room', code: invite }
            : { screen: 'online' };        // not seated yet: the Online screen has the code pre-filled
    }
    replaceRoute(route, route.screen === 'online' && !!invite);   // this is the first entry: adopt it, keep ?room= for the form
    await applyRoute(route);
    render();
})();
