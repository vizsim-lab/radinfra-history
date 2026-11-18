import {
    AREAS_GEOJSON_URL,
    AREAS_URL,
    BASEMAP_STYLE,
    DEEP_HISTORY_URL,
    DETAIL_MINZOOM,
    HOVER_DELAY_MS,
    HOVER_LAYER_ID,
    IMAGERY_URL,
    LAYER_PREFIX,
    OVERVIEW_SOURCE_LAYER,
    PLAY_INTERVAL_MS,
    PMTILES_URL,
    SIDE_OFFSET_STOPS,
    SKIP_LAYER_IDS,
    SOURCE_ID,
    SOURCE_LAYER,
    STYLE_URL,
    TIME_MAX_WAIT_MS,
    TIME_SETTLE_MS,
    TIMELINE_URL,
    URL_DEBOUNCE_MS,
    groupLabel,
    hoverStyle,
    initialMapConfig,
    radinfraURL,
} from './config.js';
import { createAreas } from './areas.js';
import { createImagery } from './imagery.js';
import {
    bindBasemapSwitch,
    bindLoadingIndicator,
    buildLegend,
    createTable,
    describeArea,
    fillAreaSelect,
    fillChangeFrom,
    renderChanges,
    swatch,
} from './panel.js';
import { createTimeline, formatDate, formatKm, formatShortDate } from './timeline.js';
import {
    MOBILE_QUERY,
    addNavigation,
    bindFooter,
    bindZoomLinks,
    foldAttributionOnPhone,
    isPanelCollapsed,
    setPanelCollapsed,
    setupMapSettings,
    setupPanel,
    setupTooltips,
    trackCornerHeight,
    trackTimelineHeight,
} from './ui.js';

const HIT_PADDING = 6;
const OPEN_END = 99991231;

// Versions valid at the selected snapshot: f <= t_selected < t. The selected
// snapshot key lives in the style's global state, so moving the slider changes
// one value instead of filters on every layer.
const AT_SELECTED_TIME = ['all',
    ['<=', ['to-number', ['get', 'f']], ['to-number', ['global-state', 't']]],
    ['>', ['to-number', ['get', 't']], ['to-number', ['global-state', 't']]],
];

function sideOffset() {
    const expr = ['interpolate', ['linear'], ['zoom']];
    for (const [zoom, offset] of SIDE_OFFSET_STOPS) {
        expr.push(zoom, ['match', ['get', 'side'], 'l', -offset, 'r', offset, 0]);
    }
    return expr;
}

function hitBox(point) {
    return [
        [point.x - HIT_PADDING, point.y - HIT_PADDING],
        [point.x + HIT_PADDING, point.y + HIT_PADDING],
    ];
}

function keyToIso(key) {
    const s = String(key);
    return `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}`;
}

async function fetchJSON(url) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
    return response.json();
}

// Optional data: without it the page still works, the feature is just missing.
function optionalJSON(url, what) {
    return fetchJSON(url).catch((error) => {
        console.warn(`${what} unavailable:`, error);
        return null;
    });
}

function debounce(fn, ms) {
    let timer = null;
    return (...args) => {
        if (timer !== null) window.clearTimeout(timer);
        timer = window.setTimeout(() => {
            timer = null;
            fn(...args);
        }, ms);
    };
}

