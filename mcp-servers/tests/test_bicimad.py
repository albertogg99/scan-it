import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx
import yaml


ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = ROOT / "servers" / "bicimad"
spec = importlib.util.spec_from_file_location("bicimad", SERVER_DIR / "tools" / "bicimad.py")
bicimad = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bicimad)
FIXTURES = ROOT / "tests" / "fixtures" / "bicimad"
INFO = json.loads((FIXTURES / "station_information.json").read_text(encoding="utf-8"))
STATUS = json.loads((FIXTURES / "station_status.json").read_text(encoding="utf-8"))
TYPES = json.loads((FIXTURES / "vehicle_types.json").read_text(encoding="utf-8"))
CONFIG = yaml.safe_load((SERVER_DIR / "config" / "tools_config.yaml").read_text(encoding="utf-8"))["bicimad"]
NOW = datetime.fromtimestamp(STATUS["last_updated"] + 30, timezone.utc)
SOL = (40.4169, -3.7035)
CIBELES = (40.42031, -3.69214)
TOOLS = {
    "search_bike_stations", "find_bike_stations_near", "get_bike_station",
    "get_bicimad_summary", "get_bicimad_prices", "search_places",
}


class FakeMCP:
    def __init__(self):
        self.tools = {}
        self.options = {}

    def tool(self, **options):
        def decorator(function):
            self.tools[options["name"]] = function
            self.options[options["name"]] = options
            return function
        return decorator


def feeds(info=INFO, status=STATUS, types=TYPES):
    payloads = {"station_information": info, "station_status": status, "vehicle_types": types}

    def fake_get(url, **_kwargs):
        feed = url.rsplit("/", 1)[1]
        return httpx.Response(200, json=payloads[feed], request=httpx.Request("GET", url))
    return fake_get


