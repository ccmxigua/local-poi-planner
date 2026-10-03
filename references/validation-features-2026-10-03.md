# Functional validation — 2026-10-03

Baseline: `f39775bc97f30ffc12c069d1a80ed82ad83a0266`.
The functional change adds request rules, budget/schedule checks, bounded recall,
and evidence-based explanations to the existing skill and Python entrypoints.

## Offline acceptance

Run from the repository root:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q scripts tests
git diff --check
```

50 tests passed locally on Python 3.10.18 and 3.14.7. These include the previous 27 regressions
and 23 functional cases covering natural Chinese requests, explicit flags,
negation and hard/soft distinctions, invalid inputs before location access,
budget boundaries and missing prices, continuous/split/overnight hours, missing
future-day schedules, unknown hard attributes, preference ranking, deduplication,
pagination, v3 fallback, partial errors, and CLI/rendered decisions.

Two independent read-only reviews identified unparsed half-hour stays and lost
failure state on a failed synonym search. Both were repaired and regression-tested.
A shop-name regression also prevents “一点点” from becoming a 01:00 arrival time.
The first CI run exposed Python 3.10's lack of `fromisoformat` support for the UTC
suffix `Z`. Arrival and observation timestamps now normalize that suffix to
`+00:00`; the full suite was rerun with an actual local Python 3.10 interpreter.

## Real provider acceptance

Actual entrypoints: `scripts/quick_search.py` and `scripts/planner.py` in this
repository. All runs used the public origin 天津财经大学, category `cafe`, fixed
3,000 m radius, and `--location-policy disabled`. No device/IP location was used.
Credentials were loaded into the validation process from an existing private
installation; no key or environment file is included in this change.

| Scenario | Entry | Observed result |
| --- | --- | --- |
| `--budget-max 100` | quick_search.py | POI success, 45-POI recall pool, 15 eligible in the returned shortlist; all 15 had reference costs |
| `--budget-max 100 --visit-at 2026-10-03T19:00:00 --stay-minutes 90` | quick_search.py | POI success, 45-POI pool, 9 eligible and 6 pending; the 9 eligible had supported current-day hours |
| `--must seating` | quick_search.py | POI success, 45-POI pool, 0 eligible and 15 pending; missing seat attributes were not asserted as facts |
| `--budget-max 100 --mode recommend` | planner.py | POI and web success, 25-POI pool, 8 eligible; `top_pick: null` because web evidence did not reach the store-level threshold |

The map source was **Amap v5** in all four runs. Real responses included
`business.cost`, `opentime_today`, `opentime_week`, `rating`, and sometimes `tag`.
Example: a returned reference price of 13 CNY and hours `06:00-23:00` passed the
19:00 arrival / 90-minute-stay check. This verifies schedule handling, not live
opening or seat availability.

The full run used a temporary external adapter selecting the existing sibling
`unified-search-suite` entrypoint with:

```bash
bash /path/to/unified-search-suite/scripts/unified-search.sh search-layer \
  --mode deep --source tavily --num 5 -- "$1"
```

`LOCAL_POI_UNIFIED_SEARCH` pointed to that adapter only for the live full-flow test.
The adapter is not a repository dependency or default configuration change. This
run validates **Tavily**, not the default Exa/Tavily/Grok/Tinyfish aggregate. The
three quick runs reported web `not_run` as expected.

Assertions checked that eligible candidates met every hard check, valid reference
costs were within the requested budget, pending/excluded partitions matched their
states, radius stayed fixed, real cost/hour fields arrived, and no automatic
location was used. Raw local outputs are in the task's temporary validation folder;
only this credential-free summary is committed.

## Remaining boundaries

- Reference cost and fetch timestamps do not establish a current quote or fresh
  merchant verification. Provider “today” hours may be stale; future-day and complex
  schedules stay unknown.
- Seating, quietness, mall membership and station distance may be unavailable.
  A request can execute successfully while producing only pending candidates.
- Recall is bounded, not an exhaustive list of businesses. Candidate counts refer
  to the returned shortlist, not the entire recall pool.
- This GitHub update does not install the skill into another local runtime or
  publish a new ClawHub package.
