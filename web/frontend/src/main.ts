import './style.css';

interface Tile {
    symbol: string;
    points: number;
    expr_multiplier: number;
}

interface Player {
    name: string;
    score: number;
    rack: Tile[];
    equals_available: number;
}

interface Board {
    width: number;
    height: number;
    grid: (Tile | null)[][];
}

interface GameState {
    board: Board;
    players: Player[];
    current_player: string;
    equals_pile_count: number;
}

interface PlacedTile {
    r: number;
    c: number;
    tile: Tile;
    valid: boolean;
}

const TILE_ASSETS: Record<string, string> = {
    "x": "/assets/tiles/x.svg",
    "y": "/assets/tiles/y.svg",
    "=": "/assets/tiles/=.svg",
    "+": "/assets/tiles/+.svg",
    "-": "/assets/tiles/-.svg",
    "*": "/assets/tiles/_mul_.svg",
    "/": "/assets/tiles/_div_.svg",
    "(": "/assets/tiles/_paren_.svg",
    ")": "/assets/tiles/_paren_close_.svg",
    "d/dx(": "/assets/tiles/d_div_dx_paren_.svg",
    "int(": "/assets/tiles/int_paren_.svg",
    "C": "/assets/tiles/C.svg",
    "x**2": "/assets/tiles/x_mul__mul_2.svg",
    "x**3": "/assets/tiles/x_mul__mul_3.svg",
    "x**4": "/assets/tiles/x_mul__mul_4.svg",
    "1/2": "/assets/tiles/1_div_2.svg",
    "1/3": "/assets/tiles/1_div_3.svg",
    "1/6": "/assets/tiles/1_div_6.svg",
    "1/63": "/assets/tiles/1_div_63.svg",
    "(x+a)": "/assets/tiles/_paren_x+a_paren_close_.svg",
    "(x+b)": "/assets/tiles/_paren_x+b_paren_close_.svg",
    "e^x": "/assets/tiles/e_pow_x.svg",
    "cos(x)": "/assets/tiles/cos_paren_x_paren_close_.svg",
    "b": "/assets/tiles/b.svg",
    "2": "/assets/tiles/2.svg",
    "3": "/assets/tiles/3.svg",
    "4": "/assets/tiles/4.svg",
};

function getTileContent(symbol: string): string {
    if (TILE_ASSETS[symbol]) {
        return `<img src="${TILE_ASSETS[symbol]}" alt="${symbol}">`;
    }
    return symbol;
}

let gameId: string | null = null;
let boardState: Board | null = null;
let playerRack: Tile[] = [];
let placedTiles: PlacedTile[] = [];
let isSwapMode = false;
let selectedForSwap: number[] = [];
let playerScores: Record<string, number> = {};

// DOM Elements
const playBtn = document.getElementById('playBtn') as HTMLButtonElement;
const resetBtn = document.getElementById('resetBtn') as HTMLButtonElement;
const swapBtn = document.getElementById('swapBtn') as HTMLButtonElement;
const drawEqualsBtn = document.getElementById('drawEqualsBtn') as HTMLButtonElement;
const newGameBtn = document.getElementById('newGameBtn') as HTMLButtonElement;
const equalsCountEl = document.getElementById('equals-count') as HTMLSpanElement;
const scoreListEl = document.getElementById('score-list') as HTMLDivElement;
const boardEl = document.getElementById('board') as HTMLDivElement;
const rackEl = document.getElementById('rack') as HTMLDivElement;

// Event Listeners
newGameBtn.addEventListener('click', createNewGame);
playBtn.addEventListener('click', submitMove);
resetBtn.addEventListener('click', resetTurn);
swapBtn.addEventListener('click', toggleSwapMode);
drawEqualsBtn.addEventListener('click', drawEqualsTile);

async function createNewGame() {
    const response = await fetch('/games/create', { method: 'POST' });
    const data = await response.json();
    gameId = data.game_id;
    playerScores = {};
    updateGame();
}

async function updateGame() {
    if (!gameId) return;
    const response = await fetch(`/games/${gameId}`);
    const data: GameState = await response.json();
    boardState = data.board;
    playerRack = data.players[0].rack;
    if (equalsCountEl) {
        equalsCountEl.innerText = data.equals_pile_count.toString();
    }

    if (scoreListEl) {
        scoreListEl.innerHTML = '';
        data.players.forEach(p => {
            const prevScore = playerScores[p.name] || 0;
            const lastTurnScore = p.score - prevScore;
            playerScores[p.name] = p.score;
            const pEl = document.createElement('div');
            pEl.innerHTML = `<strong>${p.name}</strong>: ${p.score} <small>(+${lastTurnScore})</small>`;
            scoreListEl.appendChild(pEl);
        });
    }
    renderBoard();
    renderRack();
}

