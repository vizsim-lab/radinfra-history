// The side panel: legend, area picker, change table, background switch.

import { formatDate, formatKm, formatShortDate, sparkline } from './timeline.js';

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

// One row per category, top to bottom as radinfra.de lists them. Clicking a row
// hides the category; `onToggle(groupId, visible)` does the hiding on the map.
export function buildLegend(groups, snapshots, onToggle) {
    const list = document.getElementById('legend-items');
    const zeros = snapshots.map(() => 0);
    const rows = [];
    let current = snapshots.length - 1;

    // A style lists layers bottom to top; the legend reads top to bottom, so the
    // order is reversed for display only.
    for (const group of [...groups].reverse()) {
        const row = document.createElement('button');
        row.type = 'button';
        row.className = 'legend-item legend-item--toggle';
        row.setAttribute('role', 'switch');
        row.setAttribute('aria-checked', 'true');

        const text = document.createElement('span');
        text.className = 'legend-label';
        text.textContent = group.label;

        const sparkHolder = document.createElement('span');
        sparkHolder.className = 'legend-spark-holder';
        const value = document.createElement('span');
        value.className = 'legend-km';

        row.append(swatch(group.color, group.dash), text, sparkHolder, value);
        row.addEventListener('click', () => {
            const next = row.getAttribute('aria-checked') !== 'true';
            row.setAttribute('aria-checked', String(next));
            onToggle(group.id, next);
        });
        list.appendChild(row);
        rows.push({ row, group, value, sparkHolder, spark: null, km: zeros });
    }

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
                r.value.textContent = formatKm(r.km[index]);
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
    if (rounded === 0) return '±0 km';
    return `${rounded > 0 ? '+' : '−'}${formatKm(Math.abs(rounded))}`;
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
        cell('th', 'Führungsform'),
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
        tr.append(name, cell('td', formatKm(from), 'num'), cell('td', formatKm(to), 'num'), delta);
        body.appendChild(tr);
    }
    const total = document.createElement('tr');
    total.append(cell('td', 'Summe'), cell('td', formatKm(sumFrom), 'num'),
        cell('td', formatKm(sumTo), 'num'), cell('td', formatDelta(sumTo - sumFrom), 'num'));
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

export function bindBasemapSwitch(imagery, currentDate) {
    const buttons = [...document.querySelectorAll('[data-basemap]')];
    const note = document.getElementById('aerial-note');
    if (!imagery) {
        for (const b of buttons) if (b.dataset.basemap === 'aerial') b.disabled = true;
        return () => {};
    }
    const describe = () => {
        const v = imagery.current;
        note.hidden = !imagery.on || !v;
        if (!v) return;
        note.replaceChildren();
        const strong = document.createElement('strong');
        strong.textContent = `Luftbild ${v.label}`;
        // Before the first capture there is nothing older to show; say so.
        const relation = v.start > currentDate()
            ? 'das früheste verfügbare, aufgenommen nach dem gewählten Datenstand'
            : 'das jüngste vor dem gewählten Datenstand';
        note.append(strong, document.createTextNode(` (aufgenommen ${v.captured}), ${relation}. `));
        const credit = document.createElement('span');
        credit.className = 'legend-note__credit';
        credit.textContent = v.attribution;
        note.append(credit);
    };
    for (const button of buttons) {
        button.addEventListener('click', () => {
            const aerial = button.dataset.basemap === 'aerial';
            for (const b of buttons) b.setAttribute('aria-checked', String(b === button));
            imagery.setOn(aerial, currentDate());
            describe();
        });
    }
    return describe;
}

export function bindPanelToggle() {
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
