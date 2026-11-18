// Configuration for the radinfra-history map.

const REGION_SLUG = 'berlin';

// Tiles and timeline are published next to the page (GitHub Pages), so plain
// relative URLs work locally (scripts/serve.py) and in production alike.
const base = new URL('./', window.location.href);

export const PMTILES_URL = `pmtiles://${new URL(`tiles/${REGION_SLUG}.pmtiles`, base).href}`;
export const TIMELINE_URL = new URL(`data/${REGION_SLUG}_timeline.json`, base).href;

// radinfra.de's own "details" style. Its layers are drawn as they are — colours,
// widths, dashes, filters — against our source, plus a time filter. Keeping the
// file verbatim means picking up a newer tilda style is a matter of replacing it.
// pipeline/style.py reads the same file for the km counts, so legend and numbers
// cannot disagree about which category belongs to which row.
export const STYLE_URL = new URL('radinfra_details_style.json', base).href;

export const SOURCE_ID = 'history';
export const LAYER_PREFIX = 'rh-';
export const HOVER_LAYER_ID = 'rh-hover';

// The archive has two layers (pipeline/tiles.py): every version with its id and
// name from z12, and below that a merged overview without ids. Lines can be
// hovered and clicked from DETAIL_MINZOOM on.
export const SOURCE_LAYER = 'bikelanes';
export const OVERVIEW_SOURCE_LAYER = 'bikelanes_lo';
export const DETAIL_MINZOOM = 12;

// Invisible click target in the tilda style; hit testing here does not need it.
export const SKIP_LAYER_IDS = ['hitarea-bikelanes-details'];

// The same style against radinfra.de's live tiles, as an optional comparison.
export const LIVE = {
    sourceId: 'radinfra-live',
    layerPrefix: 'live-',
    siteURL: 'https://tilda-geo.de/regionen/radinfra',
    // Opaque hash from a shared radinfra.de link (taken over from vizsim/bikeneat);
    // it selects which layers the target page shows.
    siteConfig: '1p2va4k.7h3d.9fm70g',
};

// Friendlier names than the style's layer ids, as radinfra.de shows them in its
// own legend. Anything not listed falls back to the layer id, so a newer style
// with new layers still shows up, just with its raw name.
const LABELS = {
    'needsClarification-details': 'Führungsform unklar',
    'Gemeinsamer Fahrstreifen mit Kfz Markiert': 'Gem. Fahrstreifen mit Kfz (markiert)',
    'Gemeinsamer Fahrstreifen mit Bus': 'Gem. Fahrstreifen mit Bus',
    'Fahrradstrasse Mischverkehr': 'Fahrradstraße (Mischverkehr)',
    'Fahrradstrasse keine Kfz': 'Fahrradstraße (keine Kfz)',
    'Gehweg Rad frei -details': 'Gehweg, Rad frei',
    'Gemeinsamer Geh u Radweg': 'Gemeinsamer Geh- und Radweg',
    'Geschuetzter Radfahrstreifen': 'Geschützter Radfahrstreifen',
};

export function groupLabel(layerId) {
    return LABELS[layerId] ?? layerId;
}

export const BASEMAP_STYLE = 'https://tiles.openfreemap.org/styles/positron';

export const initialMapConfig = {
    center: [13.4050, 52.5100],
    zoom: 11,
    minZoom: 9,
    maxZoom: 18,
};

// Hover highlight, drawn as a wide blurred halo beneath the category lines so
// their colour stays readable on top of it (as in vizsim/bikeneat).
export const hoverStyle = {
    color: '#fb923c',
    opacity: 0.55,
    width: ['interpolate', ['linear'], ['zoom'], 10, 5, 14, 9, 18, 16],
};

// One side of a road is drawn offset to that side; a way of its own is centred.
// A positive line-offset is to the right of the line direction, and the geometry
// keeps the OSM way direction, so 'r' is positive and 'l' negative.
// Stops are [zoom, offset in px], the same as vizsim/bikeneat uses.
export const SIDE_OFFSET_STOPS = [
    [10, 1],
    [14, 2],
    [18, 5],
    [20, 8],
];

// Berlin's aerial photo series, from the OSM Editor Layer Index (scripts/imagery).
export const IMAGERY_URL = new URL(`data/${REGION_SLUG}_imagery.json`, base).href;

// Version history of a way, more readable than osm.org's own list.
export const DEEP_HISTORY_URL = (wayId) => `https://osmlab.github.io/osm-deep-history/#/way/${wayId}`;

// Time between two snapshots while playing. A step never starts before the
// previous one is drawn, so on a slow device playing just gets slower.
export const PLAY_INTERVAL_MS = 900;

// Applying a date re-filters every loaded tile in the workers. While the timeline
// is dragged or a key held down, the map takes the newest date once the input
// rests for TIME_SETTLE_MS, and at least every TIME_MAX_WAIT_MS while it keeps
// moving. The readout, legend and chart follow the pointer without delay.
export const TIME_SETTLE_MS = 120;
export const TIME_MAX_WAIT_MS = 450;

// Before the hover halo follows the pointer. Long enough that sweeping across the
// map does not light up every line on the way, short enough not to feel late.
export const HOVER_DELAY_MS = 80;

// The ?t= parameter is written after the date has rested this long; browsers
// throttle history.replaceState when it is called on every drag step.
export const URL_DEBOUNCE_MS = 400;

export function radinfraURL({ zoom, lat, lng, wayId = null, bounds = null }) {
    const parts = [
        `map=${zoom.toFixed(1)}/${lat.toFixed(4)}/${lng.toFixed(4)}`,
        `config=${LIVE.siteConfig}`,
        'v=2',
    ];
    if (wayId && bounds) {
        const [west, south, east, north] = bounds.map((n) => n.toFixed(6));
        parts.push(`f=10|way/${wayId}|${west}|${south}|${east}|${north}`);
    }
    return `${LIVE.siteURL}?${parts.join('&')}`;
}