function renderBoard() {
    if (!boardState || !boardEl) return;
    boardEl.style.gridTemplateColumns = `repeat(${boardState.width}, 50px)`;
    boardEl.innerHTML = '';
    for (let r = 0; r < boardState.height; r++) {
        for (let c = 0; c < boardState.width; c++) {
            const cell = document.createElement('div');
            cell.className = 'cell';
            cell.dataset.r = r.toString();
            cell.dataset.c = c.toString();
            const existingTile = boardState.grid[r][c];
            const placed = placedTiles.find(p => p.r === r && p.c === c);
            if (existingTile) {
                cell.innerHTML = getTileContent(existingTile.symbol);
            }
            if (placed) {
                cell.innerHTML = getTileContent(placed.tile.symbol);
                cell.classList.add(placed.valid ? 'valid' : 'invalid');
                cell.draggable = true;
                cell.ondragstart = (e) => {
                    if (e.dataTransfer) {
                        e.dataTransfer.setData('source', `${r},${c}`);
                    }
                };
            }
            cell.ondragover = (e) => e.preventDefault();
            cell.ondrop = (e) => handleDrop(e, r, c);
            boardEl.appendChild(cell);
        }
    }
}

function renderRack() {
    if (!rackEl) return;
    rackEl.innerHTML = '';
    playerRack.forEach((tile, index) => {
        const tileEl = document.createElement('div');
        tileEl.className = 'tile';
        if (isSwapMode) {
            tileEl.style.cursor = 'pointer';
            if (selectedForSwap.includes(index)) {
                tileEl.style.background = 'yellow';
            }
            tileEl.onclick = () => toggleSelectForSwap(index);
        } else {
            tileEl.style.cursor = 'grab';
            tileEl.draggable = true;
            tileEl.dataset.index = index.toString();
            tileEl.ondragstart = (e) => {
                if (e.dataTransfer) {
                    e.dataTransfer.setData('source', 'rack:' + index);
                }
            };
        }
        tileEl.innerHTML = getTileContent(tile.symbol);
        rackEl.appendChild(tileEl);
    });
}

function handleDrop(e: DragEvent, r: number, c: number) {
    e.preventDefault();
    if (!boardState || boardState.grid[r][c] !== null) return;
    if (!e.dataTransfer) return;
    
    const source = e.dataTransfer.getData('source');
    if (!source) return;

    const [type, indexStr] = source.split(':');

    let tile: Tile;
    if (type === 'rack') {
        const index = parseInt(indexStr, 10);
        tile = playerRack.splice(index, 1)[0];
    } else {
        const [srcR, srcC] = type.split(',').map(n => parseInt(n, 10));
        const placedIndex = placedTiles.findIndex(p => p.r === srcR && p.c === srcC);
        if (placedIndex === -1) return;
        tile = placedTiles.splice(placedIndex, 1)[0].tile;
    }

    placedTiles.push({ r, c, tile, valid: false });
    if (resetBtn) {
        resetBtn.style.display = 'block';
    }
    renderBoard();
    renderRack();
    validateMove();
}

function getMoveDirection(): string {
    if (placedTiles.length < 2) return "H";
    const r1 = placedTiles[0].r;
    const r2 = placedTiles[1].r;
    return r1 === r2 ? "H" : "V";
}

async function validateMove() {
    if (!gameId) return;
    const response = await fetch(`/games/${gameId}/validate_move`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            tiles_to_play: placedTiles.map(p => ({ r: p.r, c: p.c, tile: p.tile })),
            direction: getMoveDirection()
        })
    });
    const data = await response.json();
    placedTiles.forEach(p => p.valid = data.valid);
    if (playBtn) {
        playBtn.style.display = data.valid ? 'block' : 'none';
    }
    renderBoard();
}

function resetTurn() {
    placedTiles.forEach(p => playerRack.push(p.tile));
    placedTiles = [];
    if (resetBtn) resetBtn.style.display = 'none';
    if (playBtn) playBtn.style.display = 'none';
    renderBoard();
    renderRack();
}

async function submitMove() {
    if (!gameId) return;
    await fetch(`/games/${gameId}/play`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            tiles_to_play: placedTiles.map(p => ({ r: p.r, c: p.c, tile: p.tile })),
            direction: getMoveDirection()
        })
    });
    placedTiles = [];
    if (resetBtn) resetBtn.style.display = 'none';
    if (playBtn) playBtn.style.display = 'none';
    updateGame();
}

function toggleSwapMode() {
    isSwapMode = !isSwapMode;
    if (swapBtn) {
        if (isSwapMode) {
            swapBtn.style.background = 'red';
            swapBtn.innerText = 'Confirm Swap';
        } else {
            if (selectedForSwap.length > 0) performSwap();
            swapBtn.style.background = '';
            swapBtn.innerText = 'Swap Tiles';
        }
    }
    renderRack();
}

function toggleSelectForSwap(index: number) {
    if (selectedForSwap.includes(index)) {
        selectedForSwap = selectedForSwap.filter(i => i !== index);
    } else {
        selectedForSwap.push(index);
    }
    renderRack();
}

async function performSwap() {
    if (!gameId) return;
    await fetch(`/games/${gameId}/swap`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tile_indices: selectedForSwap })
    });
    selectedForSwap = [];
    updateGame();
}

async function drawEqualsTile() {
    if (!gameId) return;
    await fetch(`/games/${gameId}/draw_equals`, { method: 'POST' });
    updateGame();
}
