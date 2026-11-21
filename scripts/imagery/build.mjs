// Writes site/data/<region>_imagery.json: aerial photo series from the OSM Editor
// Layer Index (via @osm-editor-kit/maplibre-editor-layer-index), each with the area
// it covers and one entry per capture with a ready-made MapLibre raster source.
//
// The page picks, for the map's centre and each snapshot, the newest photo taken on
// or before it, so the background shows what mappers could have seen at that time
// (site/imagery.js). Generated at build time rather than loaded at runtime: the
// series change about once a year, and the page then needs no extra JavaScript or
// index download.
//
//     cd scripts/imagery && npm ci && node build.mjs berlin && node build.mjs germany

import { readFile, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import {
  getGeometry,
  getRasterSourceSpec,
  groupLayers,
  loadLayersInViewport,
} from '@osm-editor-kit/maplibre-editor-layer-index'

// Which series each region shows. `group`: an ELI series (grouped by year);
// `match`: the layers whose name it matches. Left out on purpose: infrared and raw
// imagery, NRW's iDOP/vDOP, and layers without a licence link (Metropole Ruhr).
// Bayern (statewide), Schleswig-Holstein and Bremen have no photo layer in ELI.
const SERIES = {
  berlin: [
    { place: 'Berlin', group: 'Berlin/Geoportal DOP20RGB' },
  ],
  germany: [
    { place: 'Berlin', group: 'Berlin/Geoportal DOP20RGB' },
    { place: 'Baden-Württemberg', match: /^Baden-Württemberg DOP20$/ },
    { place: 'Brandenburg', match: /^Brandenburg GeoBasis-DE\/LGB \(latest\) \/ DOP20c$/ },
    { place: 'Hamburg', match: /^Hamburg 20cm \(HH LGV DOP20 (belaubt|unbelaubt) \d{4}\)$/ },
    { place: 'Hessen', match: /^Hesse DOP20$/ },
    { place: 'Mecklenburg-Vorpommern', match: /^Mecklenburg-Vorpommern DOP20(, \d{4})?$/ },
    { place: 'Niedersachsen', match: /^Niedersachsen DOP20$/ },
    { place: 'Nordrhein-Westfalen', match: /^NRW Orthophoto \(RGB\)$/ },
    { place: 'Rheinland-Pfalz', match: /^Rheinland-Pfalz (historisches )?DOP20( \d{4})?$/ },
    { place: 'Saarland', match: /^Saarland DOP20$/ },
    { place: 'Sachsen', match: /^Saxony (historical aerial imagery [\d-]+|latest aerial imagery)$/ },
    { place: 'Sachsen-Anhalt', match: /^© GeoBasis-DE\/LVermGeo LSA, DOP20$/ },
    { place: 'Thüringen', match: /^Thüringen DOP20$/ },
    { place: 'Erlangen', match: /^Erlangen (Luftbild|TrueDOP) / },
    { place: 'Frankfurt am Main', match: /^Frankfurt am Main Luftbild \d{4}$/ },
    { place: 'Köln', match: /^Köln TrueDOP \d{4}$/ },
    { place: 'Ludwigshafen', match: /^Ludwigshafen \d{4}$/ },
    { place: 'Mainz', match: /^Mainz latest aerial imagery$/ },
    { place: 'München', match: /^Aktuelle Luftbilder der Landeshauptstadt München/ },
    { place: 'Stuttgart', match: /^Stuttgart Luftbild Stadtmessungsamt$/ },
    { place: 'Worms', match: /^Worms \d{4}$/ },
  ],
}
const BBOX = {
  berlin: [13.088, 52.338, 13.761, 52.675],
  germany: [5.86, 47.27, 15.05, 55.15],
}

const region = process.argv[2] ?? 'berlin'
if (!SERIES[region]) throw new Error(`no imagery series for region "${region}": ${Object.keys(SERIES).join(', ')}`)
const OUT = fileURLToPath(new URL(`../../site/data/${region}_imagery.json`, import.meta.url))

// ELI dates are YYYY, YYYY-MM or YYYY-MM-DD. The page compares full dates, so a year
// or month stands for its first day.
function isoStart(value) {
  if (!value) return null
  const [y, m = '01', d = '01'] = value.split('-')
  return `${y}-${m.padStart(2, '0')}-${d.padStart(2, '0')}`
}

// A capture without a date in ELI but with its year in the name (Hamburg's).
function yearInName(name) {
  return name.match(/\b(20\d\d)\)?$/)?.[1] ?? null
}

