// Where an area's km change, from the split the pipeline makes (pipeline/changes.py,
// pipeline/areas.py), and the hints the page gives with it.
//
// Every km that started at a snapshot is `replaced` (the same place was mapped
// before, only otherwise: retagged, redrawn, moved from the road's tags to a way of
// its own, or rebuilt, which the data cannot tell), `added` (more at a place that
// had cycling infrastructure already) or `new` (nothing there before: newly built
// or newly mapped). Every km that ended is `replaced`, `reduced` or `gone` the same
// way round. Between two snapshots, a legend row's change is exactly
//
//     replaced in − replaced out  +  added − reduced  +  new − gone,
//
// and the first part is no new network, whatever happened in the street. The page
// says what OSM holds at a place, never why: built or only mapped, the data cannot tell.

import { formatKmNumber } from './timeline.js';

// An area's numbers: chg rows [step, row, replaced, added, new, replaced, reduced,
// gone] and fl rows [step, row, from row, km], rows indexing `groupIds`.
export function changesOf(area, groupIds) {
    if (!area?.chg || !groupIds?.length) return null;
    return { rows: area.chg, flows: area.fl ?? [], groupIds };
}

function emptyPart() {
    return { delta: 0, replaced: 0, added: 0, places: 0, newPlaces: 0, gone: 0, from: new Map(), to: new Map() };
}

// The change between snapshots `from` and `to` (indices), per legend row and in all.
export function splitChanges(changes, groups, kmByGroup, from, to) {
    const parts = new Map(groups.map((g) => [g.id, emptyPart()]));
    const total = emptyPart();
    if (!changes || to <= from) return { parts, total };
    const { rows, flows, groupIds } = changes;
    for (const [step, g, sRep, sAdd, sNew, eRep, eRed, eGone] of rows) {
        if (step <= from || step > to) continue;
        const part = parts.get(groupIds[g]);
        if (!part) continue;
        for (const p of [part, total]) {
            p.replaced += sRep - eRep;
            p.added += sAdd - eRed;
            p.places += sNew - eGone;
            p.newPlaces += sNew;
            p.gone += eGone;
        }
    }
    // Retagged km between two legend rows, both ways, then netted: a track that went
    // from "unklar" to a cycleway and back is no change between them.
    const gross = new Map();
    for (const [step, g, source, km] of flows) {
        if (step <= from || step > to || g === source) continue;
        const key = `${groupIds[source]}\u0000${groupIds[g]}`;
        gross.set(key, (gross.get(key) ?? 0) + km);
    }
    for (const [key, km] of gross) {
        const [source, target] = key.split('\u0000');
        const net = km - (gross.get(`${target}\u0000${source}`) ?? 0);
        if (net <= 0 || !parts.has(source) || !parts.has(target)) continue;
        parts.get(target).from.set(source, net);
        parts.get(source).to.set(target, net);
    }
    for (const g of groups) {
        const km = kmByGroup.get(g.id);
        const part = parts.get(g.id);
        part.delta = (km?.[to] ?? 0) - (km?.[from] ?? 0);
        total.delta += part.delta;
    }
    return { parts, total };
}

// Per snapshot, how many km changed in OSM since the one before: came, went, or
// took another legend row, each place once (a cycleway moved from the road's tags
// to a way of its own is one change, a moved node none). Index 0 has nothing.
export function stepChanges(changes, count) {
    const steps = Array.from({ length: count }, (_, i) => (i ? { changed: 0 } : null));
    if (!changes) return steps.map(() => null);
    for (const [step, , , sAdd, sNew, , eRed, eGone] of changes.rows) {
        if (steps[step]) steps[step].changed += sAdd + sNew + eRed + eGone;
    }
    for (const [step, g, source, km] of changes.flows) {
        if (steps[step] && g !== source) steps[step].changed += km;
    }
    return steps;
}

export function formatDelta(value) {
    const rounded = Math.abs(value) < 10 ? Math.round(value * 10) / 10 : Math.round(value);
    if (rounded === 0) return '±0';
    return `${rounded > 0 ? '+' : '−'}${formatKmNumber(Math.abs(rounded))}`;
}

const km = (value) => `${formatKmNumber(Math.abs(value))} km`;

