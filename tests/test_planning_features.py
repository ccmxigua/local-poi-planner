"""Functional acceptance: request -> recall -> evidence checks -> decision."""
import contextlib
from datetime import datetime
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import amap_poi
import planner
import planning_rules as rules
import providers

NOW = datetime.fromisoformat("2026-10-03T12:00:00+08:00")


def request(**kwargs):
    value = dict(origin="天津财经大学", category="cafe", preferences=[], constraints=[], avoid=[],
                 radius_m=3000, timezone="Asia/Shanghai")
    value.update(kwargs)
    return value


def candidate(name="Cafe", **kwargs):
    value = dict(name=name, distance_m=100, score=40, tags={}, provider="fixture",
                 observed_at=NOW.isoformat())
    value.update(kwargs)
    return value


def raw_poi(index, **kwargs):
    value = dict(id=f"id-{index}", name=f"Cafe {index}", location=f"117.{index:04d},39.01", distance=str(index))
    value.update(kwargs)
    return value


class PlanningFeatures(unittest.TestCase):
    def setUp(self):
        providers.set_request_deadline(None)
        self.addCleanup(providers.set_request_deadline, None)
        network = patch("urllib.request.urlopen", side_effect=AssertionError("unexpected network call"))
        network.start()
        self.addCleanup(network.stop)

    def test_natural_request_separates_requirements_and_preferences(self):
        query = "今晚七点，从天津财经大学出发，找咖啡，两个人，人均一百以内，必须有座位，优先清静，三公里以内，待两个小时"
        parsed = rules.parse_requirements(SimpleNamespace(), query, now=NOW)
        self.assertEqual(parsed["constraints"], ["seating"])
        self.assertEqual(parsed["preferences"], ["quiet"])
        self.assertEqual(parsed["budget_max"], 100)
        self.assertEqual(parsed["stay_minutes"], 120)
        self.assertEqual(parsed["visit_at"], "2026-10-03T19:00:00+08:00")
        args = SimpleNamespace(query=query, origin=None, category=None, preferences=None, constraints=None, avoid=None, location_policy="disabled")
        req = planner.parse_request(args)
        self.assertEqual(req["origin"], "天津财经大学")
        self.assertEqual(req["category"], "cafe")
        self.assertEqual(req["radius_m"], 3000)

    def test_negations_and_explicit_must(self):
        parsed = rules.parse_requirements(SimpleNamespace(must="seating", preferences="有座位,quiet"), "不需要地铁可达，不要求安静，无座位也可以", now=NOW)
        self.assertEqual(parsed["constraints"], ["seating"])
        self.assertEqual(parsed["preferences"], ["quiet"])
        self.assertEqual(rules.parse_requirements(SimpleNamespace(), "安静，环境好", now=NOW)["constraints"], [])
        parsed = rules.parse_requirements(SimpleNamespace(), "必须有座位而且优先安静", now=NOW)
        self.assertEqual(parsed["constraints"], ["seating"])
        self.assertEqual(parsed["preferences"], ["quiet"])
        parsed = rules.parse_requirements(SimpleNamespace(), "不需要安静但必须有座位", now=NOW)
        self.assertEqual(parsed["constraints"], ["seating"])

    def test_radius_units_and_explicit_override(self):
        for text, expected in (("2km以内", 2000), ("五百米", 500), ("1.5公里", 1500), ("三千米以内", 3000)):
            self.assertEqual(rules.parse_radius(text), expected)
        args = SimpleNamespace(query="附近咖啡三公里以内", origin="天津", category=None, preferences=None,
                               constraints="500m,seating", avoid=None, radius_m=800, location_policy="disabled")
        req = planner.parse_request(args)
        self.assertEqual(req["radius_m"], 800)
        self.assertEqual(req["constraints"], ["seating"])

    def test_unresolved_time_or_budget_blocks_confirmation(self):
        for query, key in (("周末七点去咖啡馆", "open_at"), ("明晚去，待两小时", "open_at"), ("人均100到200元", "budget"), ("人均100以上", "budget"), ("人均一百美元", "budget")):
            with self.subTest(query=query):
                parsed = rules.parse_requirements(SimpleNamespace(), query, now=NOW)
                self.assertTrue(parsed["request_warnings"])
                self.assertEqual(rules.assess_candidate(parsed, candidate())["requirement_checks"][key]["state"], "unknown")

    def test_explicit_time_and_timezone(self):
        parsed = rules.parse_requirements(SimpleNamespace(visit_at="2026-10-03T11:00:00Z"), "", now=NOW)
        self.assertEqual(parsed["visit_at"], "2026-10-03T19:00:00+08:00")
        self.assertEqual(rules.parse_requirements(SimpleNamespace(), "明天下午三点半", now=NOW)["visit_at"], "2026-10-04T15:30:00+08:00")

    def test_shop_name_does_not_invent_arrival_time(self):
        for query in ("天津财经大学附近的一点点奶茶", "一点点奶茶", "帮我找一点点"):
            parsed = rules.parse_requirements(SimpleNamespace(), query, now=NOW)
            self.assertIsNone(parsed["visit_at"])
            self.assertFalse(parsed["time_unresolved"])

    def test_half_hour_stay_and_unresolved_duration(self):
        for text, minutes in (("半小时", 30), ("一个半小时", 90), ("两个小时", 120)):
            req = rules.parse_requirements(SimpleNamespace(), f"今晚七点去咖啡馆，坐{text}", now=NOW)
            self.assertEqual(req["stay_minutes"], minutes)
            self.assertEqual(rules.opening_check(req, candidate(business={"opentime_today": "09:00-19:10"}))[0], "not_met")
        req = rules.parse_requirements(SimpleNamespace(), "今晚七点去，坐几个小时", now=NOW)
        self.assertEqual(rules.assess_candidate(req, candidate(business={"opentime_today": "24小时"}))["eligibility"], "needs_verification")

    def test_invalid_inputs_fail_before_location_or_search(self):
        for flags in (["--budget-max", "nan"], ["--budget-max", "0"], ["--stay-minutes", "-1"], ["--radius-m", "50001"], ["--visit-at", "bad"], ["--timezone", "Not/AZone"]):
            with self.subTest(flags=flags), patch.object(planner, "search_pois") as search, patch.object(planner, "get_macos_location") as locate, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                planner.main(["--corelocation"] + flags)
            search.assert_not_called()
            locate.assert_not_called()

    def test_budget_boundary_missing_zero_and_over(self):
        for cost, expected in ((100, "eligible"), ("99.5", "eligible"), (100.01, "excluded"), (None, "needs_verification"), (0, "needs_verification"), ("nan", "needs_verification")):
            with self.subTest(cost=cost):
                result = rules.assess_candidate(request(budget_max=100), candidate(business={"cost": cost}))
                self.assertEqual(result["eligibility"], expected)
                self.assertEqual(result["requirement_checks"]["budget"]["source"], "fixture")
                self.assertEqual(result["requirement_checks"]["budget"]["observed_at"], NOW.isoformat())

    def test_opening_continuous_stay_and_split_hours(self):
        req = request(visit_at="2026-10-03T11:30:00+08:00", stay_minutes=60)
        for hours, state in (("09:00-12:00,14:00-22:00", "not_met"), ("09:00-12:00;12:00-22:00", "met"), ("Mo-Fr 09:00-22:00", "unknown"), ("09:99-22:00", "unknown"), ("休息", "not_met")):
            with self.subTest(hours=hours):
                self.assertEqual(rules.opening_check(req, candidate(business={"opentime_today": hours}))[0], state)

    def test_opening_midnight_and_date_boundaries(self):
        cases = [
            ("2026-10-03T23:00:00+08:00", 120, {"business": {"opentime_today": "18:00-02:00"}}, "met"),
            ("2026-10-03T01:00:00+08:00", 30, {"business": {"opentime_today": "18:00-02:00"}}, "unknown"),
            ("2026-10-04T19:00:00+08:00", 30, {"business": {"opentime_today": "09:00-22:00"}}, "unknown"),
            ("2026-10-03T23:00:00+08:00", 120, {"business": {"opentime_today": "24小时"}}, "unknown"),
            ("2026-10-03T23:00:00+08:00", 1440, {"tags": {"opening_hours": "24/7"}}, "met"),
            ("2026-10-03T01:00:00+08:00", 30, {"tags": {"opening_hours": "18:00-02:00"}}, "met"),
            ("2026-10-03T22:00:00+08:00", 0, {"tags": {"opening_hours": "09:00-22:00"}}, "not_met"),
        ]
        for visit, stay, data, state in cases:
            with self.subTest(visit=visit, data=data):
                self.assertEqual(rules.opening_check(request(visit_at=visit, stay_minutes=stay), candidate(**data))[0], state)

    def test_unknown_hard_attributes_do_not_become_verified_from_web(self):
        poi = candidate(web_score=100, web_has_store_name=True, web_lines=["Cafe 有座位，安静"])
        for constraint in ("seating", "quiet", "near_metro"):
            result = planner.decide_recommend(request(constraints=[constraint]), {}, [], [poi])
            self.assertIsNone(result["top_pick"])
            self.assertEqual(result["planning_status"], "pending_verification")
            self.assertEqual(result["pending_verification"][0]["name"], "Cafe")

    def test_bus_only_route_does_not_disprove_subway_access(self):
        for subway, expected in ((True, "met"), (False, "unknown")):
            poi = candidate(accessibility={"has_subway": subway, "data_source": "amap_direction", "observed_at": NOW.isoformat()})
            check = rules.assess_candidate(request(constraints=["metro"]), poi)["requirement_checks"]["metro"]
            self.assertEqual(check["state"], expected)
            self.assertEqual(check["source"], "amap_direction")

    def test_known_failure_cannot_be_top_pick_or_backup(self):
        bad = candidate("Expensive", score=100, business={"cost": 200}, web_score=100, web_has_store_name=True)
        good = candidate("Affordable", business={"cost": 50}, web_score=60, web_has_store_name=True)
        unknown = candidate("Unknown", score=90, web_score=100, web_has_store_name=True)
        result = planner.decide_recommend(request(budget_max=100), {}, [], [bad, unknown, good])
        self.assertEqual(result["top_pick"], "Affordable")
        self.assertEqual(result["backups"], [])
        self.assertEqual(result["excluded_candidates"][0]["name"], "Expensive")
        self.assertEqual(result["pending_verification"][0]["name"], "Unknown")
        self.assertIn("参考人均", result["reason"])

    def test_preference_changes_order_without_accumulating_bonus(self):
        req = request(preferences=["quiet"])
        pois = [candidate("Near", score=45), candidate("Calm", business={"tag": "安静"})]
        ranked = rules.rank_candidates(req, pois)
        self.assertEqual(ranked[0]["name"], "Calm")
        self.assertEqual(ranked[0]["matched_preferences"], ["quiet"])
        self.assertEqual(rules.rank_candidates(req, ranked), ranked)
        self.assertEqual(rules.assess_candidate(req, candidate(tags={"quiet": "no"}, business={"tag": "安静"}))["matched_preferences"], [])

    def test_excluded_and_pending_render_reasons(self):
        req = request(mode="search", budget_max=100)
        result = planner.decide_search(req, {}, [], [candidate("Expensive", business={"cost": 200}), candidate("Unknown")])
        self.assertEqual(result["top_candidates"], [])
        self.assertEqual(result["results"], [])
        markdown = planner.render_markdown(req, result, {})
        for text in ("待确认候选", "已排除候选", "缺少有效人均", "预算上限"):
            self.assertIn(text, markdown)

    def test_pagination_deduplicates_and_preserves_business(self):
        page1 = [raw_poi(i) for i in range(25)]
        page2 = [raw_poi(24), raw_poi(25, business={"cost": "88", "opentime_today": "09:00-22:00"})]
        with patch.object(amap_poi, "_load_key", return_value="test"), patch.object(amap_poi, "_fetch_page", side_effect=[{"status": "1", "pois": page1}, {"status": "1", "pois": page2}]) as fetch:
            pois = amap_poi._search_with_typecode(39, 117, 800, keywords="咖啡", limit=50)
        self.assertEqual(len(pois), 26)
        self.assertEqual(pois[-1]["business"]["cost"], "88")
        self.assertEqual(pois[-1]["data_source"], "amap_poi_v5")
        self.assertEqual([call.args[0]["radius"] for call in fetch.call_args_list], [800, 800])
        self.assertEqual([call.args[0]["page_num"] for call in fetch.call_args_list], [1, 2])

    def test_later_page_failure_retains_data_as_partial(self):
        with patch.object(amap_poi, "_load_key", return_value="test"), patch.object(amap_poi, "_fetch_page", side_effect=[{"status": "1", "pois": [raw_poi(i) for i in range(25)]}, {}]):
            pois = amap_poi._search_with_typecode(39, 117, 800, limit=50)
        self.assertEqual(len(pois), 25)
        self.assertTrue(all(p["recall_partial"] for p in pois))
        self.assertEqual(planner._execution_status({"results": pois, "partial": True}, [])["overall"], "partial")

    def test_v5_failure_uses_v3_and_retains_available_fields(self):
        with patch.object(amap_poi, "_load_key", return_value="test"), patch.object(amap_poi, "_fetch_page", side_effect=[{}, {"status": "1", "pois": [raw_poi(1, biz_ext={"cost": "50"})]}]) as fetch:
            pois = amap_poi._search_with_typecode(39, 117, 800)
        self.assertEqual(fetch.call_args_list[-1].args[1], 3)
        self.assertEqual(fetch.call_args_list[-1].args[0]["extensions"], "all")
        self.assertEqual(pois[0]["business"]["cost"], "50")
        self.assertNotIn("opentime_today", pois[0]["business"])

    def test_synonym_failure_is_not_reported_as_full_success(self):
        first = amap_poi.SearchResults([dict(id="one", name="Cafe", lat=39, lon=117)])
        with patch.object(providers.amap_poi, "search_by_keywords", side_effect=[first, amap_poi.SearchResults(partial=True)]):
            result = providers.search_amap_poi_by_coords(request(), 39, 117, enable_accessibility=False)
        self.assertTrue(result["partial"])
        self.assertEqual(planner._execution_status(result, [])["overall"], "partial")
        with patch.object(amap_poi, "_load_key", return_value="test"), patch.object(amap_poi, "_fetch_page", return_value={}):
            empty = amap_poi._search_with_typecode(39, 117, 800)
        self.assertEqual(empty, [])
        self.assertTrue(empty.partial)

    def test_bounded_recall_deduplicates_synonyms_at_same_radius(self):
        poi = dict(id="id-1", name="Cafe", lat=39, lon=117)
        with patch.object(providers.amap_poi, "search_by_keywords", return_value=[poi]) as search:
            recalled = providers._recall_amap(request(), 39, 117, 500, 8)
        self.assertEqual(len(recalled), 1)
        self.assertEqual(search.call_count, 2)
        self.assertTrue(all(call.kwargs["radius"] == 500 for call in search.call_args_list))

    def test_amap_ranks_entire_pool_before_truncating(self):
        pois = [dict(id=str(i), name=f"Cafe {i}", lat=39.001, lon=117, distance=100, business={"cost": 200 if i < 24 else 50}) for i in range(25)]
        pois.append(dict(pois[-1]))
        result = providers._format_amap_pois(pois, request(budget_max=100), 39, 117, [], 1, enable_accessibility=False)
        self.assertEqual([p["name"] for p in result], ["Cafe 24"])

    def test_cli_applies_rules_and_does_not_verify_excluded_candidates(self):
        fixture = {"provider": "fixture", "origin": {}, "results": [candidate("Known", business={"cost": 60}), candidate("Over", business={"cost": 120}), candidate("Unknown")], "error": None}
        out = io.StringIO()
        with patch.object(planner, "search_pois", return_value=fixture), patch.object(planner, "run_unified_search") as search, contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            planner.main(["--origin", "天津", "--category", "cafe", "--budget-max", "100", "--poi-only", "--location-policy", "disabled"])
        search.assert_not_called()
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["status"]["overall"], "success")
        self.assertEqual(payload["result"]["top_candidates"], ["Known"])
        self.assertEqual(payload["result"]["candidate_counts"], {"eligible": 1, "pending": 1, "excluded": 1})


if __name__ == "__main__":
    unittest.main()
