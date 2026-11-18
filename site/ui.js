// The page around the map, with the look of vizsim/unfallkarte: one fast tooltip
// for every [data-tip], panel sections that fold, and the layout variables that
// follow the timeline and the attribution.

export const MOBILE_QUERY = '(max-width: 640px)';

const TIP_DELAY_MS = 100;
const TIP_TOUCH_HIDE_MS = 6000;
const TIP_GAP = 8;

// One tooltip element on <body>, placed with position: fixed, so neither the
// scrolling panel nor a popup can clip it. Delegated, so content made later
// (popups, the aerial chip) is covered without wiring.
export function setupTooltips() {
    const tip = document.createElement('div');
    tip.className = 'app-tooltip';
    tip.setAttribute('role', 'tooltip');
    document.body.appendChild(tip);
    let current = null;
    let timer = null;

    const place = (target) => {
        const r = target.getBoundingClientRect();
        const left = Math.max(TIP_GAP, Math.min(r.left + r.width / 2 - tip.offsetWidth / 2,
            window.innerWidth - tip.offsetWidth - TIP_GAP));
        let top = r.top - tip.offsetHeight - TIP_GAP;
        if (top < TIP_GAP) top = r.bottom + TIP_GAP;
        tip.style.left = `${Math.round(left)}px`;
        tip.style.top = `${Math.round(top)}px`;
    };
    const show = (target) => {
        const text = target.dataset.tip;
        if (!text) return;
        tip.textContent = text;
        tip.style.visibility = 'hidden';
        tip.classList.add('is-visible');
        place(target);
        tip.style.visibility = '';
    };
    const hide = () => {
        window.clearTimeout(timer);
        timer = null;
        current = null;
        tip.classList.remove('is-visible');
    };

    document.addEventListener('mouseover', (event) => {
        const target = event.target.closest?.('[data-tip]');
        if (!target || target === current) return;
        current = target;
        window.clearTimeout(timer);
        timer = window.setTimeout(() => {
            if (current === target) show(target);
        }, TIP_DELAY_MS);
    });
    document.addEventListener('mouseout', (event) => {
        const target = event.target.closest?.('[data-tip]');
        if (target && target === current && !target.contains(event.relatedTarget)) hide();
    });
    document.addEventListener('focusin', (event) => {
        const target = event.target.closest?.('[data-tip]');
        if (target) {
            current = target;
            show(target);
        }
    });
    document.addEventListener('focusout', hide);
    // Touch has no hover: a tap opens the hint, the next tap closes it. In the
    // capture phase, so a tap on an ⓘ in the title row does not also fold the panel.
    document.addEventListener('click', (event) => {
        const target = event.target.closest?.('[data-tip]');
        if (!target) {
            hide();
            return;
        }
        event.preventDefault();
        event.stopPropagation();
        if (target === current && tip.classList.contains('is-visible')) {
            hide();
            return;
        }
        window.clearTimeout(timer);
        current = target;
        show(target);
        timer = window.setTimeout(hide, TIP_TOUCH_HIDE_MS);
    }, { capture: true });
    window.addEventListener('scroll', hide, true);
    window.addEventListener('wheel', hide, { passive: true });
    window.addEventListener('resize', hide);
}

export function isPanelCollapsed() {
    return document.getElementById('panel').classList.contains('collapsed');
}

export function setPanelCollapsed(collapsed) {
    const panel = document.getElementById('panel');
    const button = document.getElementById('panel-toggle');
    panel.classList.toggle('collapsed', collapsed);
    // On a phone the open panel reaches down to the timeline; CSS moves things aside.
    document.body.classList.toggle('panel-open', !collapsed);
    button.classList.toggle('open', !collapsed);
    button.setAttribute('aria-expanded', String(!collapsed));
    button.setAttribute('aria-label', collapsed ? 'Panel ausklappen' : 'Panel einklappen');
}

// The title row folds the whole panel, a section's title row that section.
// Clicks on controls inside a title (a link, an ⓘ) do their own thing.
export function setupPanel() {
    const panel = document.getElementById('panel');
    const ownClick = (event) => event.target.closest('a, input, select, label, .info-icon');

    panel.querySelector('.legend-title').addEventListener('click', (event) => {
        if (ownClick(event)) return;
        setPanelCollapsed(!isPanelCollapsed());
    });
    // On a phone, the inline script folded the panel while parsing; the arrow follows.
    setPanelCollapsed(isPanelCollapsed());

    for (const section of panel.querySelectorAll('.legend-section')) {
        const title = section.querySelector('.legend-section-title');
        const arrow = title.querySelector('.section-arrow');
        title.addEventListener('click', (event) => {
            if (ownClick(event)) return;
            const collapsed = !section.classList.contains('collapsed');
            section.classList.toggle('collapsed', collapsed);
            arrow.classList.toggle('open', !collapsed);
            arrow.setAttribute('aria-expanded', String(!collapsed));
        });
    }
}

// The panel and the zoom buttons sit above the timeline on narrow screens, so
// CSS needs its height; it changes with the table view.
export function trackTimelineHeight() {
    const node = document.getElementById('timeline-panel');
    const publish = () => {
        const height = Math.round(node.getBoundingClientRect().height);
        if (height > 0) document.documentElement.style.setProperty('--timeline-h', `${height}px`);
    };
    new ResizeObserver(publish).observe(node);
    publish();
}

// The attribution in the bottom right changes height: it starts open, folds to its
// ⓘ once the map is dragged, and the aerial photo adds a credit. The timeline sits
// above it, so CSS gets the room it takes from the bottom edge. Call after the
// corner's controls are added.
export function trackCornerHeight(map) {
    const container = map.getContainer();
    const corner = container.querySelector('.maplibregl-ctrl-bottom-right');
    const controls = [...corner.children];
    const publish = () => {
        const box = container.getBoundingClientRect();
        const tops = controls.map((c) => c.getBoundingClientRect()).filter((r) => r.height > 0).map((r) => r.top);
        const height = tops.length ? Math.round(box.bottom - Math.min(...tops)) : 0;
        document.documentElement.style.setProperty('--corner-h', `${height}px`);
    };
    const observer = new ResizeObserver(publish);
    for (const c of controls) observer.observe(c);
    window.addEventListener('resize', publish);
    publish();
}

// On a phone the open attribution would push the timeline up by several lines;
// there it starts folded, as in vizsim/unfallkarte. Once is enough: MapLibre
// opens it by itself only when it first turns compact.
export function foldAttributionOnPhone(map) {
    if (!window.matchMedia(MOBILE_QUERY).matches) return;
    map.once('idle', () => {
        const attrib = map.getContainer().querySelector('.maplibregl-ctrl-attrib.maplibregl-compact');
        attrib?.classList.remove('maplibregl-compact-show');
        attrib?.removeAttribute('open');
    });
}

// "Zoomstufe 12" in a hint zooms there.
export function bindZoomLinks(map) {
    for (const link of document.querySelectorAll('.zoom-link[data-zoom]')) {
        link.addEventListener('click', (event) => {
            event.preventDefault();
            const zoom = Number(link.dataset.zoom);
            if (map.getZoom() < zoom) map.easeTo({ zoom: zoom + 0.5 });
        });
    }
}
