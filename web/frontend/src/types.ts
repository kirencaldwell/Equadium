export interface Tile {
    symbol: string;
    points: number;
    expr_multiplier: number;
}

export interface Player {
    name: string;
    score: number;
    rack: Tile[];
    equals_available: boolean;
}

export interface LastMove {
    player: string;
    action: 'play' | 'swap' | 'pass';
    ok: boolean;
    score_delta: number;
    error: string | null;
    tiles: string[] | null;
    cells: [number, number][] | null;
}

export type Mode = 'human_vs_agent' | 'human_vs_human' | 'agent_vs_agent';

export interface GameState {
    board: { width: number; height: number; grid: (Tile | null)[][] };
    players: Player[];
    current_player: string;
    equals_pile_count: number;
    mode: Mode;
    seats: Record<string, 'human' | 'agent'>;
    game_over: boolean;
    end_reason: string | null;
    winners: string[];
    turns_played: number;
    bag_count: number;
    last_move: LastMove | null;
}

export interface Placed {
    r: number;
    c: number;
    tile: Tile;
    /** index into the player's rack, or 'eq' for the free "=" tile */
    src: number | 'eq';
}

export interface Preview {
    valid: boolean;
    reason: string | null;
    equations: string[];
    score: number;
}
