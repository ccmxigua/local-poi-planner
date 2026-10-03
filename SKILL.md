---
name: local-poi-planner
description: "Plan nearby places for dining, desserts, cafes, malls, and date spots using one local POI planning workflow. Best for queries like “附近有什么适合坐着聊天的咖啡店”, “帮我找地铁可达的甜品店”, or “规划一个适合约会的商场/餐厅”. Uses structured POI recall (Amap-first with OSM fallback), budget and opening-time checks, hard requirements, preference ranking, and unified-search evidence; missing evidence remains pending."
metadata:
  openclaw:
    requires:
      bins: ["python3", "bash"]
    optionalBins: ["jq"]
files:
  read:
    - references/: Design notes, output template, anchor strategy
    - config/anchors.json: Optional anchor overrides for common places
    - config/geocode_overrides.json: Optional coordinate overrides for ambiguous place names
    - scripts/: Planner implementation
    - unified-search/ or unified-search-suite/: Sibling web-search dependency → https://clawhub.ai/ccmxigua/unified-search-suite; alternatively set LOCAL_POI_UNIFIED_SEARCH to the full unified-search.sh path
  write:
    - /tmp/local-poi-planner-*.json: Intermediate planner outputs
    - /tmp/local-poi-planner-*.md: Final rendered reports
---

# Local POI Planner

Use this skill when the user wants a **specific nearby place recommendation** with constraints like:

- start point / origin
- metro access
- seating / environment quality
- mall preference
- budget / category / vibe
- avoid rules (night market, takeaway-only, no seating)

This skill is a **single top-level skill**. Internally it may call the existing `unified-search` skill script as a supporting evidence source.

## What this skill does

1. Parse the request into:
   - origin (see Location Resolution below)
   - category (see Category Resolution below)
   - preferences
   - hard constraints (`--must` / `--constraints`) versus soft preferences
   - per-person CNY reference budget, arrival time/timezone, continuous stay
   - avoid rules
2. Recall nearby candidates from a structured POI source (Amap-first with OSM fallback), with bounded pagination and category synonym searches at the requested radius; deduplicate and rank the pool before shortlisting.
3. Expand the origin into nearby anchors (mall / road / station / area)
4. Generate multiple local search queries
5. Run `unified-search` over those queries and top POI candidates
6. Score evidence via auto-derived category hints (from CATEGORY_ALIASES)
7. **Quality gate**: assess bundle quality (poor/weak/acceptable); if evidence is weak, trigger specialty fallback
8. **Specialty fallback**: for cinema queries with a recognized city in the origin, supplement with Maoyan city-level hall search (IMAX/4DX/CINITY/Dolby/LUXE/CGS); do not substitute an unrelated default city.
9. Return:
   - top pick
   - backups
   - not recommended / weak-evidence notes
   - pending and excluded candidates with the evidence for each condition
   - optional area clues if store-level evidence is weak; never present an area as a store satisfying hard conditions

### Category Resolution

