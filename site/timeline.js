// The timeline: total km over the snapshots, which is also the time slider.
//
// The chart is the control: click or drag anywhere on it to pick the nearest
// snapshot, or focus it and use the arrow keys (one step), Page Up/Down (one
// year at quarterly steps) and Home/End. The x axis is proportional to time,
// so an irregular last step (the history file's own end) sits where it belongs.
//
// Under the line, a bar for each step between two snapshots says how many km
// changed in OSM then (areas.js, activityOf): another legend row on the same
// object, came, or went. Whether built or only mapped, the data cannot say, so the
// bars do not either; where they are high and the line hardly moves, much was
// changed and little added up.

const SVG_NS = 'http://www.w3.org/2000/svg';
// The top margin holds the names of marked periods, above the line's reach.
const MARGIN = { top: 18, right: 14, bottom: 22, left: 64 };
const PLOT_HEIGHT = 64;

export const dateFormat = new Intl.DateTimeFormat('de-DE', {
    day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC',
});
const shortDateFormat = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC',
});

export function parseDate(iso) {
    return new Date(`${iso}T00:00:00Z`);
}

export function formatDate(iso) {
    return dateFormat.format(parseDate(iso));
}

export function formatShortDate(iso) {
    return shortDateFormat.format(parseDate(iso));
}

