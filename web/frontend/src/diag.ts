// Diagnostics go to the server's log (which only the site owner can read), not onto the screen.
const BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/$/, '');
const seen = new Set<string>();

/** Reports a problem for the owner. Never throws, never shown to the player, and each distinct report is sent once per visit. */
export function report(kind: string, detail: unknown = ''): void {
    try {
        const text = detail instanceof Error ? detail.message : String(detail ?? '');
        const key = `${kind}|${text}`;
        if (seen.has(key) || seen.size > 50) return;
        seen.add(key);
        console.warn(`[equadium] ${kind}`, text);
        void fetch(`${BASE}/client-log`, {
            method: 'POST', keepalive: true,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ kind, detail: text.slice(0, 500) }),
        }).catch(() => { /* the server may be the problem: nothing more to do */ });
    } catch { /* diagnostics must never break the app */ }
}