// Applying a date makes MapLibre re-filter every loaded tile of the history
// source in its workers. Dragging the timeline would queue one of those per step,
// each finishing after the pointer has long moved on. So only the newest date is
// applied: after the input rests for TIME_SETTLE_MS, at least every
// TIME_MAX_WAIT_MS while it keeps moving, and never while the previous date is
// still being drawn.
function createTimeApplier(map) {
    let wanted = null;
    let applied = null;
    let busy = false;
    let timer = null;
    let burstStart = 0;
    const waiters = [];

    const isSettled = () => !busy && timer === null && wanted === applied;
    const notify = () => {
        if (isSettled()) waiters.splice(0).forEach((resolve) => resolve());
    };

    function schedule(delay) {
        if (timer !== null) window.clearTimeout(timer);
        timer = window.setTimeout(flush, delay);
    }

    function flush() {
        timer = null;
        // Retried once the date being drawn is done.
        if (busy) return;
        if (wanted === applied) {
            notify();
            return;
        }
        applied = wanted;
        busy = true;
        burstStart = 0;
        map.setGlobalStateProperty('t', applied);
        let done = false;
        const finish = () => {
            if (done) return;
            done = true;
            busy = false;
            if (wanted !== applied) schedule(0);
            else notify();
        };
        map.once('idle', finish);
        // Never stay blocked if idle does not come, e.g. while the map is panned.
        window.setTimeout(finish, 3000);
    }

    return {
        request(key, { immediate = false } = {}) {
            wanted = key;
            const now = performance.now();
            if (!burstStart) burstStart = now;
            const delay = immediate ? 0
                : Math.min(TIME_SETTLE_MS, Math.max(0, TIME_MAX_WAIT_MS - (now - burstStart)));
            schedule(delay);
        },
        settled() {
            return new Promise((resolve) => {
                if (isSettled()) resolve();
                else waiters.push(resolve);
            });
        },
    };
}

// The styled layers of the radinfra details style, in drawing order.
function styleGroups(style) {
    return (style.layers ?? [])
        .filter((layer) => !SKIP_LAYER_IDS.includes(layer.id))
        .map((layer) => ({
            id: layer.id,
            label: groupLabel(layer.id),
            color: layer.paint?.['line-color'] ?? '#888',
            dash: layer.paint?.['line-dasharray'] ?? null,
            layer,
        }));
}

// Each category is drawn twice: from the merged overview layer below
// DETAIL_MINZOOM, from the detail layer at and above it.
const historyLayerId = (group, overview) => `${LAYER_PREFIX}${overview ? 'lo-' : ''}${group.id}`;

function historyLayerSpec(group, overview) {
    const { layer } = group;
    return {
        ...layer,
        id: historyLayerId(group, overview),
        source: SOURCE_ID,
        'source-layer': overview ? OVERVIEW_SOURCE_LAYER : SOURCE_LAYER,
        ...(overview ? { maxzoom: DETAIL_MINZOOM } : { minzoom: DETAIL_MINZOOM }),
        filter: ['all', layer.filter, AT_SELECTED_TIME],
        layout: { ...(layer.layout ?? {}), visibility: 'visible' },
        // radinfra's own offset is a small constant; here each side of a road gets
        // its own line, so the offset follows the side.
        paint: { ...(layer.paint ?? {}), 'line-offset': sideOffset() },
    };
}

function osmWayId(id) {
    const match = /^way\/(\d+)/.exec(String(id ?? ''));
    return match ? match[1] : null;
}

function featureBounds(geometry) {
    if (!geometry) return null;
    const lines = geometry.type === 'MultiLineString' ? geometry.coordinates : [geometry.coordinates];
    let west = Infinity, south = Infinity, east = -Infinity, north = -Infinity;
    for (const line of lines) {
        for (const [lng, lat] of line) {
            west = Math.min(west, lng);
            east = Math.max(east, lng);
            south = Math.min(south, lat);
            north = Math.max(north, lat);
        }
    }
    return Number.isFinite(west) ? [west, south, east, north] : null;
}

const SIDE_LABELS = { l: 'linke Straßenseite', r: 'rechte Straßenseite', s: 'eigener Weg' };