// The largest entries of a Map of km, as "„A“ 12 km, „B“ 3 km".
function largest(map, labelOf, count = 2) {
    return [...map.entries()]
        .filter(([, v]) => v >= 0.05)
        .sort((a, b) => b[1] - a[1])
        .slice(0, count)
        .map(([id, v]) => `„${labelOf(id)}“ ${km(v)}`)
        .join(', ');
}

// Does a legend row's change come mostly from places mapped otherwise? Then it is
// worth a hint, and its cell in the change table is marked.
export function mostlyRetagged(part, minKm) {
    return Math.abs(part.delta) >= minKm
        && Math.sign(part.replaced) === Math.sign(part.delta)
        && Math.abs(part.replaced) >= 0.5 * Math.abs(part.delta);
}

// What the page says about a change: which legend rows changed mostly by retagging
// (their rows in the change table get a note), a sentence on the whole, and a
// short one for the notice under the legend, on a row that grew if there is one.
export function changeHints(split, groups, { totalKm, fromDate }) {
    const minKm = Math.max(0.1, 0.01 * totalKm);
    const rows = groups
        .map((g) => ({ group: g, part: split.parts.get(g.id) }))
        .filter(({ part }) => part && mostlyRetagged(part, minKm))
        .sort((a, b) => Math.abs(b.part.replaced) - Math.abs(a.part.replaced));
    const { total } = split;
    let whole = null;
    if (total.newPlaces >= minKm || total.gone >= minKm) {
        whole = `Zusammen ${formatDelta(total.delta)} km. An Stellen, an denen vorher nichts erfasst war, `
            + `kamen ${km(total.newPlaces)} dazu: neu gebaut oder erstmals kartiert, das unterscheiden die Daten nicht.`;
        if (total.gone >= minKm) whole += ` An ${km(total.gone)} ist nichts mehr erfasst.`;
    }
    let notice = null;
    const lead = rows.find(({ part }) => part.delta > 0) ?? rows[0];
    if (lead) {
        const { group, part } = lead;
        notice = part.delta > 0
            ? `Seit ${fromDate}: Von ${formatDelta(part.delta)} km „${group.label}“ waren ${km(part.replaced)} `
                + 'vorher schon als andere Führungsform erfasst.'
            : `Seit ${fromDate}: „${group.label}“ ${formatDelta(part.delta)} km, davon ${km(part.replaced)} `
                + 'jetzt als andere Führungsform erfasst.';
        const more = rows.length - 1;
        if (more) notice += ` Ähnlich bei ${more === 1 ? 'einer weiteren Führungsform' : `${more} weiteren Führungsformen`}.`;
    }
    return { mostly: new Set(rows.map(({ group }) => group.id)), whole, notice, minKm };
}

// The note under a row of the change table that changed mostly at places with
// another category before or after. It says what OSM holds there, not why: whether
// the street was rebuilt or only mapped otherwise, the data cannot say.
export function retagNote(part, groups) {
    const labelOf = (id) => groups.find((g) => g.id === id)?.label ?? id;
    const other = part.replaced > 0 ? largest(part.from, labelOf) : largest(part.to, labelOf);
    const when = part.replaced > 0 ? 'vorher' : 'jetzt';
    return `davon ${km(part.replaced)} ${when} als andere Führungsform erfasst${other ? `, meist ${other}` : ''}`;
}

// The tooltip of a row of the change table: its change, split.
export function partTip(label, part, groups) {
    const labelOf = (id) => groups.find((g) => g.id === id)?.label ?? id;
    const lines = [`${label}: ${formatDelta(part.delta)} km.`];
    if (Math.abs(part.replaced) >= 0.05) {
        const other = part.replaced > 0 ? largest(part.from, labelOf, 3) : largest(part.to, labelOf, 3);
        lines.push(`An Stellen, die ${part.replaced > 0 ? 'vorher' : 'jetzt'} als andere Führungsform erfasst `
            + `sind: ${formatDelta(part.replaced)} km${other ? ` (${other})` : ''}.`);
    }
    if (Math.abs(part.added) >= 0.05) lines.push(`An erfassten Stellen dazu oder weg: ${formatDelta(part.added)} km.`);
    if (part.newPlaces >= 0.05) lines.push(`An Stellen, an denen vorher nichts erfasst war: +${km(part.newPlaces)}.`);
    if (part.gone >= 0.05) lines.push(`Stellen, an denen nichts mehr ist: −${km(part.gone)}.`);
    return lines.join(' ');
}
