/** Small line icons (24x24, stroke = currentColor), drawn for this app so they look the same on every device. */
const PATHS: Record<string, string> = {
    // modes
    cpu: '<rect x="6.5" y="6.5" width="11" height="11" rx="2"/><path d="M9.5 3v3.5M14.5 3v3.5M9.5 17.5V21M14.5 17.5V21M3 9.5h3.5M3 14.5h3.5M17.5 9.5H21M17.5 14.5H21"/><circle cx="12" cy="12" r="1.6"/>',
    pair: '<circle cx="9" cy="12" r="5.5"/><circle cx="15" cy="12" r="5.5"/>',
    eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>',
    globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c3 3.2 3 13.8 0 17M12 3.5c-3 3.2-3 13.8 0 17"/>',
    // actions
    shuffle: '<path d="M3.5 7.5h3c3 0 4.8 2 6.2 4.5s3.2 4.5 6.3 4.5h1.5"/><path d="M3.5 16.5h3c1.5 0 2.7-.6 3.7-1.6M13.3 9.2c1-1 2.2-1.7 3.7-1.7h3"/><path d="M18 4.8l2.5 2.7L18 10.2M18 13.8l2.5 2.7L18 19.2"/>',
    undo: '<path d="M8 4.5L4 8.5l4 4"/><path d="M4 8.5h9.5a6 6 0 0 1 0 12H8"/>',
    swap: '<path d="M8 4v15M4.5 7.5L8 4l3.5 3.5M16 20V5M12.5 16.5L16 20l3.5-3.5"/>',
    skip: '<path d="M5.5 5.5l9 6.5-9 6.5z"/><path d="M19 5.5v13"/>',
    flag: '<path d="M5.5 21V3.5"/><path d="M5.5 5c3-2 5.5 2 8.5 0s3.5-1 5 0v8.5c-1.5-1-2.5-1-5 0s-5.5-2-8.5 0"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    minus: '<path d="M5 12h14"/>',
    target: '<circle cx="12" cy="12" r="3.2"/><path d="M12 3v3.5M12 17.5V21M3 12h3.5M17.5 12H21"/>',
    bell: '<path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 2h-15z"/><path d="M10 21a2 2 0 0 0 4 0"/>',
    'bell-on': '<path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 2h-15z" fill="currentColor" fill-opacity=".18"/><path d="M10 21a2 2 0 0 0 4 0"/>',
    close: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    play: '<path d="M7.5 5l11 7-11 7z"/>',
    pause: '<path d="M8.5 5v14M15.5 5v14"/>',
    'chevron-left': '<path d="M15 5l-7 7 7 7"/>',
    'chevron-right': '<path d="M9 5l7 7-7 7"/>',
};

export function ico(name: keyof typeof PATHS | string): string {
    return `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">${PATHS[name] ?? ''}</svg>`;
}