// Built with DOM nodes and textContent: names and ids come from OSM data.
//
// The dates are those of the category, not of the version under the pointer: a
// moved node or a new name makes a new version, but the infrastructure stays what
// it was, so the popup says since when the line has had this category (cf) and
// until when (ct), and what came before (pc) and after (nc).
function popupContent(props, { groupOf, snapshots, radinfraHref }) {
    const keys = snapshots.map((s) => s.key);
    const cf = Number(props.cf ?? props.f);
    const ct = Number(props.ct ?? props.t);
    const from = keys.indexOf(cf);
    const until = ct === OPEN_END ? -1 : keys.indexOf(ct);
    const between = (i) => `zwischen ${formatShortDate(snapshots[i - 1].date)} und ${formatShortDate(snapshots[i].date)}`;

    const node = (tag, cls, text) => {
        const n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text !== undefined) n.textContent = text;
        return n;
    };
    const card = node('div', 'pop-card');
    card.append(node('div', 'pop-eyebrow', `Radinfrastruktur · ${SIDE_LABELS[props.side] ?? 'OSM'}`));
    card.append(node('div', 'pop-title', props.name || 'ohne Namen'));

    const group = groupOf.get(props.category);
    const cat = node('div', 'pop-cat');
    if (group) cat.append(swatch(group.color, group.dash));
    cat.append(node('span', null, group ? group.label : (props.category ?? '–')));
    if (props.category) cat.append(node('span', 'pop-code', props.category));
    card.append(cat);

    // Neighbours in the same legend row differ only in the category itself.
    const labelOf = (category) => {
        const g = groupOf.get(category);
        if (!g) return category;
        return g === group ? `${g.label} (${category})` : g.label;
    };
    const table = node('table', 'pop-table');
    const row = (label, value, meta) => {
        const tr = node('tr');
        const td = node('td');
        td.append(document.createTextNode(value));
        if (meta) td.append(node('span', 'pop-meta', meta));
        tr.append(node('td', null, label), td);
        table.append(tr);
    };
    if (from <= 0) {
        row('Seit', `spätestens ${formatDate(snapshots[0].date)}`, 'schon im ersten Stand dieser Karte');
    } else {
        row('Seit', formatDate(keyToIso(cf)), `eingetragen ${between(from)}`);
        row('Vorher', props.pc ? labelOf(props.pc) : 'nicht unter dieser Way-ID erfasst');
    }
    if (ct === OPEN_END) {
        row('Bis', `heute (Stand ${formatShortDate(snapshots[snapshots.length - 1].date)})`);
    } else {
        row('Bis', formatDate(keyToIso(ct)), until > 0 ? `geändert ${between(until)}` : null);
        row('Danach', props.nc ? labelOf(props.nc) : 'nicht mehr unter dieser Way-ID erfasst');
    }
    card.append(table);

    const foot = node('div', 'pop-foot');
    const link = (href, label) => {
        const a = node('a', null, label);
        a.href = href;
        a.target = '_blank';
        a.rel = 'noopener';
        foot.append(a);
    };
    const wayId = osmWayId(props.id);
    if (wayId) {
        link(`https://www.openstreetmap.org/way/${wayId}`, `OSM-Way ${wayId}`);
        link(DEEP_HISTORY_URL(wayId), 'Versionsgeschichte');
    }
    if (radinfraHref) link(radinfraHref, 'radinfra.de heute');
    if (foot.childElementCount) card.append(foot);
    return card;
}

function initialIndex(snapshots) {
    const wanted = new URLSearchParams(window.location.search).get('t');
    if (!wanted) return snapshots.length - 1;
    // The latest snapshot at or before the requested date.
    let index = 0;
    snapshots.forEach((s, i) => {
        if (s.date <= wanted) index = i;
    });
    return index;
}

// ?t= for the date (left out for the newest), ?area= for the area (left out for
// Berlin as a whole), so a view can be shared.
function writeStateToURL(snapshots, index, areaSlug) {
    const url = new URL(window.location.href);
    if (index === snapshots.length - 1) url.searchParams.delete('t');
    else url.searchParams.set('t', snapshots[index].date);
    if (!areaSlug || areaSlug === 'berlin') url.searchParams.delete('area');
    else url.searchParams.set('area', areaSlug);
    window.history.replaceState(window.history.state, '', url);
}

// Numbers for the whole extract, from the timeline, when no area data is there.
function extractScope(timeline) {
    return {
        name: 'Ausschnitt',
        area: null,
        kmByGroup: new Map(timeline.groups.map((g) => [g.id, g.km])),
        total: timeline.total_km,
    };
}

