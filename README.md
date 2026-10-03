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

## Conditions that affect the result

```bash
python3 scripts/quick_search.py --origin "天津财经大学" --category cafe \
  --budget-max 100 --visit-at "2026-10-03T19:00:00" --stay-minutes 90 \
  --radius-m 3000 --preferences quiet --location-policy disabled --format markdown

python3 scripts/planner.py \
  --query "今晚七点，从天津财经大学出发，找咖啡，人均一百以内，必须有座位，优先清静，三公里以内，待一个半小时" \
  --location-policy disabled --format json
```

- `--must` / `--constraints` are hard requirements; `--preferences` changes ranking.
  Known aliases include `seating`, `metro`, `near_metro`, `mall`, `quiet`, and
  `environment`. Explicit failures are excluded. Missing evidence stays pending,
  even if a web result mentions the shop. `metro` checks the returned route for
  a subway segment; `near_metro` requires station-distance evidence within 500 m.
- `--budget-max` is a positive **CNY reference cost per person**. Missing or zero
  cost is unknown; a map reference price is not a current quote.
- `--visit-at` accepts an ISO date/time. Naive values use `--timezone`
  (default `Asia/Shanghai`). `--stay-minutes` checks a continuous stay, including
  closing boundaries and midnight. Amap's *today* hours only establish the fetched
  day's schedule; future dates and complex weekly hours remain unknown. Use the
  destination timezone outside China. These are schedule checks, not live status.
- Natural requests support today/tomorrow evening, Chinese numbers, “半小时” and
  “一个半小时”. Unsupported time/budget expressions produce warnings and pending
  checks; explicit flags are the reliable interface for precise requests.
- Recall uses a fixed radius (1–50,000 m), bounded Amap pagination and category
  synonym searches when the pool is short. It deduplicates and ranks the collected
  pool before selecting a shortlist; it never silently expands the radius.
  Amap v5 business fields are retained, with v3 fallback when v5 is unavailable.

Execution success and finding a suitable place are separate:
`result.planning_status` is `matched`, `pending_verification`, `no_match`, or
`no_candidates`. `results` / `poi_candidates` contain eligible stores;
`pending_verification` and `excluded_candidates` explain the rest. A recommendation
has `top_pick: null` until an eligible store also has sufficient store-level web
evidence. Each candidate includes `requirement_checks`, `matched_preferences`,
`recommendation_reasons`, and source/fetch timestamps. `ranking_score` adds up to
24 preference points to the base/web/route score; it is not a probability.

The recall pool is capped at 75 unique POIs (currently 45 for search, 25 for
recommendation), up to three pages per term. Route enrichment is limited to twice
the output shortlist. `candidate_pool_size` describes recall; `candidate_counts`
describes the returned shortlist, not every business in the area. Partial page or
synonym failures retain usable candidates and report `partial`.

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
