// The area search, as a geocoder does it: type part of a name and pick from the
// suggestions. Prefix matches come first, then matches inside a name; a name that
// several areas share says where each one is. With nothing typed, the list shows
// the parts of the current area (Deutschland: its Länder; a Kreis: its Gemeinden)
// and the way back up, so the areas can be browsed as well.

const MAX = 12;
// Higher levels first among equal matches: "Berlin" the Land before a Berlin street.
const RANK = { Staat: 0, Land: 1, Kreis: 2, Bezirk: 3, Gemeinde: 4, Ortsteil: 5 };

// Case, umlauts and accents do not matter: "München", "muenchen" and "munchen" all
// find it. Two forms: umlauts written out (ü → ue), and dropped (ü → u).
const simple = (text) => text.replace(/ß/g, 'ss').normalize('NFKD').replace(/\p{M}/gu, '')
    .replace(/[^a-z0-9]+/g, ' ').trim();
export function fold(text) {
    return simple(text.toLowerCase().replace(/ä/g, 'ae').replace(/ö/g, 'oe').replace(/ü/g, 'ue'));
}
export function plain(text) {
    return simple(text.toLowerCase());
}

export function entryFor(area) {
    const forms = [...new Set([fold(area.name), plain(area.name)])];
    return { area, forms, words: forms.flatMap((f) => f.split(' ')) };
}

export function search(entries, query, max = MAX) {
    const queries = [...new Set([fold(query), plain(query)])].filter(Boolean);
    if (!queries.length) return [];
    const scored = [];
    for (const entry of entries) {
        let score = null;
        for (const q of queries) {
            for (const form of entry.forms) {
                if (form === q) score = 0;
                else if (form.startsWith(q)) score = Math.min(score ?? 9, 1);
                else if (form.includes(q)) score = Math.min(score ?? 9, entry.words.some((w) => w.startsWith(q)) ? 2 : 3);
            }
        }
        if (score !== null) scored.push([score, RANK[entry.area.level] ?? 9, entry.area.name.length, entry]);
    }
    scored.sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2] || a[3].area.name.localeCompare(b[3].area.name, 'de'));
    return scored.slice(0, max).map((s) => s[3].area);
}

export function createAreaSearch(input, results, areas, { onPick }) {
    const entries = areas.list.map(entryFor);
    const sameName = new Map();
    for (const e of entries) sameName.set(e.forms[0], (sameName.get(e.forms[0]) ?? 0) + 1);
    let shown = [];
    let active = -1;

    const label = (area) => (area === areas.top ? `${area.name} gesamt` : area.name);
    function where(area) {
        const parent = area.parent ? areas.get(area.parent) : null;
        const shared = sameName.get(fold(area.name)) > 1;
        return [area.level, parent && (shared || area.level !== 'Land') ? parent.name : null]
            .filter(Boolean).join(' · ');
    }

    function browse() {
        const here = areas.current ?? areas.top;
        const up = here.parent ? areas.get(here.parent) : null;
        const parts = [...areas.children(here.slug)].sort((a, b) => a.name.localeCompare(b.name, 'de'));
        // A Gemeinde has no parts: its neighbours in the Kreis instead.
        const list = parts.length ? parts
            : (up ? areas.children(up.slug).filter((a) => a !== here) : [])
                .sort((a, b) => a.name.localeCompare(b.name, 'de'));
        return [...(up ? [{ area: up, up: true }] : []), ...list.map((area) => ({ area }))];
    }

    function render(items) {
        shown = items;
        active = -1;
        results.replaceChildren(...items.map(({ area, up }, i) => {
            const li = document.createElement('li');
            li.setAttribute('role', 'option');
            li.id = `area-result-${i}`;
            li.dataset.index = String(i);
            const name = document.createElement('span');
            name.className = 'area-results__name';
            name.textContent = up ? `↑ ${label(area)}` : label(area);
            const meta = document.createElement('span');
            meta.className = 'area-results__meta';
            meta.textContent = up ? 'zurück' : where(area);
            li.append(name, meta);
            return li;
        }));
        if (!items.length && input.value.trim()) {
            const li = document.createElement('li');
            li.className = 'area-results__empty';
            li.textContent = 'Kein Gebiet mit diesem Namen';
            results.append(li);
        }
        results.hidden = !results.children.length;
        input.setAttribute('aria-expanded', String(!results.hidden));
    }

    function highlight(i) {
        active = i;
        for (const li of results.children) li.setAttribute('aria-selected', String(li.dataset.index === String(i)));
        const li = results.children[i];
        if (li) {
            li.scrollIntoView({ block: 'nearest' });
            input.setAttribute('aria-activedescendant', li.id);
        }
    }

    function close() {
        results.hidden = true;
        input.setAttribute('aria-expanded', 'false');
        input.removeAttribute('aria-activedescendant');
    }

    function pick(i) {
        const item = shown[i];
        if (!item) return;
        close();
        input.blur();
        onPick(item.area.slug);
    }

    const update = () => {
        const q = input.value.trim();
        render(q ? search(entries, q).map((area) => ({ area })) : browse());
    };

    input.addEventListener('focus', () => {
        input.select();
        render(browse());
    });
    input.addEventListener('input', update);
    input.addEventListener('keydown', (event) => {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            if (results.hidden) update();
            const n = shown.length;
            if (n) highlight(((active + (event.key === 'ArrowDown' ? 1 : -1)) % n + n) % n);
        } else if (event.key === 'Enter') {
            event.preventDefault();
            pick(active >= 0 ? active : 0);
        } else if (event.key === 'Escape') {
            close();
            setValue(areas.current);
            input.blur();
        }
    });
    // mousedown, not click: the list would close on the input's blur first.
    results.addEventListener('mousedown', (event) => {
        const li = event.target.closest('li[data-index]');
        if (!li) return;
        event.preventDefault();
        pick(Number(li.dataset.index));
    });
    input.addEventListener('blur', () => {
        close();
        setValue(areas.current);
    });

    function setValue(area) {
        if (area && document.activeElement !== input) input.value = label(area);
    }
    return setValue;
}
