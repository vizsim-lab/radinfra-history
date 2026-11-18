import {
    BASEMAP_STYLE,
    HOVER_LAYER_ID,
    LAYER_PREFIX,
    LIVE,
    PLAY_INTERVAL_MS,
    PMTILES_URL,
    SIDE_OFFSET_STOPS,
    SKIP_LAYER_IDS,
    SOURCE_ID,
    SOURCE_LAYER,
    STYLE_URL,
    TIMELINE_URL,
    groupLabel,
    hoverStyle,
    initialMapConfig,
    radinfraURL,
} from './config.js';
import {
    createTimeline,
    formatDate,
    formatKm,
    formatShortDate,
    sparkline,
} from './timeline.js';

const HIT_PADDING = 6;
const HOVER_DELAY_MS = 60;
const OPEN_END = 99991231;

// Versions valid at the selected snapshot: f <= t_selected < t. The selected
// snapshot key lives in the style's global state, so moving the slider changes
// one value instead of thirteen filters.
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

function historyLayerSpec(group) {
    const { layer } = group;
    return {
        ...layer,
        id: LAYER_PREFIX + layer.id,
        source: SOURCE_ID,
        'source-layer': SOURCE_LAYER,
        filter: ['all', layer.filter, AT_SELECTED_TIME],
        layout: { ...(layer.layout ?? {}), visibility: 'visible' },
        // radinfra's own offset is a small constant; here each side of a road gets
        // its own line, so the offset follows the side.
        paint: { ...(layer.paint ?? {}), 'line-offset': sideOffset() },
    };
}

function liveLayerSpec(group, sourceLayer) {
    const { layer } = group;
    return {
        ...layer,
        id: LIVE.layerPrefix + layer.id,
        source: LIVE.sourceId,
        'source-layer': layer['source-layer'] ?? sourceLayer,
        layout: { ...(layer.layout ?? {}), visibility: 'none' },
    };
}

// Legend swatch mirrors the mark: a short line, dashed where the style dashes it.
function swatch(color, dash) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'legend-color');
    svg.setAttribute('viewBox', '0 0 18 6');
    svg.setAttribute('aria-hidden', 'true');
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('x1', '0');
    line.setAttribute('x2', '18');
    line.setAttribute('y1', '3');
    line.setAttribute('y2', '3');
    line.setAttribute('stroke', color);
    line.setAttribute('stroke-width', '3');
    if (Array.isArray(dash)) line.setAttribute('stroke-dasharray', dash.map((d) => d * 3).join(' '));
    svg.appendChild(line);
    return svg;
}

function buildLegend(map, groups, timeline, liveState) {
    const list = document.getElementById('legend-items');
    const kmByGroup = new Map(timeline.groups.map((g) => [g.id, g.km]));
    const visible = new Map(groups.map((g) => [g.id, true]));
    const rows = [];

    const apply = () => {
        for (const group of groups) {
            const on = visible.get(group.id);
            const historyId = LAYER_PREFIX + group.id;
            if (map.getLayer(historyId)) {
                map.setLayoutProperty(historyId, 'visibility', on ? 'visible' : 'none');
            }
            const liveId = LIVE.layerPrefix + group.id;
            if (map.getLayer(liveId)) {
                map.setLayoutProperty(liveId, 'visibility', on && liveState.on ? 'visible' : 'none');
            }
        }
    };

    // A style lists layers bottom to top; the legend reads top to bottom, so the
    // order is reversed for display only.
    for (const group of [...groups].reverse()) {
        const km = kmByGroup.get(group.id) ?? timeline.snapshots.map(() => 0);
        const row = document.createElement('button');
        row.type = 'button';
        row.className = 'legend-item legend-item--toggle';
        row.setAttribute('role', 'switch');
        row.setAttribute('aria-checked', 'true');

        const text = document.createElement('span');
        text.className = 'legend-label';
        text.textContent = group.label;

        const spark = sparkline(km, group.color);
        const value = document.createElement('span');
        value.className = 'legend-km';

        row.append(swatch(group.color, group.dash), text, spark.node, value);
        row.addEventListener('click', () => {
            const next = row.getAttribute('aria-checked') !== 'true';
            row.setAttribute('aria-checked', String(next));
            visible.set(group.id, next);
            apply();
        });
        list.appendChild(row);
        rows.push({ km, spark, value, row, group });
    }

    return {
        apply,
        update(index) {
            for (const { km, spark, value, row, group } of rows) {
                spark.mark(index);
                value.textContent = formatKm(km[index]);
                row.title = `${group.label}: ${formatKm(km[index])} am ${formatDate(timeline.snapshots[index].date)}`;
            }
        },
    };
}

