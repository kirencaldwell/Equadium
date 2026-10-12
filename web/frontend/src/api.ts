import { accessToken, refreshToken } from './auth';
import { report } from './diag';
import type { GameState, Mode, Placed, Preview, SavedGame, Stats } from './types';

// Empty in dev (Vite proxies to the local API); set VITE_API_URL to the hosted API in production.
const BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/$/, '');

async function call<T>(url: string, init: RequestInit = {}, retried = false): Promise<T> {
    // Signed-in players identify themselves with their Firebase ID token.
    const headers = new Headers(init.headers);
    const token = await accessToken();
    if (token) headers.set('Authorization', `Bearer ${token}`);
    let res: Response;
    try {
        res = await fetch(BASE + url, { ...init, headers });
    } catch {
        // fetch only throws when no response arrived at all: server asleep or down, offline, or blocked.
        throw new Error("Couldn't reach the game server. Please try again in a moment.");
    }
    if (res.status === 401 && token && !retried && await refreshToken()) return call<T>(url, init, true);
    if (!res.ok) {
        let detail = res.statusText;
        try { detail = (await res.json()).detail ?? detail; } catch { /* not json */ }
        if (res.status >= 500) {          // our fault, not the player's: say so plainly and keep the details for the owner
            report(`server ${res.status}`, `${init.method ?? 'GET'} ${url}: ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`);
            throw new Error('Something went wrong on our side. Please try again in a moment.');
        }
        throw new Error(typeof detail === 'string' ? detail : 'That request was not accepted.');
    }
    return res.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
});

const movePayload = (placed: Placed[], direction: string) => ({
    tiles_to_play: placed.map(p => ({ r: p.r, c: p.c, tile: p.tile })),
    direction,
});

export interface ActionResult {
    status: 'success' | 'failed';
    error: string | null;
    score_delta: number;
    agent_moves: { player: string; action: string; score_delta: number; tiles: string[] | null }[];
    game_over: boolean;
}

export interface RoomJoin { code: string; seat: string; token: string }
export type RoomPoll = { changed: false; version: number } | { changed: true; version: number; seat: string; state: GameState };

// Guests prove their seat with a secret token; signed-in players also match by account,
// so a seat token isn't needed when resuming on a new device.
const authed = (token: string, init: RequestInit = {}): RequestInit => ({
    ...init,
    headers: { ...(init.headers as Record<string, string> | undefined), ...(token ? { 'X-Player-Token': token } : {}) },
});

export const room = {
    create: (name: string) => call<RoomJoin>('/rooms', json({ name })),
    join: (code: string, name: string, token = '') =>
        call<RoomJoin>(`/rooms/${encodeURIComponent(code)}/join`, authed(token, json({ name }))),
    state: (code: string, token: string, since?: number) =>
        call<RoomPoll>(`/rooms/${code}${since === undefined ? '' : `?since=${since}`}`, authed(token)),
    validate: (code: string, token: string, placed: Placed[], direction: string) =>
        call<Preview>(`/rooms/${code}/validate_move`, authed(token, json(movePayload(placed, direction)))),
    play: (code: string, token: string, placed: Placed[], direction: string) =>
        call<ActionResult>(`/rooms/${code}/play`, authed(token, json(movePayload(placed, direction)))),
    swap: (code: string, token: string, indices: number[]) =>
        call<ActionResult>(`/rooms/${code}/swap`, authed(token, json({ tile_indices: indices }))),
    pass: (code: string, token: string) =>
        call<ActionResult>(`/rooms/${code}/pass`, authed(token, { method: 'POST' })),
    forfeit: (code: string, token: string) =>
        call<ActionResult>(`/rooms/${code}/forfeit`, authed(token, { method: 'POST' })),
    // A guest who lost their seat token asks the player still in the game to hand the seat back.
    seats: (code: string) => call<{ full: boolean; seats: { seat: string; label: string }[] }>(`/rooms/${encodeURIComponent(code)}/seats`),
    reclaim: (code: string, seat: string) => call<{ request_id: string; secret: string }>(`/rooms/${encodeURIComponent(code)}/reclaim`, json({ seat })),
    reclaimStatus: (code: string, id: string, secret: string) =>
        call<{ status: 'pending' | 'denied' | 'approved'; seat?: string; token?: string }>(`/rooms/${encodeURIComponent(code)}/reclaim/${id}`, { headers: { 'X-Reclaim-Secret': secret } }),
    answerReclaim: (code: string, token: string, id: string, approve: boolean) =>
        call<{ approved: boolean }>(`/rooms/${code}/reclaim/${id}/${approve ? 'approve' : 'deny'}`, authed(token, { method: 'POST' })),
};

export interface PushSub { endpoint: string; keys: { p256dh: string; auth: string } }
export const push = {
    config: () => call<{ enabled: boolean; public_key: string | null }>('/push/config'),
    subscribe: (sub: PushSub) => call<{ subscribed: boolean }>('/push/subscribe', json(sub)),
    test: () => call<{ devices: number; results: { ok: boolean; service: string; error?: string }[] }>('/push/test', { method: 'POST' }),
    unsubscribe: (endpoint: string) => call<{ subscribed: boolean }>('/push/unsubscribe', json({ endpoint })),
};

export const me = {
    profile: () => call<{ id: string; name: string | null; saving: string }>('/me'),
    games: () => call<{ games: SavedGame[] }>('/me/games'),
    stats: () => call<Stats>('/me/stats'),
};

export const api = {
    create: (mode: Mode) => call<{ game_id: string }>('/games/create', json({ mode })),
    state: (id: string) => call<GameState>(`/games/${id}`),
    validate: (id: string, placed: Placed[], direction: string) =>
        call<Preview>(`/games/${id}/validate_move`, json(movePayload(placed, direction))),
    play: (id: string, player: string, placed: Placed[], direction: string) =>
        call<ActionResult>(`/games/${id}/play?player=${encodeURIComponent(player)}`, json(movePayload(placed, direction))),
    swap: (id: string, player: string, indices: number[]) =>
        call<ActionResult>(`/games/${id}/swap?player=${encodeURIComponent(player)}`, json({ tile_indices: indices })),
    pass: (id: string, player: string) =>
        call<ActionResult>(`/games/${id}/pass?player=${encodeURIComponent(player)}`, { method: 'POST' }),
    forfeit: (id: string, player: string) =>
        call<ActionResult>(`/games/${id}/forfeit?player=${encodeURIComponent(player)}`, { method: 'POST' }),
    remove: (id: string) => call<{ deleted: boolean }>(`/games/${id}`, { method: 'DELETE' }),
    agentStep: (id: string) => call<{ game_over: boolean }>(`/games/${id}/agent_step`, { method: 'POST' }),
};
