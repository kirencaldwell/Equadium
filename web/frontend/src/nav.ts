/**
 * Browser-history navigation. Every screen has a URL, so the browser's Back/Forward buttons (and a phone's
 * back gesture) move between screens instead of leaving the site. Routes live in the hash so the page works
 * from any static host without server-side fallbacks, and the `?room=CODE` invite links keep working.
 *
 *   (empty)          home
 *   #/online         create / join an online game
 *   #/stats          the signed-in player's stats
 *   #/game/<id>      a solo game (also pass & play, and watching bots)
 *   #/room/<CODE>    an online game
 */
export type Route =
    | { screen: 'home' }
    | { screen: 'online' }
    | { screen: 'stats' }
    | { screen: 'game'; kind: 'solo'; id: string }
    | { screen: 'game'; kind: 'room'; code: string };

/** What we store in each history entry. `idx` counts how deep into *our* entries we are (0 = first). */
export interface NavState { v: 1; idx: number; route: Route; modal?: string }

export function routeToHash(route: Route): string {
    switch (route.screen) {
        case 'home': return '';
        case 'online': return '#/online';
        case 'stats': return '#/stats';
        case 'game': return route.kind === 'room' ? `#/room/${route.code}` : `#/game/${route.id}`;
    }
}

export function parseHash(hash: string): Route {
    const [, kind, arg] = hash.replace(/^#/, '').split('/');
    if (kind === 'online') return { screen: 'online' };
    if (kind === 'stats') return { screen: 'stats' };
    if (kind === 'game' && arg) return { screen: 'game', kind: 'solo', id: decodeURIComponent(arg) };
    if (kind === 'room' && arg) return { screen: 'game', kind: 'room', code: decodeURIComponent(arg).toUpperCase() };
    return { screen: 'home' };
}

export const sameRoute = (a: Route, b: Route) => routeToHash(a) === routeToHash(b);

const isNavState = (s: unknown): s is NavState => !!s && typeof s === 'object' && (s as NavState).v === 1;

export function currentState(): NavState {
    return isNavState(history.state) ? history.state : { v: 1, idx: 0, route: parseHash(location.hash) };
}

const urlFor = (route: Route, keepSearch: boolean) => location.pathname + (keepSearch ? location.search : '') + routeToHash(route);

/** Records `route` as a new history entry. */
export function pushRoute(route: Route, modal?: string): void {
    const idx = currentState().idx + 1;
    history.pushState({ v: 1, idx, route, ...(modal ? { modal } : {}) } satisfies NavState, '', urlFor(route, false));
}

/** Overwrites the current history entry with `route` (no new entry). */
export function replaceRoute(route: Route, keepSearch = false): void {
    history.replaceState({ v: 1, idx: currentState().idx, route } satisfies NavState, '', urlFor(route, keepSearch));
}

/** Adds an entry for an open pop-up on top of the current route, so Back closes the pop-up first. */
export function pushModal(modal: string): void {
    const cur = currentState();
    history.pushState({ v: 1, idx: cur.idx + 1, route: cur.route, modal } satisfies NavState, '', location.href);
}
