import type { GameState, Mode, Placed, Preview } from './types';

// Empty in dev (Vite proxies to the local API); set VITE_API_URL to the hosted API in production.
const BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/$/, '');

async function call<T>(url: string, init?: RequestInit): Promise<T> {
    const res = await fetch(BASE + url, init);
    if (!res.ok) {
        let detail = res.statusText;
        try { detail = (await res.json()).detail ?? detail; } catch { /* not json */ }
        throw new Error(detail);
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
    agentStep: (id: string) => call<{ game_over: boolean }>(`/games/${id}/agent_step`, { method: 'POST' }),
};