function buildTable(timeline) {
    const tbody = document.querySelector('#tl-table tbody');
    timeline.snapshots.forEach((snapshot, i) => {
        const tr = document.createElement('tr');
        const date = document.createElement('td');
        date.textContent = formatShortDate(snapshot.date);
        const km = document.createElement('td');
        km.textContent = formatKm(timeline.total_km[i]);
        tr.append(date, km);
        tbody.appendChild(tr);
    });
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
function popupContent(props, { groupOf, snapshots, liveHref }) {
    const keys = snapshots.map((s) => s.key);
    const f = Number(props.f);
    const t = Number(props.t);
    const fIndex = keys.indexOf(f);
    const tIndex = t === OPEN_END ? -1 : keys.indexOf(t);

    const root = document.createElement('div');
    const title = document.createElement('h4');
    title.textContent = props.name || 'ohne Namen';
    root.appendChild(title);

    const table = document.createElement('table');
    const addRow = (label, ...nodes) => {
        const tr = document.createElement('tr');
        const th = document.createElement('th');
        th.textContent = label;
        const td = document.createElement('td');
        td.append(...nodes);
        tr.append(th, td);
        table.appendChild(tr);
    };
    const text = (value) => document.createTextNode(value);
    const code = (value) => {
        const node = document.createElement('code');
        node.textContent = value;
        return node;
    };
    const hint = (value) => {
        const node = document.createElement('span');
        node.className = 'popup-hint';
        node.textContent = value;
        return node;
    };

    const group = groupOf.get(props.category);
    addRow('Führungsform', text(group ? group.label : '–'), document.createElement('br'), code(props.category ?? '–'));
    addRow('Lage', text(SIDE_LABELS[props.side] ?? '–'));

    let since;
    if (fIndex <= 0) {
        since = hint(`schon im ersten Stand (${formatShortDate(snapshots[0].date)}) enthalten`);
    } else {
        since = hint(`erfasst oder geändert zwischen ${formatShortDate(snapshots[fIndex - 1].date)} `
            + `und ${formatShortDate(snapshots[fIndex].date)}`);
    }
    addRow('In OSM seit', text(formatDate(keyToIso(f))), since);

    if (t === OPEN_END) {
        addRow('Bis', text(`heute (Stand ${formatShortDate(snapshots[snapshots.length - 1].date)})`));
    } else {
        const until = tIndex > 0
            ? hint(`geändert oder entfernt zwischen ${formatShortDate(snapshots[tIndex - 1].date)} `
                + `und ${formatShortDate(snapshots[tIndex].date)}`)
            : text('');
        addRow('Bis', text(`vor ${formatDate(keyToIso(t))}`), until);
    }
    root.appendChild(table);

    const links = document.createElement('p');
    links.className = 'popup-links';
    const link = (href, label) => {
        const a = document.createElement('a');
        a.href = href;
        a.target = '_blank';
        a.rel = 'noopener';
        a.textContent = label;
        links.append(a, document.createElement('br'));
    };
    const wayId = osmWayId(props.id);
    if (wayId) {
        link(`https://www.openstreetmap.org/way/${wayId}`, `Way ${wayId} auf openstreetmap.org`);
        link(`https://www.openstreetmap.org/way/${wayId}/history`, 'Versionsgeschichte des Ways');
    }
    if (liveHref) link(liveHref, 'Heutiger Stand bei radinfra.de');
    root.appendChild(links);
    return root;
}

// Most of the wait when zooming is the basemap, so the indicator follows the
// map's own load state rather than our source (as in vizsim/bikeneat).
function bindLoadingIndicator(map) {
    const node = document.getElementById('loading');
    let timer = null;
    let idle = true;
    const show = () => {
        idle = false;
        if (timer !== null || node.classList.contains('is-visible')) return;
        timer = window.setTimeout(() => {
            timer = null;
            if (!idle) node.classList.add('is-visible');
        }, 250);
    };
    const hide = () => {
        idle = true;
        if (timer !== null) window.clearTimeout(timer);
        timer = null;
        node.classList.remove('is-visible');
    };
    map.on('movestart', show);
    map.on('dataloading', show);
    map.on('idle', hide);
}

function bindPanelToggle() {
    const panel = document.querySelector('.panel');
    const button = document.getElementById('panel-toggle');
    button.addEventListener('click', () => {
        const collapsed = button.getAttribute('aria-expanded') === 'true';
        panel.classList.toggle('is-collapsed', collapsed);
        button.setAttribute('aria-expanded', String(!collapsed));
        const label = collapsed ? 'Panel ausklappen' : 'Panel einklappen';
        button.setAttribute('aria-label', label);
        button.title = label;
    });
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

function writeIndexToURL(snapshots, index) {
    const url = new URL(window.location.href);
    if (index === snapshots.length - 1) url.searchParams.delete('t');
    else url.searchParams.set('t', snapshots[index].date);
    window.history.replaceState(window.history.state, '', url);
}

async function main() {
    bindPanelToggle();

    const protocol = new pmtiles.Protocol();
    maplibregl.addProtocol('pmtiles', protocol.tile);

    const [timeline, style] = await Promise.all([fetchJSON(TIMELINE_URL), fetchJSON(STYLE_URL)]);
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
    });
    window.historyMap = map;
    bindLoadingIndicator(map);
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 120 }));

    let index = initialIndex(snapshots);
    map.setGlobalStateProperty('t', snapshots[index].key);

    map.on('load', () => {
        map.setGlobalStateProperty('t', snapshots[index].key);
        map.addSource(SOURCE_ID, { type: 'vector', url: PMTILES_URL });

        map.addLayer({
            id: HOVER_LAYER_ID,
            type: 'line',
            source: SOURCE_ID,
            'source-layer': SOURCE_LAYER,
            filter: ['all', ['==', ['id'], -1], AT_SELECTED_TIME],
            layout: { 'line-cap': 'butt' },
            paint: {
                'line-color': hoverStyle.color,
                'line-width': hoverStyle.width,
                'line-opacity': hoverStyle.opacity,
                'line-blur': 1,
                'line-offset': sideOffset(),
            },
        });
        for (const group of groups) map.addLayer(historyLayerSpec(group));
        const historyIds = groups.map((g) => LAYER_PREFIX + g.id);

        // radinfra.de live, for comparing the newest snapshot with today. Off by
        // default and drawn on top.
        const liveSource = Object.values(style.sources ?? {})[0];
        const liveState = { on: false };
        const sourceLayer = (style.layers ?? []).find((l) => l['source-layer'])?.['source-layer'];
        if (liveSource) {
            map.addSource(LIVE.sourceId, liveSource);
            for (const group of groups) map.addLayer(liveLayerSpec(group, sourceLayer));
        }

        const legend = buildLegend(map, groups, timeline, liveState);
        buildTable(timeline);

        const readoutDate = document.getElementById('tl-date');
        const readoutTotal = document.getElementById('tl-total');
        const legendDate = document.getElementById('legend-date');
        const playButton = document.getElementById('tl-play');
        let timer = null;

        const show = (i) => {
            index = i;
            map.setGlobalStateProperty('t', snapshots[i].key);
            readoutDate.textContent = formatDate(snapshots[i].date);
            readoutTotal.textContent = formatKm(timeline.total_km[i]);
            legendDate.textContent = formatShortDate(snapshots[i].date);
            legend.update(i);
            writeIndexToURL(snapshots, i);
        };

        const control = createTimeline(document.getElementById('timeline'), {
            snapshots,
            values: timeline.total_km,
            label: 'Datenstand wählen',
            onChange: show,
        });

        const stop = () => {
            if (timer !== null) window.clearInterval(timer);
            timer = null;
            playButton.classList.remove('is-playing');
            playButton.setAttribute('aria-label', 'Abspielen');
            playButton.title = 'Abspielen';
        };
        playButton.addEventListener('click', () => {
            if (timer !== null) {
                stop();
                return;
            }
            if (control.get() === snapshots.length - 1) control.set(0);
            playButton.classList.add('is-playing');
            playButton.setAttribute('aria-label', 'Anhalten');
            playButton.title = 'Anhalten';
            timer = window.setInterval(() => {
                if (control.get() >= snapshots.length - 1) {
                    stop();
                    return;
                }
                control.set(control.get() + 1);
            }, PLAY_INTERVAL_MS);
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

        const liveToggle = document.getElementById('toggle-live');
        if (!liveSource) {
            liveToggle.disabled = true;
        }
        liveToggle.addEventListener('change', () => {
            liveState.on = liveToggle.checked;
            // Only the newest snapshot is comparable with today's data.
            if (liveState.on) {
                stop();
                control.set(snapshots.length - 1);
            }
            legend.apply();
        });

        const updateDeepLinks = () => {
            const center = map.getCenter();
            const href = radinfraURL({ zoom: map.getZoom(), lat: center.lat, lng: center.lng });
            for (const a of document.querySelectorAll('.radinfra-deeplink')) a.href = href;
        };
        map.on('moveend', updateDeepLinks);
        updateDeepLinks();

        // Hover and click query the drawn history layers, so a hidden category or
        // a version not valid at the selected date can never be hit.
        const popup = new maplibregl.Popup({ closeButton: true, maxWidth: '340px' });
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
            map.setFilter(HOVER_LAYER_ID, ['all', ['==', ['id'], vid ?? -1], AT_SELECTED_TIME]);
        };
        const applyHover = () => {
            hoverTimer = null;
            if (target === shown) return;
            shown = target;
            setHalo(shown);
        };
        map.on('mousemove', (event) => {
            pointer = event.point;
            if (frame !== null) return;
            frame = requestAnimationFrame(() => {
                frame = null;
                const top = query(pointer)[0] ?? null;
                map.getCanvas().style.cursor = top ? 'pointer' : '';
                const next = top ? top.id : null;
                if (next === target) return;
                target = next;
                if (hoverTimer !== null) window.clearTimeout(hoverTimer);
                hoverTimer = window.setTimeout(applyHover, HOVER_DELAY_MS);
            });
        });
        map.on('mouseout', () => {
            target = null;
            shown = null;
            map.getCanvas().style.cursor = '';
            setHalo(null);
        });

        map.on('click', (event) => {
            const top = query(event.point)[0] ?? null;
            if (!top) {
                popup.remove();
                return;
            }
            const wayId = osmWayId(top.properties.id);
            const bounds = featureBounds(top.geometry);
            const liveHref = wayId && bounds ? radinfraURL({
                zoom: Math.max(map.getZoom(), 16),
                lat: event.lngLat.lat,
                lng: event.lngLat.lng,
                wayId,
                bounds,
            }) : null;
            popup
                .setLngLat(event.lngLat)
                .setDOMContent(popupContent(top.properties, { groupOf, snapshots, liveHref }))
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
