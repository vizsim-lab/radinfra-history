// Aerial photos as the background, matched to the selected snapshot.
//
// The capture list (site/data/berlin_imagery.json) comes from the OSM Editor Layer
// Index, see scripts/imagery/build.mjs. For a snapshot the newest photo taken on or
// before that date is shown, so the background is what mappers could have seen at
// the time. Photos dated only by year count from 1 January. Before the first
// capture the oldest photo is shown, and the panel says that it is newer.
//
// While the photo is on, the vector basemap keeps only its labels; its fills would
// otherwise cover the photo.

const RASTER_PAINT = {
    // Slightly muted, so the category colours stay readable on top.
    'raster-saturation': -0.3,
    'raster-contrast': -0.05,
    'raster-fade-duration': 0,
};

export function imageryFor(variants, isoDate) {
    let pick = null;
    for (const v of variants) {
        if (v.start && v.start <= isoDate && (!pick || v.start > pick.start
            || (v.start === pick.start && v.order > pick.order))) {
            pick = v;
        }
    }
    // Before the first capture: the oldest one is the closest there is.
    return pick ?? variants[0] ?? null;
}

export function createImagery(map, { variants, beforeId, isOwnLayer }) {
    let on = false;
    let current = null;
    const hiddenBase = new Map();

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
            if (on) {
                hideBasemap();
                const v = imageryFor(variants, isoDate);
                if (v) showVariant(v);
            } else {
                for (const v of variants) removeVariant(v);
                current = null;
                restoreBasemap();
            }
        },
        update(isoDate) {
            if (!on) return;
            const v = imageryFor(variants, isoDate);
            if (v) showVariant(v);
        },
    };
}
