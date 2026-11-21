// Configuration for the radinfra-history map.

const base = new URL('./', window.location.href);
const LOCAL = ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname);
const params = new URLSearchParams(window.location.search);

// The region the page shows: Berlin. Locally ?region= shows another one built into
// site/data/ (germany, saarland): a preview until the page is made for Germany.
const asked = params.get('region');
export const REGION_SLUG = LOCAL && asked && /^[a-z-]+$/.test(asked) ? asked : 'berlin';

// The data is not on GitHub Pages but in the B2 bucket radinfra-history, behind
// Cloudflare (scripts/publish_b2.sh). Locally scripts/serve.py serves the same
// layout from site/data/, where the pipeline and scripts/fetch_data.sh put it;
// ?data=remote there loads the bucket instead.
const BUCKET_URL = 'https://tiles.vizsim.de/file/radinfra-history/';
const REMOTE = !LOCAL || params.get('data') === 'remote';
export const DATA_BASE = new URL(`${REGION_SLUG}/`, REMOTE ? BUCKET_URL : new URL('data/', base)).href;

// The one file that changes: it names the current build (pipeline/builds.py).
export const TIMELINE_URL = new URL('timeline.json', DATA_BASE).href;

// Everything else is in that build's folder, which never changes once published:
// a browser cannot combine tiles and popup data of two builds.
const buildFile = (build, name) => new URL(`builds/${build}/${name}`, DATA_BASE).href;
// MapLibre Tiles in three archives (pipeline/tiles.py): details, overview, and far
// out (Germany whole, without the shortest lines).
export const tilesURL = (build) => `pmtiles://${buildFile(build, 'tiles.pmtiles')}`;
export const overviewTilesURL = (build) => `pmtiles://${buildFile(build, 'tiles_lo.pmtiles')}`;
export const farTilesURL = (build) => `pmtiles://${buildFile(build, 'tiles_far.pmtiles')}`;
export const TILE_ENCODING = 'mlt';
// The popup's dates, read per line on click (pipeline/versions.py, write_spans).
export const spansURL = (build) => buildFile(build, 'spans.bin');

// radinfra.de's own "details" style. Its layers are drawn as they are — colours,
// widths, dashes, filters — against our source, plus a time filter. Keeping the
// file verbatim means picking up a newer tilda style is a matter of replacing it.
// pipeline/style.py reads the same file for the km counts, so legend and numbers
// cannot disagree about which category belongs to which row.
export const STYLE_URL = new URL('radinfra_details_style.json', base).href;

export const SOURCE_ID = 'history';
export const OVERVIEW_SOURCE_ID = 'history-lo';
export const FAR_SOURCE_ID = 'history-far';
export const LAYER_PREFIX = 'rh-';
export const HOVER_LAYER_ID = 'rh-hover';

// Three layers (pipeline/tiles.py): every version with its id and name from z12,
// below that a merged overview without ids, and below z9 the same without short
// lines and sides. Lines can be hovered and clicked from DETAIL_MINZOOM on.
export const SOURCE_LAYER = 'bikelanes';
export const OVERVIEW_SOURCE_LAYER = 'bikelanes_lo';
export const FAR_SOURCE_LAYER = 'bikelanes_far';
export const DETAIL_MINZOOM = 12;
export const OVERVIEW_MINZOOM = 9;

// Invisible click target in the tilda style; hit testing here does not need it.
export const SKIP_LAYER_IDS = ['hitarea-bikelanes-details'];

// radinfra.de, for the popup's link to a way's current state there.
export const RADINFRA_SITE = {
    url: 'https://tilda-geo.de/regionen/radinfra',
    // Opaque hash from a shared radinfra.de link (taken over from vizsim/bikeneat);
    // it selects which layers the target page shows.
    config: '1p2va4k.7h3d.9fm70g',
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

// The attribution names OpenStreetMap once, for basemap and data alike, and in the
// control itself, so it stays when the lines or the basemap are switched off. The
// sources only add what is theirs; the basemap's own credit, which names
// OpenStreetMap again, is replaced.
const link = (href, text) => `<a href="${href}" target="_blank" rel="noopener">${text}</a>`;
export const ATTRIBUTION = [
    link('https://maplibre.org/', 'MapLibre'),
    link('https://www.openstreetmap.org/copyright', '© OpenStreetMap-Mitwirkende'),
];
export const DATA_ATTRIBUTION = `Klassifikation nach ${link('https://radinfra.de', 'radinfra.de')} · ${link('https://github.com/rush42/OSMnexus', 'OSMnexus')}`;
export const BASEMAP_ATTRIBUTION = `${link('https://openfreemap.org', 'OpenFreeMap')} ${link('https://www.openmaptiles.org/', '© OpenMapTiles')}`;

// Where the map starts. Lines are drawn from z9 on; Germany can be zoomed out
// further to find one's way.
const VIEWS = {
    berlin: { center: [13.4050, 52.5100], zoom: 11, minZoom: 9 },
    germany: { center: [10.45, 51.16], zoom: 9, minZoom: 5 },
    saarland: { center: [6.95, 49.38], zoom: 10, minZoom: 8 },
};
export const initialMapConfig = { ...(VIEWS[REGION_SLUG] ?? VIEWS.berlin), maxZoom: 18 };

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

// Bezirke and Ortsteile with km per category and snapshot (pipeline/areas.py).
export const areasURL = (build) => buildFile(build, 'areas.json');
export const areasGeojsonURL = (build) => buildFile(build, 'areas.geojson');

// Berlin's aerial photo series, from the OSM Editor Layer Index (scripts/imagery);
// part of the page, not of the data.
export const IMAGERY_URL = new URL(`data/${REGION_SLUG}_imagery.json`, base).href;

// Version history of a way, more readable than osm.org's own list.
// Periods in which the curve mostly shows a change of mapping, not of the streets;
// drawn as a faint band in the timeline. 2021–2024: cycle tracks tagged on the
// road became ways of their own (incompletely tagged at first), and bicycle=yes was
// removed from many footways. The analysis is in the README.
// Berlin's own, so only on Berlin's page.
const NOTES = {
    berlin: [{ from: '2021-01-01', to: '2024-07-01', label: 'Umstellung der Kartierung', short: 'Umstellung' }],
};
export const TIMELINE_NOTES = NOTES[REGION_SLUG] ?? [];

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
        `config=${RADINFRA_SITE.config}`,
        'v=2',
    ];
    if (wayId && bounds) {
        const [west, south, east, north] = bounds.map((n) => n.toFixed(6));
        parts.push(`f=10|way/${wayId}|${west}|${south}|${east}|${north}`);
    }
    return `${RADINFRA_SITE.url}?${parts.join('&')}`;
}
