// The side panel: legend, area picker, change table, and the background switch.

import { formatDate, formatKm, formatKmNumber, formatShortDate, sparkline } from './timeline.js';

// Legend swatch mirrors the mark: a short line, dashed where the style dashes it.
export function swatch(color, dash) {
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

// One row per category, top to bottom as radinfra.de lists them, each with a
// checkbox; the section's checkbox switches all of them and shows a mix as
// indeterminate. `onToggle(groupId, visible)` does the hiding on the map.
export function buildLegend(groups, snapshots, onToggle) {
    const list = document.getElementById('legend-items');
    const all = document.getElementById('legend-all');
    const zeros = snapshots.map(() => 0);
    const rows = [];
    let current = snapshots.length - 1;

    const syncAll = () => {
        const on = rows.filter((r) => r.input.checked).length;
        all.checked = on === rows.length;
        all.indeterminate = on > 0 && on < rows.length;
    };
    const setRow = (r, visible) => {
        r.input.checked = visible;
        r.row.classList.toggle('is-off', !visible);
        onToggle(r.group.id, visible);
    };

    // A style lists layers bottom to top; the legend reads top to bottom, so the
    // order is reversed for display only.
    for (const group of [...groups].reverse()) {
        const row = document.createElement('label');
        row.className = 'legend-item';
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.checked = true;

        const text = document.createElement('span');
        text.className = 'legend-label';
        text.textContent = group.label;

        const sparkHolder = document.createElement('span');
        sparkHolder.className = 'legend-spark-holder';
        const value = document.createElement('span');
        value.className = 'count-badge';

        row.append(input, swatch(group.color, group.dash), text, sparkHolder, value);
        list.appendChild(row);
        const r = { row, input, group, value, sparkHolder, spark: null, km: zeros };
        input.addEventListener('change', () => {
            setRow(r, input.checked);
            syncAll();
        });
        rows.push(r);
    }

    all.addEventListener('change', () => {
        all.indeterminate = false;
        for (const r of rows) if (r.input.checked !== all.checked) setRow(r, all.checked);
    });

    const legend = {
        // km per snapshot for every row, for the area now selected.
        setData(kmByGroup) {
            for (const r of rows) {
                r.km = kmByGroup.get(r.group.id) ?? zeros;
                r.spark = sparkline(r.km, r.group.color);
                r.sparkHolder.replaceChildren(r.spark.node);
            }
            legend.update(current);
        },
        update(index) {
            current = index;
            for (const r of rows) {
                if (!r.spark) continue;
                r.spark.mark(index);
                r.value.textContent = formatKmNumber(r.km[index]);
                r.row.title = `${r.group.label}: ${formatKm(r.km[index])} am ${formatDate(snapshots[index].date)}`;
            }
        },
    };
    return legend;
}

// The timeline's table view: total km per snapshot.
export function createTable(snapshots) {
    const tbody = document.querySelector('#tl-table tbody');
    return {
        setValues(totals) {
            tbody.replaceChildren();
            snapshots.forEach((snapshot, i) => {
                const tr = document.createElement('tr');
                const date = document.createElement('td');
                date.textContent = formatShortDate(snapshot.date);
                const km = document.createElement('td');
                km.textContent = formatKm(totals[i]);
                tr.append(date, km);
                tbody.appendChild(tr);
            });
        },
    };
}

function formatDelta(value) {
    const rounded = Math.abs(value) < 10 ? Math.round(value * 10) / 10 : Math.round(value);
    if (rounded === 0) return '±0';
    return `${rounded > 0 ? '+' : '−'}${formatKmNumber(Math.abs(rounded))}`;
}

// What changed in the selected area between two snapshots, per category. Rows
// without any km at either date are left out; the sum covers all of them.
export function renderChanges(groups, scope, snapshots, fromIndex, toIndex) {
    const table = document.getElementById('change-table');
    const cell = (tag, text, cls) => {
        const node = document.createElement(tag);
        node.textContent = text;
        if (cls) node.className = cls;
        return node;
    };

    const headRow = document.createElement('tr');
    headRow.append(
        cell('th', 'km je Führungsform'),
        cell('th', formatShortDate(snapshots[fromIndex].date), 'num'),
        cell('th', formatShortDate(snapshots[toIndex].date), 'num'),
        cell('th', 'Differenz', 'num'),
    );
    table.querySelector('thead').replaceChildren(headRow);

    const body = table.querySelector('tbody');
    body.replaceChildren();
    let sumFrom = 0;
    let sumTo = 0;
    for (const group of [...groups].reverse()) {
        const km = scope.kmByGroup.get(group.id);
        const from = km?.[fromIndex] ?? 0;
        const to = km?.[toIndex] ?? 0;
        sumFrom += from;
        sumTo += to;
        if (from < 0.05 && to < 0.05) continue;
        const tr = document.createElement('tr');
        const name = document.createElement('td');
        name.className = 'change-name';
        name.append(swatch(group.color, group.dash), cell('span', group.label));
        const delta = cell('td', formatDelta(to - from), 'num');
        if (Math.abs(to - from) < 0.05) delta.classList.add('is-zero');
        tr.append(name, cell('td', formatKmNumber(from), 'num'), cell('td', formatKmNumber(to), 'num'), delta);
        body.appendChild(tr);
    }
    const total = document.createElement('tr');
    total.append(cell('td', 'Summe'), cell('td', formatKmNumber(sumFrom), 'num'),
        cell('td', formatKmNumber(sumTo), 'num'), cell('td', formatDelta(sumTo - sumFrom), 'num'));
    table.querySelector('tfoot').replaceChildren(total);
}

// The "from" date of the change table.
export function fillChangeFrom(snapshots, onChange) {
    const select = document.getElementById('change-from');
    snapshots.forEach((s, i) => {
        const option = document.createElement('option');
        option.value = String(i);
        option.textContent = formatShortDate(s.date);
        select.appendChild(option);
    });
    select.value = '0';
    select.addEventListener('change', () => onChange(Number(select.value)));
    return () => Number(select.value);
}

// The area list: Berlin, then Bezirke, then Ortsteile, each alphabetically.
export function fillAreaSelect(areas, onChange) {
    const select = document.getElementById('area-select');
    const add = (parent, area, label = area.name) => {
        const option = document.createElement('option');
        option.value = area.slug;
        option.textContent = label;
        parent.appendChild(option);
    };
    const berlin = areas.get('berlin');
    if (berlin) add(select, berlin, 'Berlin gesamt');
    for (const [level, label] of [['Bezirk', 'Bezirke'], ['Ortsteil', 'Ortsteile']]) {
        const group = document.createElement('optgroup');
        group.label = label;
        for (const area of areas.byLevel(level)) add(group, area);
        select.appendChild(group);
    }
    select.addEventListener('change', () => onChange(select.value));
    return (slug) => {
        select.value = slug;
    };
}

export function describeArea(area, areas) {
    const info = document.getElementById('area-info');
    const km2 = `${area.km2.toLocaleString('de-DE', { maximumFractionDigits: 1 })} km²`;
    if (area.level === 'Ortsteil') {
        const parent = areas.get(area.parent);
        info.textContent = `Ortsteil${parent && parent.slug !== 'berlin' ? ` im Bezirk ${parent.name}` : ''} · ${km2}`;
    } else if (area.level === 'Bezirk') {
        info.textContent = `Bezirk · ${km2}`;
    } else {
        info.textContent = `innerhalb der Landesgrenze · ${km2}`;
    }
}

// Map or aerial photo, from the thumbnails in the background panel. While the
// photo is on, a chip in the timeline names the year it shows.
export function bindBasemapSwitch(imagery, currentDate, variants = []) {
    const buttons = [...document.querySelectorAll('.basemap-btn[data-basemap]')];
    const note = document.getElementById('aerial-note');
    const chip = document.getElementById('tl-aerial');
    if (!imagery) {
        for (const b of buttons) {
            if (b.dataset.basemap === 'aerial') {
                b.disabled = true;
                b.title = 'Luftbilder sind gerade nicht verfügbar';
            }
        }
        return () => {};
    }
    const years = variants.map((v) => v.label).filter(Boolean);
    const intro = `Das Luftbild folgt der Zeitleiste: zu jedem Stand das jüngste Bild davor`
        + `${years.length ? ` (${years[0]}–${years[years.length - 1]})` : ''}.`;
    note.textContent = intro;

    const describe = () => {
        const v = imagery.current;
        if (!imagery.on || !v) {
            note.textContent = intro;
            chip.hidden = true;
            return;
        }
        // Before the first capture there is nothing older to show; say so.
        const relation = v.start > currentDate()
            ? 'das früheste verfügbare, aufgenommen nach dem gewählten Datenstand'
            : 'das jüngste vor dem gewählten Datenstand';
        const strong = document.createElement('strong');
        strong.textContent = `Luftbild ${v.label}`;
        const credit = document.createElement('span');
        credit.className = 'map-settings-credit';
        credit.textContent = v.attribution;
        note.replaceChildren(strong, document.createTextNode(` (aufgenommen ${v.captured}), ${relation}.`), credit);
        chip.textContent = `Luftbild ${v.label}`;
        chip.dataset.tip = `Aufgenommen ${v.captured}, ${relation}.`;
        chip.hidden = false;
    };
    for (const button of buttons) {
        button.addEventListener('click', () => {
            const aerial = button.dataset.basemap === 'aerial';
            for (const b of buttons) {
                b.classList.toggle('selected', b === button);
                b.setAttribute('aria-checked', String(b === button));
            }
            imagery.setOn(aerial, currentDate());
            describe();
        });
    }
    return describe;
}

// Most of the wait when zooming is the basemap, so the indicator follows the
// map's own load state rather than our source (as in vizsim/bikeneat).
export function bindLoadingIndicator(map) {
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
