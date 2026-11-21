// Aerial photos as the background, matched to the selected snapshot.
//
// The series (site/data/<region>_imagery.json) come from the OSM Editor Layer
// Index, see scripts/imagery/build.mjs: one per Land or city, each with the area it
// covers, smallest first, and a capture that covers less (Länder that fly half of
// their area each year) with its own. For the map's centre the series that cover it are tried
// in that order, so a city's photos come before its Land's, and the newest photo
// taken on or before the snapshot is shown: what mappers could have seen at the
// time. Photos dated only by year count from 1 January. With none that old, the
// earliest dated photo is shown and the panel says that it is newer; failing that,
// a photo without a date; where no series reaches, the last photo stays. Only one
// photo is shown at a time: most Länder serve
// opaque JPEGs, white beyond their border.
//
// While the photo is on, the vector basemap keeps only its labels; its fills would
// otherwise cover the photo.

const RASTER_PAINT = {
    // Slightly muted, so the category colours stay readable on top.
    'raster-saturation': -0.3,
    'raster-contrast': -0.05,
    'raster-fade-duration': 0,
};

// Point in a GeoJSON Polygon or MultiPolygon (lon/lat), holes included.
function inRing(ring, [x, y]) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const [xi, yi] = ring[i];
        const [xj, yj] = ring[j];
        if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
}

export function covers(geometry, point) {
    if (!geometry) return true;
    const polygons = geometry.type === 'MultiPolygon' ? geometry.coordinates : [geometry.coordinates];
    return polygons.some(([outer, ...holes]) => inRing(outer, point) && !holes.some((h) => inRing(h, point)));
}

function newestBefore(variants, isoDate) {
    let pick = null;
    for (const v of variants) {
        if (v.start && v.start <= isoDate && (!pick || v.start > pick.start
            || (v.start === pick.start && v.order > pick.order))) {
            pick = v;
        }
    }
    return pick;
}

export function imageryFor(series, isoDate, point) {
    const here = series.filter((s) => covers(s.coverage, point));
    // Off its one series (the edge of Berlin's extract) Berlin's photos are still the
    // nearest; off every one of several (Schleswig-Holstein) there is none: null.
    const candidates = here.length ? here : series.length === 1 ? series : [];
    // A capture with a coverage of its own (a Land flown half at a time) only there.
    const usable = (s) => s.variants.filter((v) => covers(v.coverage, point));
    for (const s of candidates) {
        const pick = newestBefore(usable(s), isoDate);
        if (pick) return pick;
    }
    // Before the first capture: the oldest one is the closest there is.
    const all = candidates.flatMap(usable);
    const dated = all.filter((v) => v.start)
        .sort((a, b) => a.start.localeCompare(b.start) || a.order - b.order);
    return dated[0] ?? all[0] ?? null;
}

export function createImagery(map, { series, beforeId, isOwnLayer, onChange = () => {} }) {
    let on = false;
    let current = null;
    let date = null;
    const hiddenBase = new Map();
    for (const s of series) {
        for (const v of s.variants) v.place = s.place;
    }
    const variants = series.flatMap((s) => s.variants);
    const centre = () => map.getCenter().toArray();

    const sourceId = (v) => `aerial-${v.id}`;

    function hideBasemap() {
        for (const layer of map.getStyle().layers) {
            if (isOwnLayer(layer.id) || layer.id.startsWith('aerial-')) continue;
            if (layer.type === 'symbol') continue;
            hiddenBase.set(layer.id, map.getLayoutProperty(layer.id, 'visibility') ?? 'visible');
            map.setLayoutProperty(layer.id, 'visibility', 'none');
        }
    }

    function restoreBasemap() {
        for (const [id, visibility] of hiddenBase) {
            if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', visibility);
        }
        hiddenBase.clear();
    }

    function removeVariant(v) {
        const id = sourceId(v);
        if (map.getLayer(id)) map.removeLayer(id);
        if (map.getSource(id)) map.removeSource(id);
    }

    // The new photo goes in on top of the old one, and the old one is removed once
    // the new tiles are drawn, so switching years never flashes the empty map.
    function showVariant(v) {
        if (current?.id === v.id) return;
        const previous = current;
        current = v;
        const id = sourceId(v);
        if (!map.getSource(id)) map.addSource(id, v.source);
        if (!map.getLayer(id)) {
            map.addLayer({ id, type: 'raster', source: id, paint: RASTER_PAINT }, beforeId);
        }
        if (previous) {
            const drop = () => {
                if (current?.id !== previous.id) removeVariant(previous);
            };
            map.once('idle', drop);
        }
    }

    // Moved into another Land or city: its photos.
    map.on('moveend', () => {
        if (!on || series.length < 2) return;
        const v = imageryFor(series, date, centre());
        if (v && v.id !== current?.id) {
            showVariant(v);
            onChange();
        }
    });

    return {
        get on() {
            return on;
        },
        get current() {
            return current;
        },
        setOn(value, isoDate) {
            if (value === on) return;
            on = value;
            date = isoDate;
            if (on) {
                hideBasemap();
                const v = imageryFor(series, isoDate, centre());
                if (v) showVariant(v);
            } else {
                for (const v of variants) removeVariant(v);
                current = null;
                restoreBasemap();
            }
        },
        update(isoDate) {
            date = isoDate;
            if (!on) return;
            const v = imageryFor(series, isoDate, centre());
            if (v) showVariant(v);
        },
    };
}
