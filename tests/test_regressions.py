"""Offline regression tests: no credentials, location access, or network required."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["AMAP_KEY"] = ""

import macos_location
import planner
import providers


def request(**overrides):
    result = dict(origin="测试地点", category="cafe", preferences=[], constraints=[], avoid=[])
    result.update(overrides)
    return result


class Regressions(unittest.TestCase):
    def setUp(self):
        providers.set_request_deadline(None)
        self.addCleanup(providers.set_request_deadline, None)
        # Any forgotten mock must fail locally rather than calling a real service.
        network = patch("urllib.request.urlopen", side_effect=AssertionError("unexpected network call"))
        network.start()
        self.addCleanup(network.stop)

    def test_zero_web_verification_keeps_candidates(self):
        with patch.object(planner, "run_unified_search") as search:
            result = planner.enrich_poi_with_web(request(), [{"name": "Cafe", "score": 40}], 0)
        search.assert_not_called()
        self.assertEqual(result[0]["web_status"], "not_checked")
        self.assertEqual(result[0]["total_score"], 40)

    def test_corelocation_rejects_invalid_coordinates(self):
        for output in ('{"latitude": 91, "longitude": 117}', "nan 117", "39 inf",
                       '[]', 'null', '{"latitude": 39}', '{"lat": true, "lon": 117}',
                       '{"latitude": null, "longitude": 117}', '-91 117', '39 181'):
            with self.subTest(output=output), patch.object(macos_location.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, "")):
                self.assertIsNone(macos_location.get_macos_location())

    def test_corelocation_preserves_valid_zero_coordinates(self):
        for output in ('{"latitude": 0, "longitude": 0}', "0 0"):
            with self.subTest(output=output), patch.object(macos_location.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, "")):
                self.assertEqual(macos_location.get_macos_location(), {
                    "lat": 0.0, "lon": 0.0, "provider": "corelocationcli", "accuracy": "wifi", "coordinate_system": "wgs84",
                })

    def test_quick_search_import_does_not_locate_or_search(self):
        with patch.object(planner, "parse_request", return_value={}) as parse, patch.object(providers, "search_pois") as search:
            import quick_search
        parse.assert_not_called()
        search.assert_not_called()

    def test_overpass_uses_native_geocoding(self):
        amap_origin = {"lat": 39.006, "lon": 117.006, "provider": "amap", "coordinate_system": "gcj02"}
        osm_response = [{"lat": "39.0", "lon": "117.0", "display_name": "测试地点, 中国", "address": {"country_code": "cn"}}]
        calls = []

        def http(url, **kwargs):
            calls.append((url, kwargs))
            return osm_response if url == providers.GEOCODE_URL else {"elements": []}

        with patch.object(providers, "load_geocode_overrides", return_value={}), patch("amap_geocode.geocode_address", return_value=dict(amap_origin, success=True)), patch.object(providers, "_http_get_json", side_effect=http):
            result = providers.search_overpass(request(), "测试地点", enable_accessibility=False)
        self.assertEqual(result["origin"]["lat"], 39.0)
        self.assertEqual(result["origin"]["coordinate_system"], "wgs84")

    def test_overpass_rejects_gcj02_without_sending_query(self):
        with patch.object(providers, "_http_get_json") as http:
            result = providers.search_overpass(request(coordinate_system="gcj02"), "39,117")
        http.assert_not_called()
        self.assertEqual(result["error"], "wgs84_origin_required")

    def test_overpass_retains_equator_and_prime_meridian(self):
        data = {"elements": [{"lat": 0, "lon": 0, "tags": {"name": "Cafe", "amenity": "cafe"}}]}
        with patch.object(providers, "_http_get_json", return_value=data):
            result = providers.search_overpass(request(), "0,0", enable_accessibility=False)
        self.assertEqual(result["results"][0]["distance_m"], 0)
        self.assertEqual(result["results"][0]["coordinate_system"], "wgs84")

    def test_geocode_network_failure_is_a_structured_error(self):
        with patch.object(providers, "geocode_place", side_effect=OSError("offline")):
            result = providers.search_overpass(request(), "London")
        self.assertEqual(result["error"], "origin_geocode_failed")

    def test_unknown_override_datum_is_not_used(self):
        override = {"London": {"lat": 1, "lon": 2}}
        data = [{"lat": "51.5", "lon": "0", "display_name": "London"}]
        with patch.object(providers, "load_geocode_overrides", return_value=override), patch.object(providers, "_http_get_json", return_value=data):
            result = providers.geocode_place("London", coordinate_system="wgs84")
        self.assertEqual(result["lat"], 51.5)

    def test_amap_conversion_uses_lon_lat_and_request_cache(self):
        loc = {"lat": 39, "lon": 117, "coordinate_system": "wgs84"}
        with patch.dict(os.environ, AMAP_KEY="test-only"), patch.object(providers, "_http_get_json", return_value={"status": "1", "locations": "117.006,39.006"}) as http:
            self.assertEqual(providers.amap_coordinates(loc), (39.006, 117.006))
            self.assertEqual(providers.amap_coordinates(loc), (39.006, 117.006))
            http.assert_called_once()
            self.assertEqual(http.call_args.kwargs["params"]["coordsys"], "gps")
            self.assertEqual(http.call_args.kwargs["params"]["locations"], "117.000000,39.000000")
            providers.set_request_deadline(None)
            providers.amap_coordinates(loc)
            self.assertEqual(http.call_count, 2)

    def test_amap_conversion_failure_does_not_relabel_coordinates(self):
        loc = {"lat": 39, "lon": 117, "coordinate_system": "wgs84"}
        for payload in ({"status": "0"}, {"status": "1", "locations": "nan,39"}, {"status": "1", "locations": []}):
            with self.subTest(payload=payload), patch.dict(os.environ, AMAP_KEY="test-only"), patch.object(providers, "_http_get_json", return_value=payload):
                self.assertIsNone(providers.amap_coordinates(loc))
        self.assertEqual(loc["coordinate_system"], "wgs84")

    def test_gcj02_does_not_get_converted_again(self):
        with patch.object(providers, "_http_get_json") as http:
            self.assertEqual(providers.amap_coordinates({"lat": 39, "lon": 117, "coordinate_system": "gcj02"}), (39, 117))
        http.assert_not_called()

    def test_wgs84_fallback_keeps_original_coordinates(self):
        with patch.object(providers, "search_amap_poi_by_coords", return_value={"results": [], "error": "conversion_failed"}), patch.object(providers, "search_overpass", return_value={"results": []}) as osm:
            result = providers.search_pois(request(coordinate_system="wgs84"), "39,117")
        self.assertEqual(osm.call_args.args[1], "39,117")
        self.assertEqual(result["fallback_from"]["reason"], "conversion_failed")

    def test_ip_fallback_preserves_gcj02_datum(self):
        with patch.object(providers, "search_amap_poi_by_coords", return_value={"results": [], "error": "empty"}), patch.object(providers, "_http_get_json") as http:
            result = providers.search_pois(request(), "未指定起点", ip_location={"lat": 39, "lon": 117, "provider": "amap_ip", "coordinate_system": "gcj02"})
        self.assertEqual(result["error"], "wgs84_origin_required")
        http.assert_not_called()

    def test_osm_routing_converts_both_ends_and_keeps_geometry(self):
        origin = {"lat": 39, "lon": 117, "coordinate_system": "wgs84"}
        poi = {"name": "Cafe", "lat": 39.01, "lon": 117.01, "coordinate_system": "wgs84", "score": 50}
        with patch.object(providers, "amap_coordinates", side_effect=[(39.006, 117.006), (39.016, 117.016)]), patch.object(providers.amap_direction, "get_accessibility", return_value={"score": 50}) as route:
            providers._enrich_osm_accessibility([poi], origin)
        route.assert_called_once_with(117.006, 39.006, 117.016, 39.016)
        self.assertEqual(poi["lat"], 39.01)
        self.assertEqual(poi["coordinate_system"], "wgs84")

    def test_transit_details_use_converted_coordinates(self):
        poi = {"lat": 39.01, "lon": 117.01, "coordinate_system": "wgs84", "accessibility": {"mode": "transit"}}
        with patch.object(planner, "amap_coordinates", side_effect=[(39.006, 117.006), (39.016, 117.016)]), patch.object(providers.amap_direction, "get_transit_details", return_value={"steps": []}) as details:
            planner._attach_transit_details({"origin": {"lat": 39, "lon": 117, "coordinate_system": "wgs84"}}, [poi])
        details.assert_called_once_with(117.006, 39.006, 117.016, 39.016)

    def test_partial_routing_enrichment_still_affects_ranking(self):
        elements = [{"lat": 39.001, "lon": 117.001, "tags": {"name": "First"}},
                    {"lat": 39.002, "lon": 117.002, "tags": {"name": "Second"}}]
        with patch.object(providers, "_http_get_json", return_value={"elements": elements}), patch.object(planner, "rank_poi", return_value=40), patch.object(providers, "amap_coordinates", side_effect=[(39.006, 117.006), None, (39.008, 117.008)]), patch.object(providers.amap_direction, "get_accessibility", return_value={"score": 100, "duration_min": 5}):
            result = providers.search_overpass(request(), "39,117")
        self.assertEqual([poi["name"] for poi in result["results"]], ["Second", "First"])

    def test_auto_location_is_used_in_web_query_origin(self):
        output = io.StringIO()
        result = {"provider": "amap_poi", "origin": {"lat": 39, "lon": 117, "display_name": "39,117", "coordinate_system": "gcj02"}, "results": [], "error": None}
        with patch.object(planner, "search_pois", return_value=result), patch.object(planner, "run_unified_search", return_value={"ok": True, "count": 0, "query": "cafe", "stdout": '{"count":0,"results":[]}'}), patch.object(planner, "_resolve_gps_anchor", return_value="天津") as anchor, contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            planner.main(["--category", "cafe", "--location-policy", "disabled"])
        self.assertEqual(json.loads(output.getvalue())["request"]["origin"], "39,117")
        self.assertEqual(anchor.call_args.kwargs["coordinate_system"], "gcj02")

    def test_dependency_discovery_and_explicit_override(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "unified-search-suite/scripts/unified-search.sh"
            script.parent.mkdir(parents=True)
            script.write_text("#!/bin/bash\n", encoding="utf-8")
            with patch.dict(os.environ, {"LOCAL_POI_UNIFIED_SEARCH": ""}), patch.object(planner, "SKILL_DIR", root / "planner"), patch.object(planner, "UNIFIED_SEARCH", root / "unified-search/scripts/unified-search.sh"):
                self.assertEqual(planner.resolve_unified_search(), script)
                with patch.dict(os.environ, LOCAL_POI_UNIFIED_SEARCH=str(root / "absent.sh")):
                    self.assertEqual(planner.run_unified_search("cafe")["contract_error"], "dependency_missing")

    def test_search_contract_cases(self):
        cases = [
            ("broken JSON", False, None),
            ("[]", False, None),
            ('{"count":true,"results":[]}', False, None),
            ('{"count":1,"results":[]}', False, None),
            ('{"count":0,"results":[],"status":"error"}', False, None),
            ('{"count":0,"results":[]}', True, 0),
            ('{"count":1,"results":[{"title":"Cafe"}]}', True, 1),
        ]
        for output, ok, count in cases:
            with self.subTest(output=output), patch.object(planner, "resolve_unified_search", return_value=Path(__file__)), patch.object(planner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, "diagnostics")):
                result = planner.run_unified_search("cafe")
            self.assertEqual(result["ok"], ok)
            self.assertEqual(result["count"], count)
            self.assertNotIn("diagnostics", planner._candidate_lines_from_run(result))

    def test_search_executes_configured_script_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="poi search ") as directory:
            script = Path(directory) / "search script.sh"
            script.write_text("printf '%s' '{\"count\":0,\"results\":[]}'\nprintf 'diagnostics only' >&2\n", encoding="utf-8")
            with patch.dict(os.environ, LOCAL_POI_UNIFIED_SEARCH=str(script)):
                result = planner.run_unified_search("咖啡馆", deadline=time.monotonic() + 5)
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["stderr"], "diagnostics only")
        self.assertEqual(planner._candidate_lines_from_run(result), [])

    def test_search_partial_and_nonzero_exit(self):
        for code in (0, 1):
            with self.subTest(code=code), patch.object(planner, "resolve_unified_search", return_value=Path(__file__)), patch.object(planner.subprocess, "run", return_value=subprocess.CompletedProcess([], code, '{"count":1,"results":[{}],"status":"partial"}', "[tavily] error: unavailable")):
                result = planner.run_unified_search("cafe")
            self.assertEqual(result["ok"], code == 0)
            self.assertTrue(result["partial"])
            self.assertEqual(result["source_errors"], ["tavily"])

    def test_search_deadline_does_not_start_subprocess(self):
        with patch.object(planner, "resolve_unified_search", return_value=Path(__file__)), patch.object(planner.subprocess, "run") as child:
            result = planner.run_unified_search("cafe", time.monotonic() - 1)
        child.assert_not_called()
        self.assertFalse(result["ok"])

    def test_search_timeout_decodes_bytes(self):
        with patch.object(planner, "resolve_unified_search", return_value=Path(__file__)), patch.object(planner.subprocess, "run", side_effect=subprocess.TimeoutExpired("search", 1, output=b"partial", stderr=b"timeout")):
            result = planner.run_unified_search("cafe")
        self.assertFalse(result["ok"])
        self.assertEqual(result["stdout"], "partial")

    def test_failed_web_search_does_not_boost_poi(self):
        with patch.object(planner, "run_unified_search", return_value={"ok": False, "output": "Cafe great coffee"}):
            result = planner.enrich_poi_with_web(request(), [{"name": "Cafe", "score": 40}], 1)
        self.assertEqual(result[0]["total_score"], 40)
        self.assertEqual(result[0]["evidence_level"], "none")

    def test_poi_only_main_never_calls_web_or_specialty(self):
        result = {"provider": "amap_poi", "origin": {}, "results": [{"name": "影院", "score": 40, "distance_m": 100}], "error": None}
        output = io.StringIO()
        with patch.object(planner, "search_pois", return_value=result), patch.object(planner, "run_unified_search") as search, patch.object(planner, "_specialty_fallback_run") as specialty, contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            planner.main(["--origin", "天津", "--category", "电影院", "--location-policy", "disabled", "--poi-only"])
        search.assert_not_called()
        specialty.assert_not_called()
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["queries"], [])
        self.assertEqual(payload["status"]["components"]["web_search"], "not_run")

    def test_quick_cli_without_origin_is_valid_json(self):
        proc = subprocess.run([sys.executable, str(ROOT / "scripts/quick_search.py"), "--query", "附近的咖啡", "--location-policy", "disabled"], capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["poi_provider"]["error"], "origin_required")
        self.assertEqual(payload["status"]["overall"], "error")


if __name__ == "__main__":
    unittest.main()
