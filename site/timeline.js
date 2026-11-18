// The timeline: total km over the snapshots, which is also the time slider.
//
// The chart is the control: click or drag anywhere on it to pick the nearest
// snapshot, or focus it and use the arrow keys (one step), Page Up/Down (one
// year at quarterly steps) and Home/End. The x axis is proportional to time,
// so an irregular last step (the history file's own end) sits where it belongs.

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

export function createTimeline(container, { snapshots, values: initialValues, label, onChange, notes = [] }) {
    let values = initialValues;
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

        // Periods in which the curve shows a change of mapping rather than of the
        // streets: a faint band, named in the margin above, where it never meets the
        // line. A short name where the long one would overrun the band.
        for (const note of notes) {
            const a = Math.max(t0, parseDate(note.from).getTime());
            const b = Math.min(t1, parseDate(note.to).getTime());
            if (b <= a) continue;
            const xa = x(a);
            const xb = x(b);
            el('rect', { class: 'tl-note', x: xa, y: MARGIN.top, width: xb - xa, height: PLOT_HEIGHT }, svg);
            const name = el('text', { class: 'tl-note-label', x: xa + 2, y: MARGIN.top - 5 }, svg);
            name.textContent = xb - xa >= note.label.length * 5.6 ? note.label : (note.short ?? note.label);
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
        const px = x(times[index]);
        tooltip.style.left = `${Math.min(Math.max(px, 60), width - 60)}px`;
        tooltip.classList.add('is-visible');
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
        // New series for the same snapshots, e.g. when the area changes.
        setValues(next) {
            values = next;
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
