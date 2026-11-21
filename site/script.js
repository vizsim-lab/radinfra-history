import {
    ATTRIBUTION,
    BASEMAP_ATTRIBUTION,
    BASEMAP_STYLE,
    DATA_ATTRIBUTION,
    DEEP_HISTORY_URL,
    DETAIL_MINZOOM,
    FAR_SOURCE_ID,
    FAR_SOURCE_LAYER,
    HOVER_DELAY_MS,
    HOVER_LAYER_ID,
    IMAGERY_URL,
    LAYER_PREFIX,
    OVERVIEW_MINZOOM,
    OVERVIEW_SOURCE_ID,
    OVERVIEW_SOURCE_LAYER,
    PLAY_INTERVAL_MS,
    SIDE_OFFSET_STOPS,
    SKIP_LAYER_IDS,
    SOURCE_ID,
    SOURCE_LAYER,
    STYLE_URL,
    TILE_ENCODING,
    TIME_MAX_WAIT_MS,
    TIME_SETTLE_MS,
    TIMELINE_NOTES,
    TIMELINE_URL,
    URL_DEBOUNCE_MS,
    areasGeojsonURL,
    areasURL,
    groupLabel,
    hoverStyle,
    initialMapConfig,
    farTilesURL,
    overviewTilesURL,
    radinfraURL,
    spansURL,
    tilesURL,
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
    bindZoomLinks,
    foldAttributionOnPhone,
    isPanelCollapsed,
    setPanelCollapsed,
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

// `fresh`: ask the server every time (a cheap 304 if unchanged). For the timeline,
// which names the current build; the build's own files never change.
async function fetchJSON(url, { fresh = false } = {}) {
    const response = await fetch(url, fresh ? { cache: 'no-cache' } : undefined);
    if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
    return response.json();
}

// Optional data: without it the page still works, the feature is just missing.
function optionalJSON(url, what, options) {
    return fetchJSON(url, options).catch((error) => {
        console.warn(`${what} unavailable:`, error);
        return null;
    });
}

// The basemap style, with its own credit in place of the one in its TileJSON
// (explicit source options win over TileJSON in MapLibre). If it cannot be read
// here, MapLibre loads it from the URL, with the longer credit.
async function basemapStyle(url) {
    try {
        const style = await fetchJSON(url);
        for (const source of Object.values(style.sources ?? {})) {
            if (source.type === 'vector') source.attribution = BASEMAP_ATTRIBUTION;
        }
        return style;
    } catch (error) {
        console.warn('basemap style:', error);
        return url;
    }
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

// Each category is drawn three times: far out below OVERVIEW_MINZOOM, from the
// merged overview layer up to DETAIL_MINZOOM, from the detail layer above it.
const TIERS = {
    far: { prefix: 'far-', source: FAR_SOURCE_ID, sourceLayer: FAR_SOURCE_LAYER, zooms: { maxzoom: OVERVIEW_MINZOOM } },
    lo: { prefix: 'lo-', source: OVERVIEW_SOURCE_ID, sourceLayer: OVERVIEW_SOURCE_LAYER,
        zooms: { minzoom: OVERVIEW_MINZOOM, maxzoom: DETAIL_MINZOOM } },
    detail: { prefix: '', source: SOURCE_ID, sourceLayer: SOURCE_LAYER, zooms: { minzoom: DETAIL_MINZOOM } },
};
const historyLayerId = (group, tier) => `${LAYER_PREFIX}${TIERS[tier].prefix}${group.id}`;

function historyLayerSpec(group, tier) {
    const { layer } = group;
    const t = TIERS[tier];
    return {
        ...layer,
        id: historyLayerId(group, tier),
        source: t.source,
        'source-layer': t.sourceLayer,
        ...t.zooms,
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

// The popup file has one record of six bytes per version id (vid, the tiles'
// feature id): cf and ct as snapshot indices (uint16, 0xFFFF: still so), pc and nc
// as indices into the timeline's `categories` (uint8, 0xFF: none). One range
// request per clicked line; a server that ignores ranges sends the whole file.
const SPAN_SIZE = 6;
function createSpanReader(url, timeline) {
    const keys = timeline.snapshots.map((s) => s.key);
    const categories = timeline.categories ?? [];
    const cache = new Map();
    return async (vid) => {
        if (!Number.isInteger(vid) || vid <= 0) throw new Error(`no version id: ${vid}`);
        if (cache.has(vid)) return cache.get(vid);
        const start = vid * SPAN_SIZE;
        const response = await fetch(url, { headers: { Range: `bytes=${start}-${start + SPAN_SIZE - 1}` } });
        if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
        let buffer = await response.arrayBuffer();
        if (response.status === 200) buffer = buffer.slice(start, start + SPAN_SIZE);
        if (buffer.byteLength < SPAN_SIZE) throw new Error(`${url}: no record for ${vid}`);
        const view = new DataView(buffer);
        const cti = view.getUint16(2, true);
        const pci = view.getUint8(4);
        const nci = view.getUint8(5);
        const span = {
            cf: keys[view.getUint16(0, true)],
            ct: cti === 0xffff ? OPEN_END : keys[cti],
            pc: pci === 0xff ? null : categories[pci],
            nc: nci === 0xff ? null : categories[nci],
        };
        if (span.cf === undefined || span.ct === undefined) throw new Error(`${url}: bad record for ${vid}`);
        cache.set(vid, span);
        return span;
    };
}

// Built with DOM nodes and textContent: names and ids come from OSM data.
//
// The dates are those of the category, not of the version under the pointer: a
// moved node or a new name makes a new version, but the infrastructure stays what
// it was, so the popup says since when the line has had this category (cf) and
// until when (ct), and what came before (pc) and after (nc). They are not in the
// tiles but in the popup file: `span` is undefined while it loads, null if it
// could not be read.
function popupContent(props, { groupOf, snapshots, radinfraHref, span }) {
    const keys = snapshots.map((s) => s.key);
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
    if (span === undefined) {
        row('Seit', '…');
        row('Bis', '…');
    } else if (span === null) {
        row('Seit', '–', 'Verlauf gerade nicht abrufbar');
    } else {
        const from = keys.indexOf(span.cf);
        const until = span.ct === OPEN_END ? -1 : keys.indexOf(span.ct);
        if (from <= 0) {
            row('Seit', `spätestens ${formatDate(snapshots[0].date)}`, 'schon im ersten Stand dieser Karte');
        } else {
            row('Seit', formatDate(keyToIso(span.cf)), `eingetragen ${between(from)}`);
            row('Vorher', span.pc ? labelOf(span.pc) : 'nicht unter dieser Way-ID erfasst');
        }
        if (span.ct === OPEN_END) {
            row('Bis', `heute (Stand ${formatShortDate(snapshots[snapshots.length - 1].date)})`);
        } else {
            row('Bis', formatDate(keyToIso(span.ct)), until > 0 ? `geändert ${between(until)}` : null);
            row('Danach', span.nc ? labelOf(span.nc) : 'nicht mehr unter dieser Way-ID erfasst');
        }
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
// the region as a whole), so a view can be shared.
function writeStateToURL(snapshots, index, areaSlug, topSlug) {
    const url = new URL(window.location.href);
    if (index === snapshots.length - 1) url.searchParams.delete('t');
    else url.searchParams.set('t', snapshots[index].date);
    if (!areaSlug || areaSlug === topSlug) url.searchParams.delete('area');
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
    trackTimelineHeight();

    // The sources name their credit themselves (DATA_ATTRIBUTION), so the protocol
    // needs no archive metadata.
    const protocol = new pmtiles.Protocol();
    maplibregl.addProtocol('pmtiles', protocol.tile);

    const timelineLoaded = fetchJSON(TIMELINE_URL, { fresh: true });
    const areasLoaded = timelineLoaded.then(({ build }) => Promise.all([
        optionalJSON(areasURL(build), 'area statistics'),
        optionalJSON(areasGeojsonURL(build), 'area boundaries'),
    ]));
    const [timeline, style, basemap, imageryList, [areasData, areasGeojson]] = await Promise.all([
        timelineLoaded,
        fetchJSON(STYLE_URL),
        basemapStyle(BASEMAP_STYLE),
        optionalJSON(IMAGERY_URL, 'imagery list'),
        areasLoaded,
    ]);
    const { snapshots, build } = timeline;
    const readSpan = createSpanReader(spansURL(build), timeline);
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
        style: basemap,
        center: initialMapConfig.center,
        zoom: initialMapConfig.zoom,
        minZoom: initialMapConfig.minZoom,
        maxZoom: initialMapConfig.maxZoom,
        hash: true,
        attributionControl: false,
    });
    window.historyMap = map;
    bindLoadingIndicator(map);
    bindZoomLinks(map);
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 120 }), 'bottom-left');
    // Bottom right, under the timeline: the attribution starts open and folds to
    // its ⓘ once the map is dragged.
    map.addControl(new maplibregl.AttributionControl({ compact: true, customAttribution: ATTRIBUTION }),
        'bottom-right');
    trackCornerHeight(map);
    foldAttributionOnPhone(map);

    let index = initialIndex(snapshots);

    map.on('load', () => {
        // Set before the layers exist, so their first render is already filtered.
        map.setGlobalStateProperty('t', snapshots[index].key);
        const tiles = { type: 'vector', encoding: TILE_ENCODING, attribution: DATA_ATTRIBUTION };
        map.addSource(SOURCE_ID, { ...tiles, url: tilesURL(build) });
        map.addSource(OVERVIEW_SOURCE_ID, { ...tiles, url: overviewTilesURL(build) });
        map.addSource(FAR_SOURCE_ID, { ...tiles, url: farTilesURL(build) });

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
            for (const tier of Object.keys(TIERS)) map.addLayer(historyLayerSpec(group, tier));
        }
        // Only the detail layers carry ids and names, so only they are hit-tested.
        const historyIds = groups.map((g) => historyLayerId(g, 'detail'));

        const imagery = imageryList?.series?.length ? createImagery(map, {
            series: imageryList.series,
            beforeId: HOVER_LAYER_ID,
            isOwnLayer: (id) => id === HOVER_LAYER_ID || id.startsWith(LAYER_PREFIX) || id.startsWith('area-'),
            onChange: () => describeImagery(),
        }) : null;
        const describeImagery = bindBasemapSwitch(imagery, () => snapshots[index].date, imageryList?.series);

        const legend = buildLegend(groups, snapshots, (groupId, visible) => {
            const group = groups.find((g) => g.id === groupId);
            for (const id of Object.keys(TIERS).map((tier) => historyLayerId(group, tier))) {
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
        const writeURL = debounce(() => writeStateToURL(snapshots, index, scope.area?.slug,
            areasData?.areas[0]?.slug), URL_DEBOUNCE_MS);
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
            notes: TIMELINE_NOTES,
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
        const setSelectValue = areas ? fillAreaSelect(areas, (slug) => areas.select(slug, { fit: slug !== areas.top.slug }))
            : () => {};
        const pickButton = document.getElementById('area-pick');
        if (areas) {
            pickButton.addEventListener('click', () => areas.setPicking(!areas.picking));
            areas.select(new URLSearchParams(window.location.search).get('area') ?? areas.top.slug,
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
        let popupToken = null;
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
            // The popup opens at once with what the tile knows; the dates of the
            // category come from the popup file and fill in when they arrive.
            const props = { ...top.properties };
            const context = { groupOf, snapshots, radinfraHref };
            const token = Symbol('popup');
            popupToken = token;
            popup
                .setLngLat(event.lngLat)
                .setDOMContent(popupContent(props, { ...context, span: undefined }))
                .addTo(map);
            readSpan(top.id).then(
                (span) => ({ span }),
                (error) => {
                    console.warn('popup dates unavailable:', error);
                    return { span: null };
                },
            ).then(({ span }) => {
                if (popupToken === token && popup.isOpen()) popup.setDOMContent(popupContent(props, { ...context, span }));
            });
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
