// The numbers for what the map shows: km per legend row and snapshot of the lines
// in view, summed from the tiles the map has loaded (pipeline/tiles.py).
//
// Every tile holds every version with its dates (f, t), so one pass over them
// gives the whole timeline. Each line counts with its part inside the view and
// inside the tile it comes from: tiles repeat what crosses their edge. Lengths are
// measured as pipeline/versions.py measures, per side of the road and per way;
// against its own numbers for the same box, the count is within 0.1 % from the
// detail tiles and 0.5 % from the simplified overview tiles. Below the overview
// tiles the far ones leave short lines out, so there is no count.
//
// The lines are measured in the tiles' own coordinates, as MapLibre decoded them
// (`_vectorTileFeature`, not public); without those, from their longitude and
// latitude, a few times slower. MapLibre decodes a tile again for the first
// query on it, about 40 ms for a dense one, and a tile wholly in view is counted
// once and kept, so after a move only new tiles and those on the edge cost.

const EARTH_RADIUS_M = 6371008.8; // as pipeline/versions.py
const WORLD_M = 2 * Math.PI * EARTH_RADIUS_M;
const OPEN_END = 99991231;
const RAD = Math.PI / 180;
// Tiles wholly in view kept between counts, at most.
const KEEP_TILES = 1500;

// Web Mercator, as a share of the world from the west and from the north.
const mercX = (lng) => (lng + 180) / 360;
const mercY = (lat) => {
    const s = Math.sin(lat * RAD);
    return 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI);
};

// The line parts of a feature in its tile's units, as arrays of {x, y}.
function linesOf(f, extent) {
    const raw = f._vectorTileFeature;
    if (raw?.loadGeometry && raw.type === 2) return raw.loadGeometry();
    const g = f.geometry;
    const lines = g.type === 'MultiLineString' ? g.coordinates : g.type === 'LineString' ? [g.coordinates] : [];
    const n = 2 ** f.tile.z;
    return lines.map((line) => line.map(([lng, lat]) => ({
        x: (mercX(lng) * n - f.tile.x) * extent, y: (mercY(lat) * n - f.tile.y) * extent,
    })));
}

// Metres of a feature's lines inside `box` [x0, y0, x1, y1] (tile units, y down),
// each segment cut to the box (Liang–Barsky) and measured at its latitude.
function lengthIn(lines, box, tile, extent) {
    const [bx0, by0, bx1, by1] = box;
    const size = 2 ** tile.z * extent;
    const top = tile.y * extent;
    const scale = WORLD_M / size;
    let m = 0;
    for (const line of lines) {
        for (let i = 1; i < line.length; i += 1) {
            const { x: x0, y: y0 } = line[i - 1];
            const dx = line[i].x - x0;
            const dy = line[i].y - y0;
            let t0 = 0;
            let t1 = 1;
            let inside = true;
            for (const [p, q] of [[-dx, x0 - bx0], [dx, bx1 - x0], [-dy, y0 - by0], [dy, by1 - y0]]) {
                if (p === 0) {
                    if (q < 0) inside = false;
                } else if (p < 0) {
                    t0 = Math.max(t0, q / p);
                } else {
                    t1 = Math.min(t1, q / p);
                }
            }
            if (!inside || t0 >= t1) continue;
            const lat = Math.atan(Math.sinh(Math.PI * (1 - (2 * (top + y0 + dy * (t0 + t1) / 2)) / size)));
            m += scale * Math.cos(lat) * (t1 - t0) * Math.hypot(dx, dy);
        }
    }
    return m;
}

