import { initializeApp } from 'firebase/app';
import {
    GoogleAuthProvider, browserLocalPersistence, browserPopupRedirectResolver, indexedDBLocalPersistence,
    initializeAuth, onAuthStateChanged,
    signInWithPopup, signInWithRedirect, signOut as fbSignOut, type Auth, type User as FbUser,
} from 'firebase/auth';

const cfg = {
    apiKey: import.meta.env.VITE_FIREBASE_API_KEY as string | undefined,
    authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN as string | undefined,
    projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID as string | undefined,
    appId: import.meta.env.VITE_FIREBASE_APP_ID as string | undefined,
};

/** False when the Firebase env vars are missing: the app then runs guest-only (no sign-in UI). */
export const authAvailable = Boolean(cfg.apiKey && cfg.authDomain && cfg.projectId);

// Only initialise when configured, so a missing config can't crash the whole app at load.
const auth: Auth | null = authAvailable
    ? initializeAuth(initializeApp({ apiKey: cfg.apiKey, authDomain: cfg.authDomain, projectId: cfg.projectId, appId: cfg.appId }),
        // IndexedDB first, localStorage as a fallback. The resolver is required for popup/redirect sign-in
        // when using initializeAuth (getAuth adds it implicitly).
        { persistence: [indexedDBLocalPersistence, browserLocalPersistence], popupRedirectResolver: browserPopupRedirectResolver })
    : null;

export interface User { id: string; name: string; email: string; avatar: string }

function toUser(u: FbUser | null): User | null {
    if (!u) return null;
    return {
        id: u.uid,
        email: u.email ?? '',
        name: u.displayName ?? u.email?.split('@')[0] ?? 'Player',
        avatar: u.photoURL ?? '',
    };
}

/** A fresh-enough ID token (Firebase refreshes it automatically), sent as `Authorization: Bearer`. */
export async function accessToken(): Promise<string | null> {
    return (await auth?.currentUser?.getIdToken()) ?? null;
}

/** Forces a token refresh (used when the API answers 401). Returns whether we still have a session. */
export async function refreshToken(): Promise<boolean> {
    try { return Boolean(await auth?.currentUser?.getIdToken(true)); } catch { return false; }
}

/** Restores a saved session (also completes a pending redirect sign-in), then reports every change. */
export function initAuth(onChange: (user: User | null) => void): Promise<User | null> {
    if (!auth) return Promise.resolve(null);
    return new Promise(resolve => {
        let first = true;
        onAuthStateChanged(auth, fbUser => {
            const user = toUser(fbUser);
            if (first) { first = false; resolve(user); } else onChange(user);
        });
    });
}

const PENDING_ROOM = 'equadium.pendingRoom';

const isPopupProblem = (code: string) =>
    ['auth/popup-blocked', 'auth/operation-not-supported-in-this-environment'].includes(code);

/** Turns Firebase's error codes into something a person (or whoever is setting this up) can act on. */
function friendlyAuthError(e: unknown): Error {
    const code = (e as { code?: string }).code ?? '';
    const messages: Record<string, string> = {
        'auth/unauthorized-domain': "This site isn't allowed to sign in yet. Add its domain in Firebase → Authentication → Settings → Authorized domains.",
        'auth/operation-not-allowed': 'Google sign-in is not enabled. Turn it on in Firebase → Authentication → Sign-in method.',
        'auth/invalid-api-key': 'The Firebase API key in this build is invalid. Check the VITE_FIREBASE_* settings.',
        'auth/configuration-not-found': 'Firebase Authentication is not set up for this project yet.',
        'auth/network-request-failed': "Couldn't reach Google. Check your connection and try again.",
        'auth/internal-error': "Couldn't reach Google to sign in. Check your connection (or any ad/privacy blocker) and try again.",
    };
    return new Error(messages[code] ?? `Sign-in failed (${code || (e as Error).message})`);
}

export async function signInWithGoogle(): Promise<void> {
    if (!auth) throw new Error('Sign-in is not configured');
    const provider = new GoogleAuthProvider();
    provider.setCustomParameters({ prompt: 'select_account' });
    try {
        await signInWithPopup(auth, provider);
    } catch (e) {
        const code = (e as { code?: string }).code ?? '';
        if (code === 'auth/popup-closed-by-user' || code === 'auth/cancelled-popup-request') return;   // user changed their mind
        if (!isPopupProblem(code)) throw friendlyAuthError(e);
        // Pop-ups are blocked (common on phones): fall back to a full-page redirect. Remember an
        // invite link's ?room= code, since the page reloads.
        try {
            const room = new URLSearchParams(location.search).get('room');
            if (room) sessionStorage.setItem(PENDING_ROOM, room);
        } catch { /* storage unavailable */ }
        try { await signInWithRedirect(auth, provider); } catch (e2) { throw friendlyAuthError(e2); }
    }
}

/** An invite code stashed before a redirect sign-in, if any (consumed once). */
export function takePendingRoom(): string | null {
    try {
        const r = sessionStorage.getItem(PENDING_ROOM);
        sessionStorage.removeItem(PENDING_ROOM);
        return r;
    } catch { return null; }
}

export async function signOut(): Promise<void> {
    if (auth) await fbSignOut(auth);
}
