// Areas: Berlin, its Bezirke and Ortsteile (pipeline/areas.py).
//
// Picking an area scopes every number on the page to it: legend, trend lines,
// timeline, table and the change table. The map keeps drawing everything, but
// dims what lies outside and outlines the area. An area can be picked from the
// list or, for Ortsteile, by clicking on the map.

const SOURCE = 'areas';
const MASK_SOURCE = 'area-mask';
const LAYERS = {
    mask: 'area-mask',
    outline: 'area-outline',
    pickFill: 'area-pick-fill',
    pickLine: 'area-pick-line',
};

const WORLD = [[-180, -85], [180, -85], [180, 85], [-180, 85], [-180, -85]];

// The world with the area cut out, to dim everything around it.
function maskFor(feature) {
    const { geometry } = feature;
    const polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.coordinates;
    return {
        type: 'Feature',
        properties: {},
        geometry: { type: 'Polygon', coordinates: [WORLD, ...polygons.map((rings) => rings[0])] },
    };
}

export function createAreas(map, { data, geojson, beforeId = undefined, onSelect }) {
    const bySlug = new Map(data.areas.map((a) => [a.slug, a]));
    const featureBySlug = new Map(geojson.features.map((f) => [f.properties.slug, f]));
    let current = bySlug.get('berlin') ?? data.areas[0];
    let picking = false;
    let hovered = null;

    map.addSource(SOURCE, { type: 'geojson', data: geojson, promoteId: 'slug' });
    map.addSource(MASK_SOURCE, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });

    // Above the lines, so what lies outside the area is dimmed, under nothing else.
    map.addLayer({
        id: LAYERS.mask, type: 'fill', source: MASK_SOURCE,
        paint: { 'fill-color': '#ffffff', 'fill-opacity': 0.6 },
    }, beforeId);
    map.addLayer({
        id: LAYERS.outline, type: 'line', source: SOURCE,
        filter: ['==', ['get', 'slug'], ''],
        layout: { 'line-join': 'round' },
        paint: { 'line-color': '#1f2937', 'line-width': 2, 'line-opacity': 0.8 },
    }, beforeId);
    // Only while picking on the map: every Ortsteil, the hovered one tinted.
    map.addLayer({
        id: LAYERS.pickFill, type: 'fill', source: SOURCE,
        filter: ['==', ['get', 'level'], 'Ortsteil'],
        layout: { visibility: 'none' },
        paint: {
            'fill-color': '#174ed9',
            'fill-opacity': ['case', ['boolean', ['feature-state', 'hover'], false], 0.16, 0],
        },
    }, beforeId);
    map.addLayer({
        id: LAYERS.pickLine, type: 'line', source: SOURCE,
        filter: ['==', ['get', 'level'], 'Ortsteil'],
        layout: { visibility: 'none' },
        paint: { 'line-color': '#374151', 'line-width': 1, 'line-opacity': 0.6 },
    }, beforeId);

    const tooltip = document.createElement('div');
    tooltip.className = 'area-tooltip';
    tooltip.hidden = true;
    map.getContainer().appendChild(tooltip);

    function setHover(slug) {
        if (hovered === slug) return;
        if (hovered) map.setFeatureState({ source: SOURCE, id: hovered }, { hover: false });
        hovered = slug;
        if (slug) map.setFeatureState({ source: SOURCE, id: slug }, { hover: true });
    }

    function show(area, { fit }) {
        current = area;
        const feature = featureBySlug.get(area.slug);
        map.getSource(MASK_SOURCE).setData({
            type: 'FeatureCollection', features: feature ? [maskFor(feature)] : [],
        });
        map.setFilter(LAYERS.outline, ['==', ['get', 'slug'], area.slug]);
        if (fit && area.bbox) {
            const [w, s, e, n] = area.bbox;
            map.fitBounds([[w, s], [e, n]], { padding: fitPadding(map), maxZoom: 15, duration: 600 });
        }
        onSelect(area);
    }

    function setPicking(on) {
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
    }

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

    function select(slug, { fit = false } = {}) {
        const area = bySlug.get(slug) ?? bySlug.get('berlin');
        if (area) show(area, { fit });
    }

    return {
        get current() {
            return current;
        },
        get picking() {
            return picking;
        },
        select,
        setPicking,
        byLevel: (level) => data.areas.filter((a) => a.level === level),
        get: (slug) => bySlug.get(slug),
    };
}

// Keep the area clear of the panel and the timeline when zooming to it.
function fitPadding(map) {
    const box = map.getContainer().getBoundingClientRect();
    const panel = document.querySelector('.panel')?.getBoundingClientRect();
    const timeline = document.querySelector('.timeline-panel')?.getBoundingClientRect();
    const pad = { top: 24, right: 24, bottom: 24, left: 24 };
    if (panel && box.width > 700) pad.left = Math.max(pad.left, panel.right - box.left + 16);
    else if (panel) pad.top = Math.max(pad.top, panel.bottom - box.top + 16);
    if (timeline) pad.bottom = Math.max(pad.bottom, box.bottom - timeline.top + 16);
    return pad;
}
