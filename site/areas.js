// Areas: the region and its administrative areas (pipeline/areas.py): for Berlin
// its Bezirke and Ortsteile, for Germany Länder, Kreise and Gemeinden.
//
// Picking an area scopes every number on the page to it: legend, trend lines,
// timeline, table and the change table. The map keeps drawing everything, but
// dims what lies outside and outlines the area. An area can be picked from the
// search or, for Ortsteile and Gemeinden, by clicking on the map.
//
// The index (names, levels, parents, boxes) is loaded once; an area's numbers come
// with its bundle, the file of its Kreis, when it is picked; the boundaries are
// tiles (areas.pmtiles).

const SOURCE = 'areas';
const SOURCE_LAYER = 'areas';
const OUTSIDE_SOURCE = 'area-outside';
const LAYERS = {
    outside: 'area-outside',
    mask: 'area-mask',
    outline: 'area-outline',
    pickFill: 'area-pick-fill',
    pickLine: 'area-pick-line',
};
const DIM = { 'fill-color': '#ffffff', 'fill-opacity': 0.6 };
const NONE = ['==', ['get', 'slug'], ''];

const WORLD = [[-180, -85], [180, -85], [180, 85], [-180, 85], [-180, -85]];

// What a click on the map picks: the smallest areas there are.
const PICKABLE = ['in', ['get', 'level'], ['literal', ['Ortsteil', 'Gemeinde']]];

// The world with the region cut out, to dim what lies beyond it.
function outsideOf(outline) {
    const { geometry } = outline;
    const polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.coordinates;
    return {
        type: 'Feature',
        properties: {},
        geometry: { type: 'Polygon', coordinates: [WORLD, ...polygons.map((rings) => rings[0])] },
    };
}

// Everything in the region but one area: at each level from it up, its siblings.
// They cover the rest of the region once each, so the dimming is even.
function maskFilter(area, get) {
    const parts = [];
    for (let a = area; a?.parent; a = get(a.parent)) {
        parts.push(['all',
            ['==', ['get', 'level'], a.level],
            ['==', ['get', 'parent'], a.parent],
            ['!=', ['get', 'slug'], a.slug]]);
    }
    return parts.length ? ['any', ...parts] : NONE;
}

// areas/index.json: rows of `fields`, the region first.
export function readIndex(data) {
    const at = Object.fromEntries(data.fields.map((name, i) => [name, i]));
    const list = data.areas.map((row) => ({
        slug: row[at.slug], name: row[at.name], level: row[at.level], parent: row[at.parent],
        bundle: row[at.bundle], km2: row[at.km2], bbox: row[at.bbox],
    }));
    return { list, aliases: data.aliases ?? {} };
}

// What changed in OSM at each snapshot since the one before, from an area's `act`
// rows [step, changed, came, went] (pipeline/areas.py): km whose legend row changed
// on the same object, that came under an id with nothing before, and that went
// from one. null for the first snapshot, and for all while a build has no `act`
// (before November 2026).
export function activityOf(area, count) {
    if (!area?.act) return Array(count).fill(null);
    const steps = Array.from({ length: count }, (_, i) => (i ? { changed: 0, came: 0, went: 0 } : null));
    for (const [step, changed, came, went] of area.act) {
        if (steps[step]) Object.assign(steps[step], { changed, came, went });
    }
    return steps;
}