- `--category` explicitly set by caller → used directly (for known categories like `cafe`, `dessert`)
- `infer_category(query)` matches a known category via regex rules → used
- Neither → `_extract_fallback_keywords(query)` strips noise words (我/附近/的/找/...) → raw keywords passed directly to Amap as pure keyword search (no typecode, no category normalization)
- No Grok/LLM involved — the agent should NOT translate queries to categories (e.g. don't convert "修理书包" to "皮具护理"). Just pass `--query` and let the fallback pipeline handle keyword extraction. Amap supports free-text keyword search without a category/typecode.

### Location Resolution

- `--origin` explicitly set: used directly
- Numeric origins are `latitude,longitude`, defaulting to WGS84; pass `--coordinate-system gcj02` for Amap coordinates. Device/IP providers declare their own coordinate system.
- `--corelocation` flag: forces CoreLocationCLI (WiFi-based, ~500m accuracy) to get current coordinates
- `--location-policy auto` (default) + query contains nearby hints (`附近`/`周边`/`方圆`): tries CoreLocationCLI → IP geolocation fallback
- `--location-policy corelocation` or `ip`: use only that automatic location source
- `--location-policy disabled`: do not resolve location; nearby requests require `--origin`
- `--timeout` sets the overall request budget (default 1800 seconds); map, geocoding, location, routing, and unified-search calls are capped by the remaining time.
- None of the above: "未指定起点"

Automatic IP location queries public IP lookup services and Amap. POI lookups send the
resolved coordinates or place name to the selected map provider; web enrichment sends
search text to configured search providers. Use `--location-policy disabled` with an
explicit `--origin` when you do not want automatic device/IP location lookup.

Amap requests use GCJ-02. WGS84 input is converted via Amap's coordinate conversion
API before POI lookup or routing. OSM fallback geocodes names separately in WGS84;
GCJ-02 numeric input returns `wgs84_origin_required` if Amap fails, rather than
querying OSM around an incorrect point. Supply a place name or WGS84 origin to use
OSM in that case. `config/geocode_overrides.json` entries are used only when they
declare a supported `coordinate_system`; untagged legacy entries are re-geocoded.

## Quick Start

### Natural language mode
```bash
python3 scripts/planner.py \
  --query "福州大学旗山校区北门附近，地铁可达，适合坐着吃甜品，偏酸奶/gelato" \
  --format markdown
```

### Explicit mode
```bash
python3 scripts/planner.py \
  --origin "福州大学旗山校区北门" \
  --category dessert \
  --preferences "yogurt,gelato,smoothie" \
  --constraints "metro,seating,environment,mall" \
  --avoid "night_market,takeaway_only,no_seating" \
  --format markdown
```

### CoreLocation mode (auto-detect current position)
```bash
python3 scripts/planner.py \
  --query "附近有什么电影院支持4DX" \
  --corelocation \
  --format markdown
```

## Output contract

### Request and evidence rules

- `--must` and `--constraints` are comma-separated hard requirements. `--preferences`
  affects ranking using matched store names/structured attributes. Natural “必须”
  is hard, “优先/最好/尽量” is soft; subjective quiet/environment defaults to soft.
- `--budget-max` means maximum reference CNY cost per person. Missing/zero cost is
  unknown, not free. Keep “reference price, not a quote” visible in the answer.
- `--visit-at` is an ISO arrival date/time, with `--timezone Asia/Shanghai` by
  default for naive input. Use the destination's IANA timezone outside China.
  `--stay-minutes` (0–1440) checks continuous coverage. Natural “今晚七点”,
  “明天下午三点半”, “半小时”, and “一个半小时” are supported; unresolved date,
  budget, or duration expressions remain pending and appear in `request_warnings`.
- Amap v5 `business.cost` and `opentime_today` are preserved with a source and fetch
  timestamp. If v5 fails, v3 business data may be more limited. “Today” is only
  usable for the fetch date in the selected timezone; complex schedules and future
  dates remain unknown. A supported schedule is not confirmation of live opening.
- Seat availability, quietness, mall membership, and station proximity are often
  missing from provider data. Keep them unknown. Generic web snippets and shop-name
  matches do not confirm those hard requirements. `metro` checks the returned
  route for subway use, while `near_metro` needs station-distance data (≤500 m).
- `--radius-m` (1–50000) overrides natural radius parsing. Recall never expands it.
  Up to three pages per term and at most two extra category terms are supported;
  the unique pool is capped at 75 (45 in search / 25 in recommend currently).
  All collected POIs are ranked before truncation; route enrichment is bounded to
  twice the shortlist. A failed page/term reports partial recall.

`--poi-only` skips web enrichment and specialty web fallback and reports web status
`not_run`. `scripts/quick_search.py` is a convenience entrypoint with this option
and `--mode search` enabled by default; all ordinary planner flags remain available.
It still calls map services and any location source allowed by `--location-policy`.

The planner returns a store-level recommendation only when the store meets every
hard condition and has sufficient store-level web evidence. Otherwise `top_pick`
is `null`, with eligible candidates, pending checks, exclusions, and optional area
clues available separately. Never turn a pending candidate into a confirmed pick.

In JSON mode, the top-level payload contains `status`, `request`, `poi_provider`, `queries`, and `result`:
- `status.overall`: `success`, `empty`, `partial`, or `error`; `status.components.poi` and `status.components.web_search` summarize each stage.
- `request`: normalized query, origin, category, radius, preferences, constraints, avoid rules, `budget_max`, `visit_at`, `stay_minutes`, `timezone`, and parsing warnings. A source IP from IP geolocation is omitted.
- `poi_provider`: structured POI provider, resolved origin, candidates, errors, and `fallback_from` when Amap falls back to Overpass.
- `queries`: web queries attempted. `result` keeps the existing mode-specific recommendation/search fields and per-candidate evidence, accessibility, and constraint assessments.
- `result.planning_status`: `matched`, `pending_verification`, `no_match`, or `no_candidates`; this is separate from execution `status`.
- `result.results` / `result.poi_candidates`: eligible stores only. `pending_verification` holds missing-evidence candidates; `excluded_candidates` holds known failures. `candidate_counts` describes this bounded shortlist, while `poi_provider.candidate_pool_size` describes recall.
- Per candidate: `requirement_checks` has `met` / `not_met` / `unknown` states plus reason, source and fetch timestamp; `matched_preferences`, `preference_score`, `ranking_score`, and `recommendation_reasons` explain ordering. The preference bonus is capped at 24; scores are not probabilities.

Treat provider and candidate fields as additive: consumers should ignore unknown fields and use the explicit component status to distinguish an empty result from a failed or partial lookup.

Confidence levels:
- `high`: repeated store-level hits with supporting evidence
- `medium`: usable candidates but some ambiguity
- `low`: limited web evidence or unresolved requirements; no confirmed store pick

## Internal dependency

This skill uses the unified-search entrypoint here:
```bash
bash <unified-search>/scripts/unified-search.sh "<query>"
```

**Important — which path actually runs (verified 2026-05-31):**
- The entrypoint is the new `unified-search.sh`, **not** `unified-search-legacy.sh`.
- For an ordinary query with no `--legacy`/`--mode` flag, the script routes to
  `run_search_layer_auto` → deep **search-layer** with `--source exa,tavily,grok,tinyfish`.
  So Grok + TinyFish are part of the real path; the legacy Tavily+Exa+Google merge is **not** used here.
- Ordinary query calls automatically route to deep search. Do not append legacy
  `--num`/`--topic` flags; their values can be incorporated into the query text.
- When debugging latency/hangs, profile `run-search-layer.sh` → `search.py` (deep search-layer),
  **not** `unified-search-legacy.sh`. The legacy script is a different code path.

## Lessons / Pitfalls

See `references/lessons-2026-05-31.md` for hard-won debugging notes:
- Profiling the **wrong** unified-search path (legacy vs deep) wastes hours — always confirm the actual entry script first.
- Only Grok is usable for local Chinese queries; Tavily/TinyFish pollute ~87% of results; Exa often 402s.
- Grok needs `proxies={http:None,https:None}` (Surge proxy hangs `enable_search`) and a 1800s timeout for Chinese multi-agent queries.
- Transit detail rendering rules: same-segment buslines are **alternatives** (not sequential transfers); `via_stops` carries stop names; walking has full turn-by-turn; no `max_parts` truncation.
- Accessibility scoring/thresholds and the "shops are just geographically far from metro" reality.

## Rules

- **Planner output contract**: `--format json` writes one JSON document to stdout; progress
  and provider diagnostics go to stderr. The JSON payload includes a top-level `status`
  object with `overall` (`success`, `empty`, `partial`, or `error`) and component states
  under `components.poi` / `components.web_search`. Failed web runs do not contribute
  positive evidence scores. The planner `--timeout` is a total request budget (default
  1800 seconds); each provider and unified-search subprocess receives no more than the
  remaining time. When invoking via exec, set the outer process timeout slightly above
  the configured planner budget so it has time to serialize the final result. Shorter
  runs are available by explicitly setting `--timeout`. In JSON mode, monitor stderr for
  progress; stdout is reserved for the final document.

- Prefer one final recommendation workflow, not fragmented sub-skills
- If exact local store evidence is weak, **do not hallucinate shop names**
- Area / mall / anchor clues must stay separate from store picks and cannot bypass hard requirements
- Use concise decision output:
  - 首选
  - 备选
  - 不推荐 / 风险点
  - 下一步
- **Web search: ALL external web enrichment MUST go through `unified-search.sh`.**
  Do NOT DIY via DuckDuckGo Lite, `curl` to Baidu/Sogou/360, `requests` scraping, or ad-hoc
  HTTP calls. These are unreliable (empty results, anti-bot blocking, wrong encoding) and
  waste time. Only `unified-search.sh` (especially with Grok) works for Chinese local queries.
  The Tavily research script is a secondary option for deep-dive topics, but for POI
  enrichment always prefer unified-search.
- **For cinema/venue features that POI APIs don't expose (e.g. 4D/IMAX/Dolby screen types)**,
  the skill may supplement with structured venue APIs like Maoyan (`m.maoyan.com` city-level
  cinema list). See `references/lessons-2026-06-01.md` for Maoyan API notes.

- **Quality gate**: After unified-search completes, `_assess_bundle_quality` evaluates
  each candidate bundle (max_score ≤2 → poor, avg_score <10 → weak, else acceptable).
  If `need_fallback=True`, the pipeline automatically triggers `_specialty_fallback_run`
  (currently only cinema → Maoyan API) and inserts the fallback bundle at the front of
  the run list.

- **CATEGORY_EVIDENCE_HINTS auto-derivation**: `_get_evidence_hints(category)` first checks
  the curated `CATEGORY_EVIDENCE_HINTS` dict (dessert/cafe/tea/bakery/restaurant/电影院);
  if not found, it auto-generates hints from `CATEGORY_ALIASES` by collecting all aliases
  that map to the same canonical category. This means adding a new category only requires
  updating `CATEGORY_ALIASES` — no separate hints dict needed.

- **CoreLocation**: On macOS, the skill can auto-resolve current position via
  `CoreLocationCLI` (WiFi-based, ~500m accuracy). Requires `brew install corelocationcli`.
  Use `--corelocation` flag to force this, or rely on auto-detection when origin is
  unspecified and query contains nearby hints (`附近`/`周边`/`方圆`). CoreLocationCLI may
  trigger a macOS location permission popup on first use.
  `--location-policy` supports `auto` (default), `corelocation`, `ip`, and `disabled`.
  When lookup is disabled and no origin is supplied, the planner returns `origin_required`
  and skips external web search. See `scripts/macos_location.py` for the implementation.

## Suggested trigger examples

- “帮我找附近适合约会的甜品店”
- “从 XX 出发，找地铁可达、能坐着聊天的咖啡馆”
- “附近有没有适合带女朋友去的 mall 里甜品店”
- “规划一个商场内、清爽口味优先的甜品点”