// Kilometres without the unit, for columns whose header carries it.
export function formatKmNumber(value) {
    const digits = Math.abs(value) < 10 ? 1 : 0;
    return value.toLocaleString('de-DE', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function formatKm(value) {
    return `${formatKmNumber(value)} km`;
}

function el(name, attrs = {}, parent = null) {
    const node = document.createElementNS(SVG_NS, name);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
    if (parent) parent.appendChild(node);
    return node;
}

// 1, 2 or 5 times a power of ten, so the axis reads 0 / 500 / 1.000 and not 0 / 437 / 874.
function niceStep(max, ticks) {
    const raw = Math.max(max / ticks, 1e-6);
    const power = 10 ** Math.floor(Math.log10(raw));
    for (const f of [1, 2, 5, 10]) {
        if (f * power >= raw) return f * power;
    }
    return 10 * power;
}

// The bars' scale: the highest bar of an area, but at least this share of its
// largest total, so that a quiet area's few changed metres stay small.
const BAR_FLOOR_SHARE = 0.01;
// The highest bar fills this much of the plot's height.
const BAR_HEIGHT_SHARE = 0.4;

const changedKm = (step) => (step ? step.changed + step.came + step.went : 0);

// "+1,2 km", "−3 km", "±0 km".
function signed(km) {
    const sign = km >= 0.05 ? '+' : km <= -0.05 ? '−' : '±';
    return `${sign}${formatKm(Math.abs(km) < 0.05 ? 0 : Math.abs(km))}`;
}

export function createTimeline(container, {
    snapshots, values: initialValues, label, onChange, notes: initialNotes = [], steps: initialSteps = [],
}) {
    let values = initialValues;
    let notes = initialNotes;
    let steps = initialSteps;
    const times = snapshots.map((s) => parseDate(s.date).getTime());
    const t0 = times[0];
    const t1 = times[times.length - 1];
    let current = snapshots.length - 1;
    let hover = null;
    let dragging = false;
    let width = 0;

    const svg = el('svg', { class: 'tl-svg', 'aria-hidden': 'true' }, container);
    const tooltip = document.createElement('div');
    tooltip.className = 'tl-tooltip';
    tooltip.setAttribute('aria-hidden', 'true');
    container.appendChild(tooltip);

    container.setAttribute('role', 'slider');
    container.setAttribute('tabindex', '0');
    container.setAttribute('aria-label', label);
    container.setAttribute('aria-valuemin', '0');
    container.setAttribute('aria-valuemax', String(snapshots.length - 1));

    const x = (t) => MARGIN.left + ((t - t0) / Math.max(1, t1 - t0)) * (width - MARGIN.left - MARGIN.right);
    // The total moves by a few percent while the categories underneath shift a
    // lot, so the y range is fitted to the data (clean ticks, labelled) rather than
    // starting at zero — a flat line at the top of the box would say nothing. With
    // no zero baseline there is deliberately no area fill, which would read as size.
    let step;
    let yMin;
    let yMax;
    function fitScale() {
        const lo = Math.min(...values);
        const hi = Math.max(...values);
        step = niceStep(Math.max(hi - lo, hi * 0.02, 0.1), 2);
        yMin = Math.max(0, Math.floor(lo / step) * step);
        yMax = Math.max(yMin + step, Math.ceil(hi / step) * step);
    }
    fitScale();
    const y = (v) => MARGIN.top + PLOT_HEIGHT - ((v - yMin) / (yMax - yMin)) * PLOT_HEIGHT;

    function nearest(px) {
        let best = 0;
        let bestDist = Infinity;
        times.forEach((t, i) => {
            const d = Math.abs(x(t) - px);
            if (d < bestDist) {
                bestDist = d;
                best = i;
            }
        });
        return best;
    }

    function render() {
        width = Math.max(240, container.clientWidth);
        const height = MARGIN.top + PLOT_HEIGHT + MARGIN.bottom;
        svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
        svg.setAttribute('width', width);
        svg.setAttribute('height', height);
        svg.replaceChildren();
        // The marked periods' bands lie under the bars, filled in once the bars'
        // key has taken its room in the margin.
        const bands = el('g', {}, svg);

        // What changed in OSM, step by step: one bar per step, from the snapshot
        // before to this one, along the bottom of the plot; its key in the margin
        // above, at the left, shorter on a narrow chart.
        const changed = times.map((_, i) => changedKm(steps[i]));
        const most = Math.max(...changed, 0);
        let keyRight = 0;
        if (most >= 0.05) {
            const scale = Math.max(most, BAR_FLOOR_SHARE * Math.max(...values, 0));
            const bars = el('g', { class: 'tl-activity' }, svg);
            for (let i = 1; i < times.length; i += 1) {
                const h = (changed[i] / scale) * BAR_HEIGHT_SHARE * PLOT_HEIGHT;
                if (h < 0.5) continue;
                const xa = x(times[i - 1]) + 1;
                const xb = x(times[i]) - 1;
                el('rect', { x: xa, y: MARGIN.top + PLOT_HEIGHT - h, width: Math.max(1, xb - xa), height: h }, bars);
            }
            const key = el('g', { class: 'tl-activity-key' }, svg);
            el('rect', { x: MARGIN.left, y: MARGIN.top - 13, width: 8, height: 8 }, key);
            const text = el('text', { x: MARGIN.left + 12, y: MARGIN.top - 5 }, key);
            text.textContent = width >= 520 ? 'Änderungen in OSM je Quartal' : 'OSM-Änderungen';
            keyRight = MARGIN.left + 12 + (text.getComputedTextLength?.() || 150);
        }

        // Periods in which the curve shows a change of mapping rather than of the
        // streets: a faint band, named in the margin above, where it never meets the
        // line, and right of the bars' key. A short name where the long one would
        // overrun the band, none where that would too.
        for (const note of notes) {
            const a = Math.max(t0, parseDate(note.from).getTime());
            const b = Math.min(t1, parseDate(note.to).getTime());
            if (b <= a) continue;
            const xa = x(a);
            const xb = x(b);
            el('rect', { class: 'tl-note', x: xa, y: MARGIN.top, width: xb - xa, height: PLOT_HEIGHT }, bands);
            const left = Math.max(xa + 2, keyRight + 8);
            const room = xb - left;
            const name = el('text', { class: 'tl-note-label', x: left, y: MARGIN.top - 5 }, bands);
            name.textContent = room >= note.label.length * 5.6 ? note.label : (note.short ?? note.label);
            if (room < name.textContent.length * 5.6) name.remove();
        }

        // Recessive grid: hairlines at clean values, labelled in muted ink.
        const grid = el('g', { class: 'tl-grid' }, svg);
        const digits = step < 1 ? 1 : 0;
        for (let i = 0, v = yMin; v <= yMax + step * 1e-6; i += 1, v = yMin + i * step) {
            el('line', { x1: MARGIN.left, x2: width - MARGIN.right, y1: y(v), y2: y(v) }, grid);
            const text = el('text', { x: MARGIN.left - 6, y: y(v), 'dominant-baseline': 'middle', 'text-anchor': 'end' }, grid);
            // The unit rides on the top tick instead of a separate label.
            const top = v + step > yMax + step * 1e-6;
            text.textContent = `${v.toLocaleString('de-DE', { minimumFractionDigits: digits, maximumFractionDigits: digits })}${top ? ' km' : ''}`;
        }

        // Years along the bottom, at 1 January. Where a year is narrower than its
        // label (a phone), only every second or third year is labelled.
        const axis = el('g', { class: 'tl-axis' }, svg);
        const firstYear = new Date(t0).getUTCFullYear();
        const lastYear = new Date(t1).getUTCFullYear();
        const yearPx = x(Date.UTC(firstYear + 1, 0, 1)) - x(Date.UTC(firstYear, 0, 1));
        const every = Math.max(1, Math.ceil(36 / Math.max(1, yearPx)));
        for (let year = firstYear; year <= lastYear; year += 1) {
            const t = Date.UTC(year, 0, 1);
            if (t < t0 || t > t1) continue;
            const px = x(t);
            el('line', { x1: px, x2: px, y1: MARGIN.top + PLOT_HEIGHT, y2: MARGIN.top + PLOT_HEIGHT + 4 }, axis);
            if (year % every !== 0) continue;
            const text = el('text', { x: px, y: MARGIN.top + PLOT_HEIGHT + 15, 'text-anchor': 'middle' }, axis);
            text.textContent = String(year);
        }

        // Where the snapshots are: the only positions the slider stops at.
        const stops = el('g', { class: 'tl-stops' }, svg);
        for (const t of times) {
            const px = x(t);
            el('line', { x1: px, x2: px, y1: MARGIN.top + PLOT_HEIGHT - 3, y2: MARGIN.top + PLOT_HEIGHT }, stops);
        }

        const points = times.map((t, i) => [x(t), y(values[i])]);
        const line = points.map(([px, py], i) => `${i ? 'L' : 'M'}${px.toFixed(1)},${py.toFixed(1)}`).join('');
        el('path', { class: 'tl-line', d: line }, svg);

        // Crosshair for the hovered snapshot, under the current marker.
        if (hover !== null && hover !== current) {
            const px = x(times[hover]);
            el('line', { class: 'tl-crosshair', x1: px, x2: px, y1: MARGIN.top, y2: MARGIN.top + PLOT_HEIGHT }, svg);
            el('circle', { class: 'tl-hover-dot', cx: px, cy: y(values[hover]), r: 3 }, svg);
        }

        const cx = x(times[current]);
        el('line', { class: 'tl-current-line', x1: cx, x2: cx, y1: MARGIN.top, y2: MARGIN.top + PLOT_HEIGHT }, svg);
        el('circle', { class: 'tl-current-dot', cx, cy: y(values[current]), r: 5 }, svg);

        renderTooltip();
    }

    function renderTooltip() {
        const index = hover ?? (dragging ? current : null);
        if (index === null) {
            tooltip.classList.remove('is-visible');
            return;
        }
        tooltip.replaceChildren();
        const value = document.createElement('strong');
        value.textContent = formatKm(values[index]);
        const when = document.createElement('span');
        when.textContent = formatDate(snapshots[index].date);
        tooltip.append(value, when);
        // What changed in OSM since the snapshot before, next to what that did to
        // the sum: much changed and the sum hardly moved means little added up.
        // The sum moves by what came, less what went, plus what the objects that go
        // on got longer or shorter, which is the rest: a way split in two keeps its
        // id on one part, which gets shorter, and the other part comes.
        const step = steps[index];
        if (changedKm(step) >= 0.05) {
            const net = values[index] - values[index - 1];
            const resized = net - (step.came - step.went);
            const parts = [[step.came, 'eingetragen'], [step.went, 'ausgetragen'],
                [step.changed, 'Führungsform gewechselt']]
                .filter(([km]) => km >= 0.05)
                .map(([km, what]) => `${formatKm(km)} ${what}`);
            if (Math.abs(resized) >= 0.05) {
                parts.push(`bestehende Linien ${signed(resized)} (etwa geteilte Wege)`);
            }
            const since = document.createElement('span');
            since.className = 'tl-tooltip__step';
            since.textContent = `seit ${formatShortDate(snapshots[index - 1].date)} in OSM: ${parts.join(', ')}; `
                + `Summe ${signed(net)}`;
            tooltip.append(since);
        }
        // Shown first, so it can be measured and kept inside the chart.
        tooltip.classList.add('is-visible');
        const half = Math.max(60, tooltip.offsetWidth / 2 + 4);
        tooltip.style.left = `${Math.min(Math.max(x(times[index]), half), width - half)}px`;
    }

    function set(index, { notify = true } = {}) {
        const next = Math.max(0, Math.min(snapshots.length - 1, index));
        const changed = next !== current;
        current = next;
        container.setAttribute('aria-valuenow', String(current));
        container.setAttribute('aria-valuetext',
            `${formatDate(snapshots[current].date)}, ${formatKm(values[current])}`);
        render();
        if (changed && notify) onChange(current);
    }

    function indexFromEvent(event) {
        const rect = container.getBoundingClientRect();
        return nearest(event.clientX - rect.left);
    }

    container.addEventListener('pointerdown', (event) => {
        dragging = true;
        container.setPointerCapture(event.pointerId);
        container.focus({ preventScroll: true });
        set(indexFromEvent(event));
    });
    container.addEventListener('pointermove', (event) => {
        const index = indexFromEvent(event);
        if (dragging) {
            hover = null;
            set(index);
            return;
        }
        if (index !== hover) {
            hover = index;
            render();
        }
    });
    const endDrag = () => {
        dragging = false;
        render();
    };
    container.addEventListener('pointerup', endDrag);
    container.addEventListener('pointercancel', endDrag);
    container.addEventListener('pointerleave', () => {
        hover = null;
        render();
    });

    // A year is four quarterly steps; with other step widths Page Up/Down still
    // moves by roughly a year.
    const yearSteps = Math.max(1, Math.round((snapshots.length - 1) / Math.max(1, (t1 - t0) / 3.156e10)));
    container.addEventListener('keydown', (event) => {
        const moves = {
            ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1,
            PageDown: -yearSteps, PageUp: yearSteps,
        };
        if (event.key in moves) {
            set(current + moves[event.key]);
        } else if (event.key === 'Home') {
            set(0);
        } else if (event.key === 'End') {
            set(snapshots.length - 1);
        } else {
            return;
        }
        event.preventDefault();
    });

    new ResizeObserver(() => render()).observe(container);
    set(current, { notify: false });

    return {
        set,
        get: () => current,
        // New series for the same snapshots, e.g. when the area changes, with the
        // area's steps (areas.js, activityOf) and marked periods.
        setValues(next, { steps: nextSteps = [], notes: nextNotes = notes } = {}) {
            values = next;
            steps = nextSteps;
            notes = nextNotes;
            fitScale();
            container.setAttribute('aria-valuetext',
                `${formatDate(snapshots[current].date)}, ${formatKm(values[current])}`);
            render();
        },
    };
}

// A small trend line for a legend row: muted line, the current snapshot as a dot
// in the row's colour. Own y scale per row, since the rows differ by orders of
// magnitude; the number next to it carries the actual value.
export function sparkline(values, color) {
    const w = 52;
    const h = 16;
    const svg = el('svg', {
        class: 'legend-spark', viewBox: `0 0 ${w} ${h}`, width: w, height: h, 'aria-hidden': 'true',
    });
    const max = Math.max(...values, 0.001);
    const pts = values.map((v, i) => [
        1 + (i / Math.max(1, values.length - 1)) * (w - 2),
        h - 2 - (v / max) * (h - 4),
    ]);
    el('polyline', { points: pts.map((p) => p.map((n) => n.toFixed(1)).join(',')).join(' ') }, svg);
    const dot = el('circle', { r: 2.2, fill: color }, svg);
    return {
        node: svg,
        mark(index) {
            dot.setAttribute('cx', pts[index][0].toFixed(1));
            dot.setAttribute('cy', pts[index][1].toFixed(1));
        },
    };
}