// `tiers`: where each source is drawn, [{ source, sourceLayer, minzoom, maxzoom }],
// the map zooms; `rowOf`: category → index of its legend row, of `rows` rows.
export function createViewCounter(map, { snapshots, rows, rowOf, tiers }) {
    const n = snapshots.length;
    const indexOf = new Map(snapshots.map((s, i) => [Number(s.key), i]));
    indexOf.set(OPEN_END, n);
    const kept = new Map();

    // Per row, the metres that start and end at each snapshot, added into `diff`.
    function addTile(diff, features, box, tile) {
        for (const f of features) {
            const row = rowOf.get(f.properties.category);
            const from = indexOf.get(Number(f.properties.f));
            const to = indexOf.get(Number(f.properties.t));
            if (row === undefined || from === undefined || to === undefined || from >= to) continue;
            const extent = f._vectorTileFeature?.extent ?? 4096;
            const m = lengthIn(linesOf(f, extent), box, tile, extent);
            if (m <= 0) continue;
            diff[row * (n + 1) + from] += m;
            diff[row * (n + 1) + to] -= m;
        }
    }

    return {
        // Whether the map is close enough for a count.
        canCount: () => map.getZoom() >= Math.min(...tiers.map((t) => t.minzoom)),
        // km per row and snapshot ([row][snapshot]) in `bounds` [west, south, east,
        // north], or null when the map is too far out.
        count(bounds) {
            const zoom = map.getZoom();
            const tier = tiers.find((t) => zoom >= t.minzoom && zoom < t.maxzoom);
            const source = tier && map.getSource(tier.source);
            if (!source) return null;
            // The tiles drawn now, and none of the coarser or finer ones kept for a fade.
            const z = Math.max(source.minzoom ?? 0, Math.min(source.maxzoom ?? 24, Math.floor(zoom)));
            const byTile = new Map();
            for (const f of map.querySourceFeatures(tier.source, { sourceLayer: tier.sourceLayer })) {
                if (f.tile?.z !== z) continue;
                const key = `${tier.source}/${z}/${f.tile.x}/${f.tile.y}`;
                if (!byTile.has(key)) byTile.set(key, { tile: f.tile, features: [] });
                byTile.get(key).features.push(f);
            }
            const [west, south, east, north] = bounds;
            const diff = new Float64Array(rows * (n + 1));
            for (const [key, { tile, features }] of byTile) {
                const extent = features[0]._vectorTileFeature?.extent ?? 4096;
                const k = 2 ** tile.z;
                // The view in this tile's units, and the tile's own square.
                const view = [(mercX(west) * k - tile.x) * extent, (mercY(north) * k - tile.y) * extent,
                    (mercX(east) * k - tile.x) * extent, (mercY(south) * k - tile.y) * extent];
                const box = [Math.max(0, view[0]), Math.max(0, view[1]), Math.min(extent, view[2]), Math.min(extent, view[3])];
                if (box[0] >= box[2] || box[1] >= box[3]) continue;
                if (view[0] > 0 || view[1] > 0 || view[2] < extent || view[3] < extent) {
                    addTile(diff, features, box, tile);
                    continue;
                }
                if (!kept.has(key)) {
                    const whole = new Float64Array(rows * (n + 1));
                    addTile(whole, features, box, tile);
                    if (kept.size >= KEEP_TILES) kept.delete(kept.keys().next().value);
                    kept.set(key, whole);
                }
                const whole = kept.get(key);
                for (let i = 0; i < diff.length; i += 1) diff[i] += whole[i];
            }
            const km = [];
            for (let r = 0; r < rows; r += 1) {
                const series = new Array(n);
                let sum = 0;
                for (let s = 0; s < n; s += 1) {
                    sum += diff[r * (n + 1) + s];
                    series[s] = Math.max(0, Math.round(sum) / 1000);
                }
                km.push(series);
            }
            return km;
        },
    };
}

// The part of the map not covered by the panel or the timeline, as bounds
// [west, south, east, north]: what the counts are for. The panel counts where it
// reaches down the side, not while folded to its title.
export function freeBounds(map) {
    const box = map.getContainer().getBoundingClientRect();
    const rect = (id) => {
        const r = document.getElementById(id)?.getBoundingClientRect();
        return r && r.width > 0 && r.height > 0 ? r : null;
    };
    let [left, top, right, bottom] = [0, 0, box.width, box.height];
    const panel = rect('panel');
    if (panel && panel.height > box.height * 0.5) {
        if (panel.width < box.width * 0.6) left = Math.max(left, panel.right - box.left);
        else top = Math.max(top, panel.bottom - box.top);
    }
    const timeline = rect('timeline-panel');
    if (timeline) bottom = Math.min(bottom, timeline.top - box.top);
    if (right - left < 40 || bottom - top < 40) [left, top, right, bottom] = [0, 0, box.width, box.height];
    const corners = [[left, top], [right, top], [right, bottom], [left, bottom]].map((p) => map.unproject(p));
    return [
        Math.min(...corners.map((c) => c.lng)), Math.min(...corners.map((c) => c.lat)),
        Math.max(...corners.map((c) => c.lng)), Math.max(...corners.map((c) => c.lat)),
    ];
}