class BicimadTestCase(unittest.TestCase):
    def setUp(self):
        self.mcp = FakeMCP()
        bicimad.register(self.mcp, deepcopy(CONFIG))
        self.tools = self.mcp.tools
        clock = patch.object(bicimad, "utc_now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        self.status = deepcopy(STATUS)
        self.use_feeds()

    def use_feeds(self, **kwargs):
        patcher = patch.object(bicimad.httpx, "get", side_effect=feeds(status=self.status, **kwargs))
        self.fetch = patcher.start()
        self.addCleanup(patcher.stop)

    def near_ids(self, origin=SOL, **kwargs):
        result = self.tools["find_bike_stations_near"](*origin, force_refresh=True, **kwargs)
        return [s["station_id"] for s in result["stations"]], result


class SearchPlacesTests(BicimadTestCase):
    def geocode(self, payload):
        response = lambda url, **_: httpx.Response(200, json=payload, request=httpx.Request("GET", url))
        return patch.object(bicimad.httpx, "get", side_effect=response)

    def test_returns_candidates_inside_madrid_and_flags_ambiguity(self):
        payload = [
            {"lat": "40.4237", "lon": "-3.7122", "display_name": "Plaza de España, Madrid", "importance": 0.6},
            {"lat": "40.4200", "lon": "-3.7000", "display_name": "Plaza de España, Centro"},
            {"lat": "41.65", "lon": "-0.88", "display_name": "Plaza de España, Zaragoza"},
            {"lat": "bad", "lon": "x"},
        ]
        with self.geocode(payload):
            result = self.tools["search_places"]("Plaza de España")
        self.assertEqual(len(result["places"]), 2)
        self.assertTrue(result["ambiguous"])
        self.assertEqual(result["places"][0]["latitude"], 40.4237)

    def test_no_results_and_network_error(self):
        with self.geocode([]):
            result = self.tools["search_places"]("nowhere at all")
        self.assertEqual(result["places"], [])
        self.assertFalse(result["ambiguous"])
        with patch.object(bicimad.httpx, "get", side_effect=httpx.ConnectError("offline")):
            with self.assertRaises(bicimad.ToolError):
                self.tools["search_places"]("Retiro")

    def test_rejects_empty_query(self):
        with self.assertRaises(bicimad.ToolError):
            self.tools["search_places"]("  ")


class RegistrationTests(BicimadTestCase):
    def test_six_tools_with_explicit_annotations(self):
        self.assertEqual(set(self.tools), TOOLS)
        for name, options in self.mcp.options.items():
            annotations = options["annotations"]
            self.assertTrue(annotations.read_only_hint, name)
            self.assertFalse(annotations.destructive_hint, name)
            self.assertTrue(annotations.idempotent_hint, name)
            self.assertEqual(annotations.open_world_hint, name != "get_bicimad_prices", name)

    def test_real_server_lists_tools_and_instructions_exist(self):
        from mcp.server.mcpserver import MCPServer
        server = MCPServer("bicimad-test")
        bicimad.register(server, deepcopy(CONFIG))
        listed = asyncio.run(server.list_tools())
        tools = {tool.name: tool for tool in listed}
        self.assertEqual(set(tools), TOOLS)
        for tool in tools.values():
            self.assertIsNotNone(tool.annotations)
            self.assertTrue(tool.annotations.read_only_hint)
        instructions = (SERVER_DIR / "config" / "server_instructions.md").read_text(encoding="utf-8")
        self.assertIn("72 Cibeles", instructions)
        self.assertIn("40.42031, -3.69214", instructions)

    def test_config_registers_only_bicimad_module_with_pinned_requirements(self):
        self.assertTrue((SERVER_DIR / "tools" / "bicimad.py").is_file())
        requirements = (SERVER_DIR / "tools" / "requirements" / "bicimad.txt").read_text(encoding="utf-8")
        self.assertIn("httpx>=", requirements)


class ParsingAndEnvelopeTests(BicimadTestCase):
    def test_merge_and_envelope(self):
        result = self.tools["get_bike_station"]("2307")
        station = result["station"]
        self.assertEqual(station["name"], "1 - Metro Sol")
        self.assertEqual(station["short_name"], "1")
        self.assertEqual(station["bikes_available"], 4)
        self.assertEqual(station["docks_available"], 18)
        info = next(s for s in INFO["data"]["stations"] if s["station_id"] == "2307")
        self.assertEqual(station["capacity"], info["capacity"])
        self.assertEqual(result["freshness"], "fresh")
        self.assertEqual(result["age_seconds"], 30)
        self.assertTrue(result["published_at"].endswith("+02:00"))
        self.assertTrue(station["last_reported_at"].endswith("+02:00"))
        self.assertFalse(result["cached"])
        for key in ("retrieved_at", "source_url", "attribution", "data_source_url"):
            self.assertTrue(result[key])
        self.assertEqual(result["attribution"], "Datos: BiciMAD (EMT Madrid), GBFS")
        self.assertEqual(self.tools["get_bike_station"]("2307")["station"]["rental_methods"], info["rental_methods"])

    def test_vehicle_types_are_readable(self):
        types = {t["vehicle_type_id"]: t["kind"] for t in bicimad.parse_vehicle_types(TYPES).values()}
        self.assertEqual(types["EFIT"], "ebike")
        self.assertEqual(types["FIT"], "pedal")
        self.assertEqual(types["CHLOE"], "scooter")
        by_type = self.tools["get_bike_station"]("2307")["station"]["bikes_by_type"]
        self.assertEqual({(t["vehicle_type_id"], t["kind"], t["count"]) for t in by_type},
                         {("EFIT", "ebike", 3), ("FIT", "pedal", 1)})

    def test_geofenced_bikes_are_not_counted_as_usable(self):
        station = self.tools["get_bike_station"]("1424")["station"]
        self.assertEqual(station["bikes_available"], 24)
        self.assertEqual(station["bikes_by_type"][0]["count"], 24)

    def test_missing_counts_stay_null(self):
        station = self.tools["get_bike_station"]("2306")["station"]
        self.assertIsNone(station["bikes_available"])
        self.assertIsNone(station["bikes_by_type"])
        self.assertEqual(station["docks_available"], 22)
        self.assertIn("bike_count_missing", station["flags"])

    def test_zero_is_preserved_as_zero(self):
        station = self.tools["get_bike_station"]("1428")["station"]
        self.assertEqual(station["bikes_available"], 0)

    def test_station_status_flags(self):
        self.assertIn("status_not_in_service", self.tools["get_bike_station"]("2444")["station"]["flags"])
        self.assertIn("not_renting", self.tools["get_bike_station"]("2117")["station"]["flags"])
        stale = self.tools["get_bike_station"]("1472")["station"]
        self.assertIn("station_report_stale", stale["flags"])
        self.assertEqual(stale["report_freshness"], "stale")
        self.assertEqual(self.tools["get_bike_station"]("1437")["station"]["flags"], [])

    def test_unknown_station_is_tool_error(self):
        with self.assertRaises(bicimad.ToolError):
            self.tools["get_bike_station"]("nope")
        with self.assertRaises(bicimad.ToolError):
            self.tools["get_bike_station"]("  ")


class FreshnessTests(unittest.TestCase):
    def test_feed_freshness_states(self):
        now = NOW
        base = int(now.timestamp())
        cases = [
            (base - 10, "fresh"), (base - 301, "stale"), (None, "missing"),
            ("x", "invalid"), (True, "invalid"), (-5, "invalid"), (base + 60, "future"),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(bicimad.freshness_of(value, now, 300)[0], expected)

    def test_stale_feed_is_reported_and_blocks_ranking(self):
        mcp = FakeMCP()
        bicimad.register(mcp, deepcopy(CONFIG))
        later = datetime.fromtimestamp(STATUS["last_updated"] + 1000, timezone.utc)
        with (
            patch.object(bicimad, "utc_now", return_value=later),
            patch.object(bicimad.httpx, "get", side_effect=feeds()),
        ):
            result = mcp.tools["find_bike_stations_near"](*SOL)
            self.assertEqual(result["freshness"], "stale")
            self.assertEqual(result["stations"], [])
            self.assertEqual(result["reason"], "feed_not_current")
            self.assertTrue(result["warnings"])
            summary = mcp.tools["get_bicimad_summary"]()
            self.assertIsNone(summary["bikes_available"])
            self.assertEqual(summary["stations_with_fresh_reports"], 0)


class SearchTests(BicimadTestCase):
    def search(self, query, **kwargs):
        return self.tools["search_bike_stations"](query, **kwargs)

    def test_accent_and_case_insensitive(self):
        for query in ("alcala", "ALCALÁ", "Puerta de Alcalá"):
            with self.subTest(query=query):
                ids = [s["station_id"] for s in self.search(query)["stations"]]
                self.assertIn("1464", ids)
        self.assertEqual(self.search("sol")["stations"][0]["station_id"] in {"2307", "1437"}, True)

    def test_matches_number_address_and_name(self):
        self.assertEqual(self.search("86")["stations"][0]["station_id"], "1489")
        self.assertIn("1489", [s["station_id"] for s in self.search("Cibeles")["stations"]])
        self.assertIn("2117", [s["station_id"] for s in self.search("delicias lacy")["stations"]])
        address = INFO["data"]["stations"][0]["address"]
        self.assertTrue(self.search(address.split(",")[0])["matching_stations"] >= 1)

    def test_limit_and_empty_and_no_match(self):
        self.assertEqual(len(self.search("atocha", limit=1)["stations"]), 1)
        self.assertEqual(self.search("atocha")["matching_stations"], 2)
        self.assertEqual(self.search("zzzz")["stations"], [])
        for bad in ({"query": "  "}, {"query": "sol", "limit": 0}, {"query": "sol", "limit": 11}):
            with self.subTest(bad=bad), self.assertRaises(bicimad.ToolError):
                self.search(**bad)


class NearbyTests(BicimadTestCase):
    def test_ranking_nearest_first_with_distance(self):
        ids, result = self.near_ids(SOL, radius_m=500)
        self.assertEqual(set(ids), {"1437", "2307"})
        distances = [s["distance_m"] for s in result["stations"]]
        self.assertEqual(distances, sorted(distances))
        self.assertTrue(all(isinstance(d, int) and d <= 500 for d in distances))
        self.assertEqual(result["distance_type"], "straight_line")
        self.assertIsNone(result["reason"])

    def test_radius_filter(self):
        ids, _ = self.near_ids(SOL, radius_m=10)
        self.assertEqual(ids, [])
        wide, _ = self.near_ids(SOL, radius_m=3000, limit=10)
        self.assertGreater(len(wide), len(self.near_ids(SOL, radius_m=500)[0]))

    def test_need_bikes_vs_docks(self):
        bikes, _ = self.near_ids(CIBELES, radius_m=600, need="bikes")
        docks, _ = self.near_ids(CIBELES, radius_m=600, need="docks")
        self.assertIn("1489", bikes)
        self.assertNotIn("1489", docks)
        self.assertIn("1464", docks)

    def test_min_count(self):
        ids, _ = self.near_ids(SOL, radius_m=500, min_count=5)
        self.assertEqual(ids, ["1437"])

    def test_vehicle_type_filter(self):
        self.assertEqual(self.near_ids(SOL, radius_m=500, vehicle_type="pedal")[0], ["2307"])
        self.assertEqual(self.near_ids(SOL, radius_m=500, vehicle_type="FIT")[0], ["2307"])
        self.assertEqual(set(self.near_ids(SOL, radius_m=500, vehicle_type="ebike")[0]), {"1437", "2307"})
        self.assertEqual(self.near_ids(SOL, radius_m=500, vehicle_type="scooter")[0], [])
        with self.assertRaises(bicimad.ToolError):
            self.near_ids(SOL, vehicle_type="unicorn")

    def test_ineligible_stations_are_excluded(self):
        # 1428 has zero bikes, 2117 is not renting, 1472 is stale, 2306 has unknown counts, 2444 is not in service.
        wide, result = self.near_ids(SOL, radius_m=3000, limit=10)
        for excluded in ("1428", "2117", "1472", "2306", "2444"):
            self.assertNotIn(excluded, wide)
        self.assertGreater(result["stations_in_radius"], result["matching_stations"])
        docks, _ = self.near_ids(SOL, radius_m=3000, need="docks", limit=10)
        self.assertNotIn("2444", docks)
        self.assertNotIn("1472", docks)
        self.assertIn("2117", docks)
        self.assertIn("2306", docks)

    def test_no_eligible_vs_nothing_in_radius(self):
        _, none_in_radius = self.near_ids((40.5, -3.5), radius_m=100)
        self.assertEqual(none_in_radius["reason"], "no_stations_in_radius")
        _, none_eligible = self.near_ids(SOL, radius_m=500, min_count=50)
        self.assertEqual(none_eligible["reason"], "no_eligible_stations")
        self.assertTrue(none_eligible["message"])

    def test_validation(self):
        near = self.tools["find_bike_stations_near"]
        invalid = [
            {"latitude": 47.37, "longitude": 8.53}, {"latitude": 40.1, "longitude": -3.7},
            {"latitude": 40.4, "longitude": -3.3}, {"latitude": float("nan"), "longitude": -3.7},
            {"latitude": 40.4, "longitude": -3.7, "radius_m": 0},
            {"latitude": 40.4, "longitude": -3.7, "radius_m": 3001},
            {"latitude": 40.4, "longitude": -3.7, "need": "x"},
            {"latitude": 40.4, "longitude": -3.7, "min_count": 0},
            {"latitude": 40.4, "longitude": -3.7, "limit": 11},
        ]
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), self.assertRaises(bicimad.ToolError):
                near(**kwargs)
        self.fetch.assert_not_called()


class SummaryTests(BicimadTestCase):
    def test_totals_use_only_current_in_service_stations(self):
        summary = self.tools["get_bicimad_summary"]()
        in_service = [s for s in STATUS["data"]["stations"] if s["station_id"] not in ("2444", "1472")]
        expected_bikes = sum(s.get("num_bikes_available", 0) for s in in_service)
        expected_docks = sum(s["num_docks_available"] for s in in_service)
        self.assertEqual(summary["stations_total"], 13)
        self.assertEqual(summary["stations_in_service"], 12)
        self.assertEqual(summary["stations_not_in_service"], 1)
        self.assertEqual(summary["stations_with_fresh_reports"], 11)
        self.assertEqual(summary["stations_with_stale_or_invalid_reports"], 1)
        self.assertEqual(summary["bikes_available"], expected_bikes)
        self.assertEqual(summary["docks_available"], expected_docks)
        self.assertEqual(summary["stations_with_zero_bikes"], 1)
        self.assertEqual(summary["stations_with_zero_docks"], 2)
        self.assertEqual(summary["stations_with_missing_counts"], 1)
        self.assertEqual(summary["bikes_available_by_kind"]["pedal"], 1)
        self.assertTrue(summary["warnings"])
        self.assertEqual(summary["freshness"], "fresh")


class PricesTests(BicimadTestCase):
    def test_prices_from_config_without_network(self):
        result = self.tools["get_bicimad_prices"]()
        self.fetch.assert_not_called()
        plans = {plan["id"]: plan for plan in result["plans"]}
        self.assertEqual(set(plans), {"pay_per_use", "flat_rate_30_days", "bicibus_emt"})
        self.assertEqual([r["price_eur"] for r in plans["pay_per_use"]["rules"]], [0.5, 0.5, 3.0])
        self.assertEqual(plans["flat_rate_30_days"]["price_eur"], 10.0)
        self.assertEqual(plans["bicibus_emt"]["price_eur"], 1.8)
        self.assertIn("source_url", result)


class UpstreamErrorTests(BicimadTestCase):
    def test_failures_are_tool_errors_not_empty_results(self):
        errors = [
            httpx.ConnectError("offline"), httpx.ReadTimeout("timeout"),
        ]
        calls = [
            lambda: self.tools["search_bike_stations"]("sol", force_refresh=True),
            lambda: self.tools["find_bike_stations_near"](*SOL, force_refresh=True),
            lambda: self.tools["get_bike_station"]("1437", force_refresh=True),
            lambda: self.tools["get_bicimad_summary"](force_refresh=True),
        ]
        for error in errors:
            for call in calls:
                with self.subTest(error=error), patch.object(bicimad.httpx, "get", side_effect=error):
                    with self.assertRaises(bicimad.ToolError):
                        call()

    def test_http_status_and_bad_json_and_bad_shape(self):
        def status_response(code):
            return lambda url, **_: httpx.Response(code, json={}, request=httpx.Request("GET", url))
        for code in (404, 429, 503):
            with self.subTest(code=code), patch.object(bicimad.httpx, "get", side_effect=status_response(code)):
                with self.assertRaisesRegex(bicimad.ToolError, str(code)):
                    self.tools["get_bicimad_summary"](force_refresh=True)
        with patch.object(bicimad.httpx, "get", side_effect=lambda url, **_: httpx.Response(
                200, content=b"<html>", request=httpx.Request("GET", url))):
            with self.assertRaises(bicimad.ToolError):
                self.tools["get_bicimad_summary"](force_refresh=True)
        for payload in ([], {}, {"data": {}}, {"data": {"stations": "x"}}):
            with self.subTest(payload=payload), patch.object(
                bicimad.httpx, "get", side_effect=lambda url, **_: httpx.Response(
                    200, json=payload, request=httpx.Request("GET", url))
            ):
                with self.assertRaises(bicimad.ToolError):
                    self.tools["get_bicimad_summary"](force_refresh=True)

    def test_failure_does_not_poison_cache(self):
        with patch.object(bicimad.httpx, "get", side_effect=httpx.ConnectError("offline")):
            with self.assertRaises(bicimad.ToolError):
                self.tools["get_bicimad_summary"]()
        self.assertFalse(self.tools["get_bicimad_summary"]()["cached"])


class CacheTests(BicimadTestCase):
    def test_cache_and_force_refresh(self):
        with patch.object(bicimad.time, "monotonic", return_value=100) as clock:
            first = self.tools["get_bicimad_summary"]()
            self.assertEqual(self.fetch.call_count, 3)
            second = self.tools["get_bicimad_summary"]()
            self.assertEqual(self.fetch.call_count, 3)
            self.assertFalse(first["cached"])
            self.assertTrue(second["cached"])
            self.assertEqual(first["retrieved_at"], second["retrieved_at"])
            forced = self.tools["get_bicimad_summary"](force_refresh=True)
            self.assertFalse(forced["cached"])
            self.assertEqual(self.fetch.call_count, 6)
            clock.return_value = 100 + 29
            self.assertTrue(self.tools["get_bicimad_summary"]()["cached"])
            clock.return_value = 100 + 31
            self.assertFalse(self.tools["get_bicimad_summary"]()["cached"])

    def test_fetch_uses_configured_language_and_no_redirects(self):
        self.tools["get_bicimad_summary"]()
        urls = {call.args[0] for call in self.fetch.call_args_list}
        self.assertEqual(urls, {f"{CONFIG['gbfs_base_url']}/es/{feed}" for feed in bicimad.FEEDS})
        for call in self.fetch.call_args_list:
            self.assertFalse(call.kwargs["follow_redirects"])
            self.assertEqual(call.kwargs["timeout"], 15)

    def test_cache_returns_copies(self):
        first = self.tools["get_bike_station"]("1437")
        first["station"]["bikes_available"] = 999
        self.assertEqual(self.tools["get_bike_station"]("1437")["station"]["bikes_available"], 5)


class ConfigTests(unittest.TestCase):
    def test_invalid_configuration_is_rejected(self):
        for change in ({"gbfs_base_url": "http://x"}, {"timeout_seconds": 0}, {"cache_ttl_seconds": -1},
                       {"default_radius_m": 5000}, {"prices": []}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                bicimad.register(FakeMCP(), {**deepcopy(CONFIG), **change})


if __name__ == "__main__":
    unittest.main()
