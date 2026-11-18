// Writes site/data/berlin_imagery.json: Berlin's aerial photo series from the OSM
// Editor Layer Index (via @osm-editor-kit/maplibre-editor-layer-index), one entry
// per capture with a ready-made MapLibre raster source.
//
// The page picks, for each snapshot, the newest photo taken on or before it, so the
// background shows what mappers could have seen at that time. Generated at build time
// rather than loaded at runtime: the series changes about once a year, and the page
// then needs no extra JavaScript or index download.
//
//     cd scripts/imagery && npm ci && npm run build

import { readFile, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import {
  getRasterSourceSpec,
  groupLayers,
  loadLayersInViewport,
} from '@osm-editor-kit/maplibre-editor-layer-index'

const BERLIN = [13.088, 52.338, 13.761, 52.675]
const SERIES = 'Berlin/Geoportal DOP20RGB'
const OUT = fileURLToPath(new URL('../../site/data/berlin_imagery.json', import.meta.url))

// ELI dates are YYYY, YYYY-MM or YYYY-MM-DD. The page compares full dates, so a year
// or month stands for its first day.
function isoStart(value) {
  if (!value) return null
  const [y, m = '01', d = '01'] = value.split('-')
  return `${y}-${m.padStart(2, '0')}-${d.padStart(2, '0')}`
}

const layers = await loadLayersInViewport(BERLIN, {
  // Older captures are filed as historic photos, newer ones as photos.
  categories: ['photo', 'historicphoto'],
  countryCodes: ['DE'],
  includeWorldwide: false,
})
const series = groupLayers(layers).find((entry) => entry.grouped && entry.name === SERIES)
if (!series) throw new Error(`series "${SERIES}" not in the index any more`)

const pkg = JSON.parse(await readFile(
  fileURLToPath(new URL('./node_modules/@osm-editor-kit/maplibre-editor-layer-index/package.json', import.meta.url)),
  'utf8',
))

const variants = series.variants
  .map((v) => ({
    id: v.id,
    label: v.group.label,
    order: v.group.order,
    captured: v.startDate === v.endDate || !v.endDate ? v.startDate : `${v.startDate} – ${v.endDate}`,
    start: isoStart(v.startDate),
    attribution: v.attributionText ?? v.attributionHtml ?? '',
    license: v.licenseUrl ?? null,
    source: getRasterSourceSpec(v, { pixelRatio: 1 }),
  }))
  .sort((a, b) => a.order - b.order)

await writeFile(OUT, JSON.stringify({
  series: SERIES,
  source: `Editor Layer Index via ${pkg.name}@${pkg.version}`,
  variants,
}, null, 1) + '\n')
console.log(`${OUT}: ${variants.length} captures, ${variants[0].label} … ${variants.at(-1).label}`)
