import { createClient, type Session, type SupabaseClient } from '@supabase/supabase-js';

const URL = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const KEY = import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined;

/** False when the Supabase env vars are missing: the app then runs guest-only (no sign-in UI). */
export const authAvailable = Boolean(URL && KEY);

// Only create the client when configured; createClient() throws on an empty URL.
const client: SupabaseClient | null = authAvailable
    ? createClient(URL!, KEY!, { auth: { flowType: 'pkce', persistSession: true, autoRefreshToken: true, detectSessionInUrl: true } })
    : null;

export interface User { id: string; name: string; email: string; avatar: string }

let session: Session | null = null;

function toUser(s: Session | null): User | null {
    if (!s) return null;
    const m = (s.user.user_metadata ?? {}) as Record<string, string | undefined>;
    return {
        id: s.user.id,
        email: s.user.email ?? '',
        name: m.full_name ?? m.name ?? s.user.email?.split('@')[0] ?? 'Player',
        avatar: m.avatar_url ?? m.picture ?? '',
    };
}

/** The current access token (sent as `Authorization: Bearer`), or null for guests. */
export const accessToken = (): string | null => session?.access_token ?? null;

/** Forces a token refresh (used when the API answers 401). Returns whether we still have a session. */
export async function refreshToken(): Promise<boolean> {
    if (!client) return false;
    const { data, error } = await client.auth.refreshSession();
    if (error || !data.session) return false;
    session = data.session;
    return true;
}

/** Restores any saved session (also completes a pending OAuth redirect) and then reports changes. */
export async function initAuth(onChange: (user: User | null) => void): Promise<User | null> {
    if (!client) return null;
    const { data } = await client.auth.getSession();
    session = data.session;
    // Don't call back into supabase from inside this listener; just record and notify.
    client.auth.onAuthStateChange((_event, s) => { session = s; onChange(toUser(s)); });
    return toUser(session);
}

const PENDING_ROOM = 'equadium.pendingRoom';

export async function signInWithGoogle(): Promise<void> {
    if (!client) throw new Error('Sign-in is not configured');
    // The redirect URL must match Supabase's allow-list exactly, so it carries no query string;
    // remember an invite link's ?room= across the round trip instead.
    try {
        const room = new URLSearchParams(location.search).get('room');
        if (room) sessionStorage.setItem(PENDING_ROOM, room);
    } catch { /* storage unavailable */ }
    const { error } = await client.auth.signInWithOAuth({
        provider: 'google',
        options: { redirectTo: location.origin + location.pathname },
    });
    if (error) throw error;
}

/** An invite code stashed before the OAuth redirect, if any (consumed once). */
export function takePendingRoom(): string | null {
    try {
        const r = sessionStorage.getItem(PENDING_ROOM);
        sessionStorage.removeItem(PENDING_ROOM);
        return r;
    } catch { return null; }
}

export async function signOut(): Promise<void> {
    await client?.auth.signOut();
    session = null;
}