function areaScope(area, groups, snapshots) {
    const zeros = snapshots.map(() => 0);
    const kmByGroup = new Map(groups.map((g) => [g.id, area.km[g.id] ?? zeros]));
    const total = snapshots.map((_, i) => groups.reduce((sum, g) => sum + (area.km[g.id]?.[i] ?? 0), 0));
    return { name: area.slug === 'berlin' ? 'Berlin' : area.name, area, kmByGroup, total };
}

async function main() {
    setupTooltips();
    setupPanel();
    setupMapSettings();
    trackTimelineHeight();

    // With metadata, the protocol hands MapLibre the archive's full TileJSON,
    // attribution included; without it the tiles' credit never reached the map.
    const protocol = new pmtiles.Protocol({ metadata: true });
    maplibregl.addProtocol('pmtiles', protocol.tile);

    const [timeline, style, imageryList, areasData, areasGeojson] = await Promise.all([
        fetchJSON(TIMELINE_URL),
        fetchJSON(STYLE_URL),
        optionalJSON(IMAGERY_URL, 'imagery list'),
        optionalJSON(AREAS_URL, 'area statistics'),
        optionalJSON(AREAS_GEOJSON_URL, 'area boundaries'),
    ]);
    const { snapshots } = timeline;
    const groups = styleGroups(style);
    const groupOf = new Map(groups.flatMap((g) => {
        const value = g.layer.filter?.[2];
        const categories = typeof value === 'string' ? [value] : (value ?? []);
        return categories.map((c) => [c, g]);
    }));

    document.getElementById('range-start').textContent = formatDate(snapshots[0].date);
    document.getElementById('range-end').textContent = formatDate(snapshots[snapshots.length - 1].date);

    const map = new maplibregl.Map({
        container: 'map',
        style: BASEMAP_STYLE,
        center: initialMapConfig.center,
        zoom: initialMapConfig.zoom,
        minZoom: initialMapConfig.minZoom,
        maxZoom: initialMapConfig.maxZoom,
        hash: true,
        attributionControl: false,
    });
    window.historyMap = map;
    bindLoadingIndicator(map);
    addNavigation(map);
    bindZoomLinks(map);
    bindFooter(map, snapshots);
    // Bottom right, under the panel: the attribution starts open and folds to its
    // ⓘ once the map is dragged; the scale bar sits above it.
    map.addControl(new maplibregl.AttributionControl({
        compact: true,
        customAttribution: '<a href="https://maplibre.org/" target="_blank">MapLibre</a>',
    }), 'bottom-right');
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 100 }), 'bottom-right');
    trackCornerHeight(map);
    foldAttributionOnPhone(map);

    let index = initialIndex(snapshots);

    map.on('load', () => {
        // Set before the layers exist, so their first render is already filtered.
        map.setGlobalStateProperty('t', snapshots[index].key);
        map.addSource(SOURCE_ID, { type: 'vector', url: PMTILES_URL });

        // The halo is switched with feature-state rather than a filter, so a hover
        // never touches the layer's filter. Invisible except on the hovered version.
        map.addLayer({
            id: HOVER_LAYER_ID,
            type: 'line',
            source: SOURCE_ID,
            'source-layer': SOURCE_LAYER,
            minzoom: DETAIL_MINZOOM,
            filter: AT_SELECTED_TIME,
            layout: { 'line-cap': 'butt' },
            paint: {
                'line-color': hoverStyle.color,
                'line-width': hoverStyle.width,
                'line-opacity': ['case', ['boolean', ['feature-state', 'hover'], false], hoverStyle.opacity, 0],
                'line-blur': 1,
                'line-offset': sideOffset(),
            },
        });
        for (const group of groups) {
            map.addLayer(historyLayerSpec(group, true));
            map.addLayer(historyLayerSpec(group, false));
        }
        // Only the detail layers carry ids and names, so only they are hit-tested.
        const historyIds = groups.map((g) => historyLayerId(g, false));

        const imagery = imageryList?.variants?.length ? createImagery(map, {
            variants: imageryList.variants,
            beforeId: HOVER_LAYER_ID,
            isOwnLayer: (id) => id === HOVER_LAYER_ID || id.startsWith(LAYER_PREFIX) || id.startsWith('area-'),
        }) : null;
        const describeImagery = bindBasemapSwitch(imagery, () => snapshots[index].date, imageryList?.variants);

        const legend = buildLegend(groups, snapshots, (groupId, visible) => {
            const group = groups.find((g) => g.id === groupId);
            for (const id of [historyLayerId(group, true), historyLayerId(group, false)]) {
                if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none');
            }
        });
        const table = createTable(snapshots);

        const readoutDate = document.getElementById('tl-date');
        const readoutTotal = document.getElementById('tl-total');
        const readoutScope = document.getElementById('tl-scope');
        const legendDate = document.getElementById('legend-date');
        const changeTo = document.getElementById('change-to');
        const playButton = document.getElementById('tl-play');
        const applier = createTimeApplier(map);
        let scope = extractScope(timeline);
        const writeURL = debounce(() => writeStateToURL(snapshots, index, scope.area?.slug), URL_DEBOUNCE_MS);
        let playing = false;
        let playTimer = null;
        let changeFrom = () => 0;

        const updateChanges = () => {
            changeTo.textContent = formatShortDate(snapshots[index].date);
            renderChanges(groups, scope, snapshots, changeFrom(), index);
        };

        // Readout, legend, chart and table follow at once; the map gets the date
        // through the applier, the photo with it.
        const show = (i) => {
            index = i;
            readoutDate.textContent = formatDate(snapshots[i].date);
            readoutTotal.textContent = formatKm(scope.total[i]);
            legendDate.textContent = formatShortDate(snapshots[i].date);
            legend.update(i);
            updateChanges();
            applier.request(snapshots[i].key, { immediate: playing });
            if (imagery?.on) {
                imagery.update(snapshots[i].date);
                describeImagery();
            }
            writeURL();
        };

        const control = createTimeline(document.getElementById('timeline'), {
            snapshots,
            values: scope.total,
            label: 'Datenstand wählen',
            onChange: show,
        });

        const setScope = (next) => {
            scope = next;
            legend.setData(scope.kmByGroup);
            control.setValues(scope.total);
            table.setValues(scope.total);
            readoutScope.textContent = `${scope.name} `;
            readoutTotal.textContent = formatKm(scope.total[index]);
            updateChanges();
            writeURL();
        };

        changeFrom = fillChangeFrom(snapshots, updateChanges);

        // On a phone the unfolded panel covers the map, so it folds while an
        // Ortsteil is picked and comes back as it was afterwards.
        let reopenPanel = false;
        const areas = areasData && areasGeojson ? createAreas(map, {
            data: areasData,
            geojson: areasGeojson,
            onSelect: (area) => {
                setSelectValue(area.slug);
                describeArea(area, areas);
                setScope(areaScope(area, groups, snapshots));
            },
            onPicking: (on) => {
                document.body.classList.toggle('is-picking', on);
                if (on) {
                    reopenPanel = window.matchMedia(MOBILE_QUERY).matches && !isPanelCollapsed();
                    if (reopenPanel) setPanelCollapsed(true);
                } else if (reopenPanel) {
                    reopenPanel = false;
                    setPanelCollapsed(false);
                }
            },
        }) : null;
        const setSelectValue = areas ? fillAreaSelect(areas, (slug) => areas.select(slug, { fit: slug !== 'berlin' }))
            : () => {};
        const pickButton = document.getElementById('area-pick');
        if (areas) {
            pickButton.addEventListener('click', () => areas.setPicking(!areas.picking));
            areas.select(new URLSearchParams(window.location.search).get('area') ?? 'berlin',
                { fit: Boolean(new URLSearchParams(window.location.search).get('area')) });
        } else {
            document.querySelector('.legend-section[data-section="area"]').hidden = true;
            setScope(scope);
        }

        const stop = () => {
            playing = false;
            if (playTimer !== null) window.clearTimeout(playTimer);
            playTimer = null;
            playButton.classList.remove('is-playing');
            playButton.setAttribute('aria-label', 'Abspielen');
            playButton.title = 'Abspielen';
        };
        // One step, then wait until the map has drawn it before scheduling the
        // next, so a slow device plays slower instead of piling up work.
        const step = async () => {
            if (!playing) return;
            if (control.get() >= snapshots.length - 1) {
                stop();
                return;
            }
            const started = performance.now();
            control.set(control.get() + 1);
            await applier.settled();
            if (!playing) return;
            playTimer = window.setTimeout(step, Math.max(0, PLAY_INTERVAL_MS - (performance.now() - started)));
        };
        playButton.addEventListener('click', () => {
            if (playing) {
                stop();
                return;
            }
            playing = true;
            playButton.classList.add('is-playing');
            playButton.setAttribute('aria-label', 'Anhalten');
            playButton.title = 'Anhalten';
            if (control.get() === snapshots.length - 1) {
                control.set(0);
                applier.settled().then(() => {
                    if (playing) playTimer = window.setTimeout(step, PLAY_INTERVAL_MS);
                });
            } else {
                step();
            }
        });
        document.getElementById('tl-prev').addEventListener('click', () => {
            stop();
            control.set(control.get() - 1);
        });
        document.getElementById('tl-next').addEventListener('click', () => {
            stop();
            control.set(control.get() + 1);
        });
        document.getElementById('timeline').addEventListener('pointerdown', stop);

        control.set(index, { notify: false });
        show(index);

        // Hover and click query the drawn history layers, so a hidden category or
        // a version not valid at the selected date can never be hit. While an
        // area is being picked on the map, lines are left alone.
        const popup = new maplibregl.Popup({ closeButton: true, maxWidth: '320px' });
        const query = (point) => {
            const layers = historyIds.filter((id) => map.getLayer(id));
            return layers.length ? map.queryRenderedFeatures(hitBox(point), { layers }) : [];
        };

        let target = null;
        let shown = null;
        let frame = null;
        let hoverTimer = null;
        let pointer = null;
        const setHalo = (vid) => {
            const ref = (id) => ({ source: SOURCE_ID, sourceLayer: SOURCE_LAYER, id });
            if (shown !== null) map.removeFeatureState(ref(shown), 'hover');
            shown = vid;
            if (vid !== null) map.setFeatureState(ref(vid), { hover: true });
        };
        const applyHover = () => {
            hoverTimer = null;
            if (target !== shown) setHalo(target);
        };
        map.on('mousemove', (event) => {
            pointer = event.point;
            if (frame !== null) return;
            frame = requestAnimationFrame(() => {
                frame = null;
                const picking = areas?.picking;
                const top = picking ? null : query(pointer)[0] ?? null;
                const canvas = map.getCanvas();
                const cursor = picking ? '' : top ? 'pointer' : '';
                if (canvas.style.cursor !== cursor) canvas.style.cursor = cursor;
                const next = top?.id ?? null;
                if (next === target) return;
                target = next;
                if (hoverTimer !== null) window.clearTimeout(hoverTimer);
                hoverTimer = window.setTimeout(applyHover, HOVER_DELAY_MS);
            });
        });
        map.on('mouseout', () => {
            if (hoverTimer !== null) window.clearTimeout(hoverTimer);
            hoverTimer = null;
            target = null;
            map.getCanvas().style.cursor = '';
            setHalo(null);
        });

        map.on('click', (event) => {
            if (areas?.picking) return;
            const top = query(event.point)[0] ?? null;
            if (!top) {
                popup.remove();
                return;
            }
            const wayId = osmWayId(top.properties.id);
            const bounds = featureBounds(top.geometry);
            const radinfraHref = wayId && bounds ? radinfraURL({
                zoom: Math.max(map.getZoom(), 16),
                lat: event.lngLat.lat,
                lng: event.lngLat.lng,
                wayId,
                bounds,
            }) : null;
            popup
                .setLngLat(event.lngLat)
                .setDOMContent(popupContent(top.properties, { groupOf, snapshots, radinfraHref }))
                .addTo(map);
        });
    });

    map.on('error', (event) => {
        console.error('map error', event?.error ?? event);
    });
}

main().catch((error) => {
    console.error(error);
    const node = document.getElementById('fatal');
    node.textContent = `Karte konnte nicht geladen werden: ${error.message}`;
    node.hidden = false;
});
