# Local POI Planner

Find nearby places with category, distance, seating, transit, and avoid constraints.
The planner combines Amap/OSM POIs with optional web evidence and reports uncertainty.
Requires Python 3.10+ and Bash; the planner itself uses only the Python standard library.

## Installation

```bash
clawhub install local-poi-planner
clawhub install unified-search-suite
```

For GitHub checkouts, clone both repositories alongside each other:

```bash
git clone https://github.com/ccmxigua/local-poi-planner.git
git clone https://github.com/ccmxigua/unified-search-suite.git
```

Follow the search repository's setup instructions for its Python dependencies and
search-provider credentials. The planner discovers a sibling `unified-search` or
`unified-search-suite` directory. For another layout, set `LOCAL_POI_UNIFIED_SEARCH`
to the full path of `scripts/unified-search.sh`; an invalid explicit path is reported
as a missing dependency instead of silently selecting a different installation.

Set `AMAP_KEY` in the environment or a private `.env.local` in this repository for
Amap POIs, geocoding, coordinate conversion, and routes. OSM fallback does not need
a key. Never commit credentials.

## Run

```bash
python3 scripts/planner.py --origin "天津财经大学" --category cafe \
  --location-policy disabled --timeout 300 --format json

# Quick POI lookup without the unified-search dependency or web enrichment:
python3 scripts/quick_search.py --origin "天津财经大学" --category cafe \
  --location-policy disabled --timeout 60 --format markdown
```

`quick_search.py` accepts the same arguments as the planner and defaults to
`--poi-only --mode search`. POI-only mode still uses map/location/route services;
it skips unified-search and specialty web fallback. JSON stdout contains one document;
progress is written to stderr. Inspect `status` for empty, partial, or failed results.

Numeric origins use **latitude,longitude** and default to WGS84. Coordinates copied
from Amap require `--coordinate-system gcj02`. Automatic location providers attach
their own coordinate system. WGS84 points are converted through Amap's supported
API before Amap lookup/routing; a failed conversion never relabels the input.
Named OSM fallbacks geocode independently in WGS84. A GCJ-02 numeric origin cannot
fall back to OSM without a WGS84 origin (`wgs84_origin_required`). Supply a place
name or WGS84 coordinates in that case.

Geocode overrides must include `"coordinate_system": "wgs84"` or `"gcj02"`.
Legacy overrides without a known datum are bypassed in favor of geocoding the name.
OSM geometry stays WGS84 even when Amap is used for route enrichment.

The coordinate contracts follow the [Amap conversion API](https://lbs.amap.com/api/webservice/guide/api/convert)
and [OSM coordinate reference](https://wiki.openstreetmap.org/wiki/GIS_FAQ).

## Tests

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q scripts tests
```

Tests use mocked providers and temporary fixtures; they require no network, API keys,
or device location access. GitHub Actions runs them on Linux (Python 3.10 and 3.14)
and macOS (Python 3.14). Live provider checks are separate and require configured credentials.

See [SKILL.md](./SKILL.md) for full documentation.

## License

MIT
