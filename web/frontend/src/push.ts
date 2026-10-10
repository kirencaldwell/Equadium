/**
 * Turn notifications (Web Push). They are ON by default for signed-in players: the first time someone starts, joins
 * or plays in an online game, the browser's own permission prompt appears, and whatever they answer there decides
 * whether notifications arrive. Where permission is already granted, a device is registered silently on sign-in.
 * The bell / account switch is the opt-out (and opt-back-in). The server pushes a message whenever the other player
 * moves in one of their online games (see public/sw.js for what is shown).
 */
import { push as pushApi } from './api';

export type PushStatus =
    | 'unsupported'    // this browser has no web push
    | 'needs-install'  // iPhone/iPad: only works once the site is added to the Home Screen
    | 'unavailable'    // the server has no push keys set up (or can't be reached)
    | 'blocked'        // the player denied the permission in browser settings
    | 'off'
    | 'on';

const WANT_KEY = 'equadium.push.user';       // which account this device is set up to notify
const OPTOUT_KEY = 'equadium.push.optout';   // the player turned notifications off on purpose: never auto-enable
const ASKED_KEY = 'equadium.push.asked';     // we have already shown the permission prompt once on this device

const isIos = () => /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
const isInstalled = () => matchMedia('(display-mode: standalone)').matches || (navigator as unknown as { standalone?: boolean }).standalone === true;
const supported = () => 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;

const read = (): string | null => { try { return localStorage.getItem(WANT_KEY); } catch { return null; } };
const write = (v: string | null) => { try { v ? localStorage.setItem(WANT_KEY, v) : localStorage.removeItem(WANT_KEY); } catch { /* private mode */ } };

const flag = (k: string) => { try { return localStorage.getItem(k) === '1'; } catch { return false; } };
const setFlag = (k: string, on: boolean) => { try { on ? localStorage.setItem(k, '1') : localStorage.removeItem(k); } catch { /* private mode */ } };

let cfg: { enabled: boolean; public_key: string | null } | null = null;   // cached so a tap can ask for permission at once
async function serverConfig() {
    return cfg ??= await pushApi.config();
}

function keyBytes(b64: string): Uint8Array<ArrayBuffer> {
    const pad = '='.repeat((4 - (b64.length % 4)) % 4);
    const raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(raw, c => c.charCodeAt(0));
}

async function registration() {
    return navigator.serviceWorker.register('/sw.js');
}

async function currentSub(): Promise<PushSubscription | null> {
    const reg = await navigator.serviceWorker.getRegistration('/sw.js');
    return reg ? reg.pushManager.getSubscription() : null;
}

export async function pushStatus(userId: string): Promise<PushStatus> {
    if (isIos() && !isInstalled()) return 'needs-install';
    if (!supported()) return 'unsupported';
    try {
        if (!(await serverConfig()).enabled) return 'unavailable';
    } catch { return 'unavailable'; }
    if (Notification.permission === 'denied') return 'blocked';
    try {
        return read() === userId && (await currentSub()) ? 'on' : 'off';
    } catch { return 'off'; }
}

/** Asks permission (must be called from a tap), subscribes this device and tells the server. */
export async function enablePush(userId: string): Promise<void> {
    // With the config already cached nothing is awaited before the prompt, so it still counts as the tap's own.
    const c = cfg ?? await serverConfig();
    if (!c.enabled || !c.public_key) throw new Error('Notifications are not set up on the server yet.');
    setFlag(OPTOUT_KEY, false);
    if (Notification.permission === 'default') setFlag(ASKED_KEY, true);
    const permission = Notification.permission === 'granted' ? 'granted' : await Notification.requestPermission();
    if (permission !== 'granted') throw new Error('Notifications are blocked. Allow them for this site in your browser settings.');
    await subscribeDevice(userId, c.public_key);
}

async function subscribeDevice(userId: string, publicKey: string): Promise<void> {
    const reg = await registration();
    await navigator.serviceWorker.ready;
    const sub = (await reg.pushManager.getSubscription())
        ?? await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(publicKey) });
    await pushApi.subscribe(sub.toJSON() as { endpoint: string; keys: { p256dh: string; auth: string } });
    write(userId);
}

/**
 * The default-on part: called straight from a tap (starting, joining or playing in an online game). Shows the
 * browser's permission prompt once per device; a "no" there (or in settings) is respected and never nagged.
 * Resolves true if this device just became set up.
 */
export async function autoEnablePush(userId: string): Promise<boolean> {
    if (!supported() || (isIos() && !isInstalled()) || flag(OPTOUT_KEY) || !cfg?.enabled || !cfg.public_key) return false;
    if (Notification.permission === 'denied') return false;
    if (Notification.permission === 'default' && flag(ASKED_KEY)) return false;   // asked before and not answered: leave it to the bell
    if (read() === userId && Notification.permission === 'granted' && await currentSub()) return false;
    try { await enablePush(userId); return true; } catch { return false; }
}

export async function disablePush(): Promise<void> {
    setFlag(OPTOUT_KEY, true);   // switched off on purpose: stay off until they switch it back on
    const sub = await currentSub();
    if (sub) {
        try { await pushApi.unsubscribe(sub.endpoint); } catch { /* the server will drop it when the push service says it's gone */ }
        await sub.unsubscribe();
    }
    write(null);
}

/** Signing out: stop this device notifying the account, but remember to resume if the same person signs back in. */
export async function pausePush(): Promise<void> {
    try {
        const sub = await currentSub();
        if (sub) await pushApi.unsubscribe(sub.endpoint);
    } catch { /* best effort */ }
}

/**
 * After sign-in: where the browser has already allowed notifications (and the player hasn't opted out), register
 * this device for the signed-in account with no prompt, and re-register after a rotated subscription. This is
 * what makes notifications default-on on every device the player has already said yes on.
 */
export async function syncPush(userId: string): Promise<void> {
    if (flag(OPTOUT_KEY) || !supported() || (isIos() && !isInstalled()) || Notification.permission !== 'granted') return;
    try {
        const c = await serverConfig();
        if (c.enabled && c.public_key) await subscribeDevice(userId, c.public_key);
    } catch { /* offline or push not configured: try again next time */ }
}

/** The service worker asks an open page to switch to a game when a notification is tapped. */
export function onNotificationOpen(handler: (code: string) => void): void {
    if (!('serviceWorker' in navigator)) return;
    navigator.serviceWorker.addEventListener('message', (e) => {
        if (e.data?.type === 'open-room' && typeof e.data.code === 'string') handler(e.data.code);
    });
}

export const IOS_HINT = 'On iPhone: tap Share, then Add to Home Screen, and open Equadium from your Home Screen to turn on notifications.';