// Coverage to ~100 m, which is plenty to choose a photo by, without the points that
// rounding makes repeat.
function round(geometry) {
  const ring = (r) => r.map(([x, y]) => [Math.round(x * 1e3) / 1e3, Math.round(y * 1e3) / 1e3])
    .filter((p, i, all) => i === 0 || p[0] !== all[i - 1][0] || p[1] !== all[i - 1][1])
  const polygon = (rings) => rings.map(ring)
  return geometry.type === 'MultiPolygon'
    ? { type: 'MultiPolygon', coordinates: geometry.coordinates.map(polygon) }
    : { type: 'Polygon', coordinates: polygon(geometry.coordinates) }
}

// Rough size in square degrees, to put smaller (more specific) series first.
function size(geometry) {
  const polygons = geometry.type === 'MultiPolygon' ? geometry.coordinates : [geometry.coordinates]
  let total = 0
  for (const [ring] of polygons) {
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      total += (ring[j][0] - ring[i][0]) * (ring[j][1] + ring[i][1]) / 2
    }
  }
  return Math.abs(total)
}

// Each capture is tried as the page would load it: from another origin, at a point
// it covers. One that answers there with no image, or without a CORS header (the
// browser then drops the tiles), is left out: in November 2026 Frankfurt, Mainz,
// Sachsen-Anhalt (no CORS), Erlangen 2016/2018, Ludwigshafen 2022. One that draws
// only from some zoom on (Hessen from 15: its WMS renders from about 1:25,000) gets
// that as its minzoom. A server error is tried again and then kept with a warning:
// it may be passing (the codefor mirror of Berlin's photos has its moments).
const ORIGIN = 'http://127.0.0.1:8765'
const EARTH = 20037508.342789244

function inRing(ring, [x, y]) {
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i]
    const [xj, yj] = ring[j]
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside
  }
  return inside
}

// A point inside the coverage: its largest polygon's box, searched on a grid.
function pointIn(geometry) {
  const polygons = geometry.type === 'MultiPolygon' ? geometry.coordinates : [geometry.coordinates]
  const [outer] = polygons.reduce((a, b) => (size({ type: 'Polygon', coordinates: b }) > size({ type: 'Polygon', coordinates: a }) ? b : a))
  const xs = outer.map((p) => p[0])
  const ys = outer.map((p) => p[1])
  const [w, s, e, n] = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]
  for (const f of [0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8]) {
    for (const g of [0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8]) {
      const p = [w + (e - w) * f, s + (n - s) * g]
      if (inRing(outer, p)) return p
    }
  }
  return [(w + e) / 2, (s + n) / 2]
}

// The URL of the tile at a point and zoom, as MapLibre would request it.
function tileUrl(template, [lon, lat], z) {
  const n = 2 ** z
  const x = Math.floor((lon + 180) / 360 * n)
  const y = Math.floor((1 - Math.asinh(Math.tan(lat * Math.PI / 180)) / Math.PI) / 2 * n)
  const size = 2 * EARTH / n
  const bbox = [x * size - EARTH, EARTH - (y + 1) * size, (x + 1) * size - EARTH, EARTH - y * size]
  return template.replace('{bbox-epsg-3857}', bbox.map((v) => v.toFixed(3)).join(','))
    .replace('{z}', z).replace('{x}', x).replace('{y}', y)
}

async function answer(url) {
  for (let attempt = 0; ; attempt++) {
    try {
      const r = await fetch(url, { headers: { Origin: ORIGIN }, signal: AbortSignal.timeout(30000) })
      const bytes = (await r.arrayBuffer()).byteLength
      if (r.status >= 500 && attempt < 2) continue
      return {
        status: r.status,
        image: (r.headers.get('content-type') ?? '').startsWith('image/') && bytes > 100,
        cors: Boolean(r.headers.get('access-control-allow-origin')),
      }
    } catch (error) {
      if (attempt < 2) continue
      return { status: 0, image: false, cors: false }
    }
  }
}

async function probe(source, geometry) {
  const point = pointIn(geometry)
  const top = await answer(tileUrl(source.tiles[0], point, 16))
  if (top.status >= 500 || top.status === 0) return { keep: true, warn: `server error ${top.status}, kept` }
  if (!top.image) return { keep: false, why: `no image (HTTP ${top.status})` }
  if (!top.cors) return { keep: false, why: 'no CORS header' }
  let minzoom = 16
  for (let z = 15; z >= 9; z--) {
    const a = await answer(tileUrl(source.tiles[0], point, z))
    if (!(a.image && a.cors)) break
    minzoom = z
  }
  return { keep: true, minzoom }
}

