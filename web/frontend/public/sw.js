/* Equadium service worker: shows a notification when the other player moves. No caching, no offline mode. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
    let data = {};
    try { data = event.data ? event.data.json() : {}; } catch { /* not JSON: show the defaults */ }
    const title = data.title || 'Equadium';
    event.waitUntil((async () => {
        // Browsers (Safari especially) want every push to show something, so always show it. If the player is already
        // looking at the game the page has updated itself, so take the notification back down after a moment.
        await self.registration.showNotification(title, {
            body: data.body || "It's your turn.",
            tag: data.tag || 'equadium',      // one notification per game: a new move replaces the last
            renotify: true,
            icon: '/icons/icon-192.png',
            badge: '/icons/icon-192.png',
            data: { url: data.url || '/', code: data.code || null },
        });
        const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
        if (wins.some((w) => w.focused)) {
            await new Promise((r) => setTimeout(r, 4000));
            (await self.registration.getNotifications({ tag: data.tag || 'equadium' })).forEach((n) => n.close());
        }
    })());
});

self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const { url, code } = event.notification.data || {};
    event.waitUntil((async () => {
        const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
        const open = wins[0];
        if (open) {
            await open.focus();
            if (code) open.postMessage({ type: 'open-room', code });   // the page switches to that game without reloading
        } else {
            await self.clients.openWindow(url || '/');
        }
    })());
});