export function createAreas(map, { index, loadBundle, tilesURL, encoding, outline, beforeId = undefined,
    onSelect, onPicking = () => {} }) {
    const { list, aliases } = index;
    const bySlug = new Map(list.map((a) => [a.slug, a]));
    const childrenOf = new Map();
    for (const a of list) {
        if (!a.parent) continue;
        if (!childrenOf.has(a.parent)) childrenOf.set(a.parent, []);
        childrenOf.get(a.parent).push(a);
    }
    const top = list[0];
    const find = (slug) => bySlug.get(slug) ?? bySlug.get(aliases[slug]);
    let current = top;
    let picking = false;
    let hovered = null;
    let request = 0;

    map.addSource(SOURCE, { type: 'vector', url: tilesURL, encoding, promoteId: 'slug' });
    map.addSource(OUTSIDE_SOURCE, {
        type: 'geojson',
        data: outline ? outsideOf(outline) : { type: 'FeatureCollection', features: [] },
    });

    // Above the lines, so what lies outside the area is dimmed, under nothing else.
    map.addLayer({ id: LAYERS.outside, type: 'fill', source: OUTSIDE_SOURCE, paint: DIM }, beforeId);
    map.addLayer({
        id: LAYERS.mask, type: 'fill', source: SOURCE, 'source-layer': SOURCE_LAYER,
        filter: NONE, paint: DIM,
    }, beforeId);
    map.addLayer({
        id: LAYERS.outline, type: 'line', source: SOURCE, 'source-layer': SOURCE_LAYER,
        filter: NONE,
        layout: { 'line-join': 'round' },
        paint: { 'line-color': '#1f2937', 'line-width': 2, 'line-opacity': 0.8 },
    }, beforeId);
    // Only while picking on the map: every Ortsteil or Gemeinde, the hovered one tinted.
    map.addLayer({
        id: LAYERS.pickFill, type: 'fill', source: SOURCE, 'source-layer': SOURCE_LAYER,
        filter: PICKABLE,
        layout: { visibility: 'none' },
        paint: {
            'fill-color': '#174ed9',
            'fill-opacity': ['case', ['boolean', ['feature-state', 'hover'], false], 0.16, 0],
        },
    }, beforeId);
    map.addLayer({
        id: LAYERS.pickLine, type: 'line', source: SOURCE, 'source-layer': SOURCE_LAYER,
        filter: PICKABLE,
        layout: { visibility: 'none' },
        paint: { 'line-color': '#374151', 'line-width': 1, 'line-opacity': 0.6 },
    }, beforeId);

    const tooltip = document.createElement('div');
    tooltip.className = 'area-tooltip';
    tooltip.hidden = true;
    map.getContainer().appendChild(tooltip);

    function setHover(slug) {
        if (hovered === slug) return;
        if (hovered) map.setFeatureState({ source: SOURCE, sourceLayer: SOURCE_LAYER, id: hovered }, { hover: false });
        hovered = slug;
        if (slug) map.setFeatureState({ source: SOURCE, sourceLayer: SOURCE_LAYER, id: slug }, { hover: true });
    }

    function show(area, { fit }) {
        current = area;
        map.setFilter(LAYERS.mask, maskFilter(area, (slug) => bySlug.get(slug)));
        map.setFilter(LAYERS.outline, area === top ? NONE : ['==', ['get', 'slug'], area.slug]);
        if (fit && area.bbox) {
            const [w, s, e, n] = area.bbox;
            map.fitBounds([[w, s], [e, n]], { padding: fitPadding(map), maxZoom: 15, duration: 600 });
        }
        onSelect(area);
    }

    function setPicking(on) {
        if (on === picking) return;
        picking = on;
        for (const id of [LAYERS.pickFill, LAYERS.pickLine]) {
            map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
        }
        map.getCanvas().classList.toggle('is-picking', on);
        if (!on) {
            setHover(null);
            tooltip.hidden = true;
        }
        document.getElementById('area-pick')?.setAttribute('aria-pressed', String(on));
        const bar = document.getElementById('pick-bar');
        if (bar) bar.hidden = !on;
        onPicking(on);
    }

    document.getElementById('pick-cancel')?.addEventListener('click', () => setPicking(false));

    map.on('mousemove', (event) => {
        if (!picking) return;
        const hit = map.queryRenderedFeatures(event.point, { layers: [LAYERS.pickFill] })[0];
        const slug = hit?.properties?.slug ?? null;
        setHover(slug);
        if (slug) {
            tooltip.textContent = bySlug.get(slug)?.name ?? slug;
            tooltip.style.left = `${event.point.x}px`;
            tooltip.style.top = `${event.point.y}px`;
            tooltip.hidden = false;
        } else {
            tooltip.hidden = true;
        }
    });

    map.on('click', (event) => {
        if (!picking) return;
        const hit = map.queryRenderedFeatures(event.point, { layers: [LAYERS.pickFill] })[0];
        // Picking ends after every click listener of this event has run, so the
        // same click does not also open a line's popup.
        window.setTimeout(() => setPicking(false), 0);
        const area = hit && bySlug.get(hit.properties.slug);
        if (area) select(area.slug, { fit: true });
    });

    window.addEventListener('keydown', (event) => {
        if (picking && event.key === 'Escape') setPicking(false);
    });

    // The numbers come with the area's bundle; of quick picks, the last one wins.
    async function select(slug, { fit = false } = {}) {
        const area = find(slug) ?? top;
        const mine = ++request;
        let bundle = null;
        try {
            bundle = await loadBundle(area.bundle);
        } catch (error) {
            console.warn(`area numbers for ${area.slug} unavailable:`, error);
        }
        if (mine !== request) return;
        const numbers = bundle?.areas?.[area.slug];
        area.km = numbers?.km ?? {};
        area.act = numbers?.act ?? null;
        show(area, { fit });
    }

    return {
        get current() {
            return current;
        },
        get picking() {
            return picking;
        },
        top,
        list,
        select,
        setPicking,
        get: (slug) => bySlug.get(slug),
        children: (slug) => childrenOf.get(slug) ?? [],
    };
}

// Keep the area clear of the panel and the timeline when zooming to it. The panel
// is a column on the left, or on a phone a band across the top.
function fitPadding(map) {
    const box = map.getContainer().getBoundingClientRect();
    const rect = (id) => {
        const r = document.getElementById(id)?.getBoundingClientRect();
        return r && r.width > 0 && r.height > 0 ? r : null;
    };
    const panel = rect('panel');
    const timeline = rect('timeline-panel');
    const pad = { top: 24, right: 24, bottom: 24, left: 24 };
    if (panel && panel.width < box.width * 0.6) pad.left = Math.max(pad.left, panel.right - box.left + 16);
    else if (panel) pad.top = Math.max(pad.top, panel.bottom - box.top + 16);
    if (timeline) pad.bottom = Math.max(pad.bottom, box.bottom - timeline.top + 16);
    // MapLibre cannot fit into less than nothing; leave it at least a strip.
    const shrink = (a, b, size) => {
        const over = pad[a] + pad[b] - (size - 80);
        if (over > 0) {
            const total = pad[a] + pad[b];
            pad[a] -= over * (pad[a] / total);
            pad[b] -= over * (pad[b] / total);
        }
    };
    shrink('left', 'right', box.width);
    shrink('top', 'bottom', box.height);
    return pad;
}
