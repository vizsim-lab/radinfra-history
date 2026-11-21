"""The regions the pipeline builds. Each has a folder of its own in data/, in
site/data/ and in the bucket, named by its slug.

    python -m pipeline.regions extract berlin      # URL of the update extract
"""

import sys
from dataclasses import dataclass

GEOFABRIK = "https://download.geofabrik.de/"


@dataclass(frozen=True)
class Region:
    slug: str
    name: str           # in the timeline
    relation: int       # the region's own boundary in OSM
    extract: str        # Geofabrik's public extract for the quarterly updates
    # The area the snapshots cover, for builds from before the timeline kept it
    # (`coverage`, from the history file's header): an update extract must cover it.
    coverage: tuple = None

    @property
    def extract_url(self):
        return GEOFABRIK + self.extract


REGIONS = {r.slug: r for r in (
    Region("berlin", "Berlin", 62422, "europe/germany/berlin/berlin-latest.osm.pbf",
           coverage=(13.08283, 52.33446, 13.762245, 52.6783)),
    Region("germany", "Deutschland", 51477, "europe/germany/germany-latest.osm.pbf"),
    # Small, for trying things out: 6 Kreise, 52 Gemeinden.
    Region("saarland", "Saarland", 62372, "europe/germany/saarland/saarland-latest.osm.pbf"),
)}


def get(slug):
    try:
        return REGIONS[slug.lower()]
    except KeyError:
        raise SystemExit(f"unknown region {slug!r}: one of {', '.join(REGIONS)} (pipeline/regions.py)")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "extract":
        raise SystemExit(__doc__)
    print(get(sys.argv[2]).extract_url)