function variant(layer, { label, order }) {
  const startDate = layer.startDate ?? yearInName(layer.name)
  const endDate = layer.startDate ? layer.endDate : startDate
  return {
    id: layer.id,
    // A capture over several years (Sachsen's 2006–2008) says so.
    label: label ?? (!startDate ? 'aktuell'
      : endDate && endDate.slice(0, 4) !== startDate.slice(0, 4) ? `${startDate.slice(0, 4)}–${endDate.slice(0, 4)}`
        : startDate.slice(0, 4)),
    order,
    captured: !startDate ? null : startDate === endDate || !endDate ? startDate : `${startDate} – ${endDate}`,
    start: isoStart(startDate),
    attribution: layer.attributionText ?? layer.attributionHtml ?? '',
    license: layer.licenseUrl ?? null,
    source: getRasterSourceSpec(layer, { pixelRatio: 1 }),
  }
}

const layers = await loadLayersInViewport(BBOX[region], {
  // Older captures are filed as historic photos, newer ones as photos.
  categories: ['photo', 'historicphoto'],
  countryCodes: ['DE'],
  includeWorldwide: false,
})
const groups = groupLayers(layers)

const series = []
for (const spec of SERIES[region]) {
  let picked
  if (spec.group) {
    const group = groups.find((entry) => entry.grouped && entry.name === spec.group)
    if (!group) throw new Error(`series "${spec.group}" not in the index any more`)
    picked = group.variants.map((v) => [v, { label: v.group.label, order: v.group.order }])
  } else {
    const found = layers.filter((l) => spec.match.test(l.name))
      .sort((a, b) => String(isoStart(a.startDate ?? yearInName(a.name)) ?? '9999')
        .localeCompare(String(isoStart(b.startDate ?? yearInName(b.name)) ?? '9999')))
    if (!found.length) throw new Error(`${spec.place}: no layer matches ${spec.match}`)
    picked = found.map((l, order) => [l, { order }])
  }
  // Each capture has its own coverage: Mecklenburg-Vorpommern and Rheinland-Pfalz
  // fly about half the Land each year. The series covers what its largest capture
  // does; a capture that covers something else carries its own.
  let geometries = await Promise.all(picked.map(([layer]) => getGeometry(layer)))
  if (geometries.some((g) => !g)) throw new Error(`${spec.place}: a capture without coverage in the index`)
  const probes = await Promise.all(picked.map(([layer], i) => probe(variant(layer, {}).source, geometries[i])))
  for (const [i, result] of probes.entries()) {
    const name = picked[i][0].name
    if (!result.keep) console.log(`  left out: ${name}: ${result.why}`)
    else if (result.warn) console.log(`  warning: ${name}: ${result.warn}`)
  }
  const kept = picked.map((p, i) => [p, geometries[i], probes[i]]).filter(([, , r]) => r.keep)
  if (!kept.length) {
    console.log(`  left out: ${spec.place}: no capture loads`)
    continue
  }
  picked = kept.map(([p]) => p)
  geometries = kept.map(([, g]) => g)
  const coverage = round(geometries.reduce((a, b) => (size(b) > size(a) ? b : a)))
  const variants = picked.map(([layer, meta], i) => {
    const v = variant(layer, meta)
    // Below this zoom the service draws nothing: MapLibre then asks for no tiles.
    const minzoom = kept[i][2].minzoom
    if (minzoom > 9) v.source.minzoom = minzoom
    const own = round(geometries[i])
    return JSON.stringify(own) === JSON.stringify(coverage) ? v : { ...v, coverage: own }
  })
  series.push({
    place: spec.place,
    coverage,
    variants: variants.sort((a, b) => a.order - b.order),
    size: size(coverage),
  })
}
series.sort((a, b) => a.size - b.size)
for (const s of series) delete s.size

const pkg = JSON.parse(await readFile(
  fileURLToPath(new URL('./node_modules/@osm-editor-kit/maplibre-editor-layer-index/package.json', import.meta.url)),
  'utf8',
))
// Readable, except the coverage polygons: one line each.
const polygons = []
const text = JSON.stringify({
  source: `Editor Layer Index via ${pkg.name}@${pkg.version}`,
  series,
}, (k, v) => (k === 'coverage' ? `@coverage${polygons.push(JSON.stringify(v)) - 1}@` : v), 1)
await writeFile(OUT, text.replace(/"@coverage(\d+)@"/g, (_, i) => polygons[i]) + '\n')
for (const s of series) {
  const dated = s.variants.filter((v) => v.start)
  console.log(`  ${s.place}: ${s.variants.length} ${dated.length ? `${dated[0].label} … ${dated.at(-1).label}` : 'ohne Datum'}`
    + `${dated.length < s.variants.length && dated.length ? ' + ohne Datum' : ''}`)
}
console.log(`${OUT}: ${series.length} series`)
