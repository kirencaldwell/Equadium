/**
 * Turn notifications (Web Push). A player turns them on once per device; the server then pushes a message
 * whenever the other player moves in one of their online games (see public/sw.js for what is shown).
 */
import { push as pushApi } from './api';

export type PushStatus =
    | 'unsupported'    // this browser has no web push
    | 'needs-install'  // iPhone/iPad: only works once the site is added to the Home Screen
    | 'unavailable'    // the server has no push keys set up (or can't be reached)
    | 'blocked'        // the player denied the permission in browser settings
    | 'off'
    | 'on';

const WANT_KEY = 'equadium.push.user';   // which account this device is set up to notify

const isIos = () => /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
const isInstalled = () => matchMedia('(display-mode: standalone)').matches || (navigator as unknown as { standalone?: boolean }).standalone === true;
const supported = () => 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;

const read = (): string | null => { try { return localStorage.getItem(WANT_KEY); } catch { return null; } };
const write = (v: string | null) => { try { v ? localStorage.setItem(WANT_KEY, v) : localStorage.removeItem(WANT_KEY); } catch { /* private mode */ } };

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
        if (!(await pushApi.config()).enabled) return 'unavailable';
    } catch { return 'unavailable'; }
    if (Notification.permission === 'denied') return 'blocked';
    try {
        return read() === userId && (await currentSub()) ? 'on' : 'off';
    } catch { return 'off'; }
}

/** Asks permission (must be called from a tap), subscribes this device and tells the server. */
export async function enablePush(userId: string): Promise<void> {
    const cfg = await pushApi.config();
    if (!cfg.enabled || !cfg.public_key) throw new Error('Notifications are not set up on the server yet.');
    const permission = await Notification.requestPermission();
    if (permission !== 'granted') throw new Error('Notifications are blocked. Allow them for this site in your browser settings.');
    const reg = await registration();
    await navigator.serviceWorker.ready;
    const sub = (await reg.pushManager.getSubscription())
        ?? await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(cfg.public_key) });
    await pushApi.subscribe(sub.toJSON() as { endpoint: string; keys: { p256dh: string; auth: string } });
    write(userId);
}

export async function disablePush(): Promise<void> {
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

/** After sign-in: if this device was set up for this account, re-register it (subscriptions can rotate). */
export async function syncPush(userId: string): Promise<void> {
    if (read() !== userId || !supported() || Notification.permission !== 'granted') return;
    try {
        const sub = await currentSub();
        if (sub) await pushApi.subscribe(sub.toJSON() as { endpoint: string; keys: { p256dh: string; auth: string } });
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
