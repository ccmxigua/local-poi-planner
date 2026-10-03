# Validation — 2026-10-03

Changes were developed from `379111e` and checked in the actual planner checkout.

## Offline checks

- `python3 -m unittest discover -s tests -v`: 27 tests passed on Python 3.14.7.
- `python3 -m compileall -q scripts tests`: passed.
- Python 3.10 syntax compatibility and `git diff --check`: passed.
- Initial regressions reproduced quick-search's `None` crash, zero web-verification
  worker failure, invalid CoreLocation coordinates, and Amap coordinates reaching
  Overpass before the fixes.
- Tests cover subprocess JSON/error/deadline contracts, dependency discovery,
  POI-only behavior, coordinate conversion/fallback/routing, and partial enrichment
  ranking. A temporary Bash fixture exercises a real subprocess with spaces in its path.

## Live checks

All queries used public landmarks or explicit public coordinates. Automatic device
and IP location were disabled. Existing credentials were injected into the test
process only; no credentials or raw service logs are stored in this repository.

| Path | Result |
| --- | --- |
| `providers.amap_coordinates` → Amap conversion API | Public WGS84 point `(39.9, 116.4)` converted successfully; approximately 555 m coordinate displacement |
| `providers.search_amap_poi_by_coords` | 3 POIs; converted GCJ-02 origin and candidates |
| `providers.search_overpass` near central London | 3 POIs; WGS84 origin and candidates |
| `quick_search.py --origin 天津财经大学 --category cafe --location-policy disabled --timeout 120 --format json` | 15 POIs, `overall=success`, `web_search=not_run`, approximately 5.5 seconds |
| `planner.py` with the same origin/category and `--timeout 300` | 15 POIs; default search dependency discovered without a path adapter; `overall=partial` because Exa/Grok/TinyFish reported source errors |
| `planner.py` with a temporary Tavily-only search adapter | 15 POIs, 5 successful candidate web checks, 2 general web queries; both components and overall `success`, approximately 23.6 seconds |

The full runs invoked the real sibling `unified-search-suite/scripts/unified-search.sh`.
The second run set `LOCAL_POI_UNIFIED_SEARCH` to a temporary adapter executing that
entrypoint with `search-layer --mode deep --source tavily --num 5 -- "$1"`.
This selected a functioning provider; it did not repair or alter the search dependency.
Default multi-provider health remains an external limitation, and the planner reports
it as partial instead of claiming complete success.

Successful execution is not proof of store-level recommendation quality: indexed
web evidence can still be weak or lack an exact store-name match. Unverified
candidates retain their evidence labels.

## Coordinate compatibility

- Numeric CLI origins now explicitly default to WGS84; Amap coordinates require
  `--coordinate-system gcj02`.
- GCJ-02 input is never sent unchanged to OSM. Named fallbacks geocode independently;
  numeric GCJ-02 fallbacks require a WGS84 origin.
- Overrides without a declared datum are re-geocoded, not assigned a guessed datum.
- Amap route requests convert WGS84 endpoints; returned OSM geometry remains WGS84.

References: [Amap conversion contract](https://lbs.amap.com/api/webservice/guide/api/convert),
[OSM WGS84 reference](https://wiki.openstreetmap.org/wiki/GIS_FAQ),
[Apple CoreLocation coordinate reference](https://developer.apple.com/documentation/corelocation/cllocationcoordinate2d).
