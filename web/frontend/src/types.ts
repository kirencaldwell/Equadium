export interface Tile {
    symbol: string;
    points: number;
    expr_multiplier: number;
}

export interface Player {
    name: string;
    score: number;
    rack: Tile[];
    /** online games hide the opponent's rack and send only its size */
    rack_count?: number;
    equals_available: boolean;
}

export interface LastMove {
    player: string;
    action: 'play' | 'swap' | 'pass' | 'forfeit';
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
    /** seat name of the player who gave up, if the game ended by forfeit */
    forfeited_by: string | null;
    winners: string[];
    turns_played: number;
    bag_count: number;
    last_move: LastMove | null;
    /** online games only */
    labels?: Record<string, string>;
    joined?: boolean;
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
    equation_tiles?: string[][];   // the same equations as lists of tile symbols
    reason_tiles?: string[] | null; // the line that failed, as tile symbols
    score: number;
}

/** An unfinished game that the signed-in player can resume on any device. */
export interface SavedGame {
    kind: 'solo' | 'room';
    id: string;
    code: string | null;
    mode: Mode;
    status: 'waiting' | 'active';
    seat: string | null;
    /** only present for rooms: lets the usual seat-token flow resume the game */
    token: string | null;
    current_player: string | null;
    your_turn: boolean;
    players: { name: string; label: string | null; score: number }[];
    turns_played: number;
    updated_at: string;
}

export interface Stats {
    games: number; wins: number; losses: number; ties: number;
    win_rate: number; avg_score: number; best_score: number; best_play: number;
    total_plays: number; win_streak: number;
    vs_computer: { games: number; wins: number };
    vs_humans: { games: number; wins: number };
    recent: { game_id: string; mode: string; opponent: 'agent' | 'human'; outcome: 'win' | 'loss' | 'tie'; score: number; opp_score: number; finished_at: string }[];
}
