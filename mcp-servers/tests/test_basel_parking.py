from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx


ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = ROOT / "servers" / "basel-parking"
spec = importlib.util.spec_from_file_location("basel_parking", SERVER_DIR / "tools" / "basel_parking.py")
parking = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parking)
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "basel_parking.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 9, 24, 11, 42, tzinfo=timezone.utc)


def response(payload: object, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload, request=httpx.Request("GET", parking.API_URL))


class ParkingClientTests(unittest.TestCase):
    def setUp(self):
        self.client = parking.ParkingClient({})
        self.clock = patch.object(parking, "utc_now", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def test_fixed_host_and_cache_preserve_original_timestamps_and_nulls(self):
        with (
            patch.object(parking.httpx, "get", return_value=response(FIXTURE)) as fetch,
            patch.object(parking.time, "monotonic", return_value=100) as clock,
        ):
            first = self.client.snapshot()
            second = self.client.snapshot()
            fetch.assert_called_once()
            self.assertFalse(first["cached"])
            self.assertTrue(second["cached"])
            self.assertEqual(first["retrieved_at"], second["retrieved_at"])
            self.assertEqual(first["records"], FIXTURE["results"])
            self.assertIsNone(first["records"][2]["total"])
            self.assertEqual(fetch.call_args.args, (parking.API_URL,))
            self.assertEqual(fetch.call_args.kwargs["timeout"], 15)
            self.assertFalse(fetch.call_args.kwargs["follow_redirects"])
            self.assertTrue(fetch.call_args.kwargs["verify"].check_hostname)
            first["records"][0]["free"] = 0
            self.assertEqual(self.client.snapshot()["records"][0]["free"], 188)
            clock.return_value = 130
            self.assertFalse(self.client.snapshot()["cached"])
            self.assertEqual(fetch.call_count, 2)

    def test_pagination_collects_all_records_in_stable_id_order(self):
        pages = [
            {"total_count": 3, "results": FIXTURE["results"][:2]},
            {"total_count": 3, "results": FIXTURE["results"][2:]},
        ]
        with (
            patch.object(parking, "PAGE_SIZE", 2),
            patch.object(parking.httpx, "get", side_effect=[response(page) for page in pages]) as fetch,
        ):
            self.assertEqual(self.client.snapshot()["records"], FIXTURE["results"])
        self.assertEqual([call.kwargs["params"]["offset"] for call in fetch.call_args_list], [0, 2])
        self.assertTrue(all(call.kwargs["params"]["order_by"] == "id" for call in fetch.call_args_list))

    def test_empty_results_are_successful_but_failures_are_not(self):
        with patch.object(parking.httpx, "get", return_value=response({"total_count": 0, "results": []})):
            self.assertEqual(self.client.snapshot()["records"], [])
        self.client.cache = None
        for error in (httpx.ConnectError("offline"), httpx.ReadTimeout("timeout")):
            with self.subTest(error=error), patch.object(parking.httpx, "get", side_effect=error):
                with self.assertRaises(httpx.HTTPError):
                    self.client.snapshot()
                self.assertIsNone(self.client.cache)

    def test_expired_cache_is_not_a_fallback_for_network_failure(self):
        with (
            patch.object(parking.httpx, "get", return_value=response(FIXTURE)) as fetch,
            patch.object(parking.time, "monotonic", return_value=100) as clock,
        ):
            self.client.snapshot()
            clock.return_value = 131
            fetch.side_effect = httpx.ConnectError("offline")
            with self.assertRaises(httpx.ConnectError):
                self.client.snapshot()

    def test_redirects_and_http_errors_are_rejected(self):
        for status in (302, 404, 429, 503):
            with self.subTest(status=status), patch.object(parking.httpx, "get", return_value=response({}, status)):
                with self.assertRaises(httpx.HTTPStatusError):
                    self.client.snapshot()
                self.assertIsNone(self.client.cache)

    def test_malformed_incomplete_oversized_and_duplicate_snapshots_are_rejected(self):
        invalid_payloads = [
            [], {}, {"total_count": True, "results": []},
            {"total_count": 1, "results": []},
            {"total_count": 0, "results": FIXTURE["results"]},
            {"total_count": 1001, "results": []},
            {"total_count": 1, "results": [None]},
            {"total_count": 1, "results": [{}]},
            {"total_count": 2, "results": [FIXTURE["results"][0]] * 2},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload), patch.object(parking.httpx, "get", return_value=response(payload)):
                with self.assertRaises(ValueError):
                    self.client.snapshot()
                self.assertIsNone(self.client.cache)

    def test_changes_during_pagination_are_rejected(self):
        with patch.object(parking.httpx, "get", side_effect=[
            response({"total_count": 2, "results": FIXTURE["results"][:1]}),
            response({"total_count": 3, "results": FIXTURE["results"][1:]}),
        ]):
            with self.assertRaisesRegex(ValueError, "changed during pagination"):
                self.client.snapshot()
        self.assertIsNone(self.client.cache)

    def test_configuration_rejects_unbounded_or_invalid_values(self):
        for config in (
            {"timeout_seconds": 0}, {"timeout_seconds": float("nan")},
            {"cache_ttl_seconds": -1}, {"cache_ttl_seconds": float("inf")},
            {"max_pages": 0}, {"max_pages": 11}, {"max_pages": True},
        ):
            with self.subTest(config=config), self.assertRaises(ValueError):
                parking.ParkingClient(config)


class ParkingNormalizationTests(unittest.TestCase):
    def normalize(self, **changes):
        return parking.normalize_record({**FIXTURE["results"][0], **changes}, NOW, 300)

    def test_source_timestamps_and_observed_german_status_are_preserved(self):
        result = self.normalize()
        self.assertEqual(result["status_raw"], "offen")
        self.assertEqual(result["status"], "open")
        self.assertEqual(result["published_at"], "2026-09-24T11:41:00+00:00")
        self.assertEqual(result["last_downloaded"], "2026-09-24T11:41:04+00:00")
        self.assertEqual(result["age_seconds"], 60)
        self.assertEqual(result["freshness"], "fresh")
        self.assertEqual(result["count_quality"], "valid")
        self.assertAlmostEqual(result["occupancy_percent"], 83.12)
        self.assertTrue(result["eligible_for_ranking"])

    def test_missing_counts_are_null_and_never_invent_occupancy(self):
        for changes in ({"free": None}, {"total": None}, {"free": None, "total": None}):
            with self.subTest(changes=changes):
                result = self.normalize(**changes)
                self.assertEqual(result["count_quality"], "missing")
                self.assertIsNone(result["occupancy_percent"])
                self.assertIsNone(result["free_percent"])
                for source, target in (("free", "free_spaces"), ("total", "total_spaces")):
                    if source in changes:
                        self.assertIsNone(result[target])
        self.assertTrue(self.normalize(total=None)["eligible_for_ranking"])
        self.assertFalse(self.normalize(free=None)["eligible_for_ranking"])

    def test_invalid_and_zero_capacity_counts_cannot_be_ranked(self):
        for changes in (
            {"free": -1}, {"total": -1}, {"free": "10"}, {"free": True},
            {"free": 1.5}, {"free": 1115}, {"total": 0}, {"free": float("nan")},
            {"free": 0, "total": 0}, {"free": None, "total": -1},
        ):
            with self.subTest(changes=changes):
                result = self.normalize(**changes)
                self.assertIn(result["count_quality"], ("invalid", "zero_capacity"))
                self.assertIsNone(result["occupancy_percent"])
                self.assertIsNone(result["free_percent"])
                self.assertFalse(result["eligible_for_ranking"])
        result = self.normalize(free=0)
        self.assertEqual(result["occupancy_percent"], 100)
        self.assertEqual(result["free_percent"], 0)

    def test_missing_invalid_stale_and_future_publication_times_are_not_current(self):
        timestamps = [
            (None, "missing"), ("not-a-date", "invalid"),
            ("2026-09-24T11:41:00", "invalid"),
            ((NOW - timedelta(seconds=301)).isoformat(), "stale"),
            ((NOW + timedelta(seconds=1)).isoformat(), "future"),
        ]
        for published, freshness in timestamps:
            with self.subTest(published=published):
                result = self.normalize(published=published)
                self.assertEqual(result["freshness"], freshness)
                self.assertFalse(result["eligible_for_ranking"])
                self.assertTrue(result["warnings"])
        self.assertEqual(self.normalize(published="2026-09-24T13:41:00+02:00")["age_seconds"], 60)
        self.assertEqual(self.normalize(published="2026-09-24T11:41:00Z")["age_seconds"], 60)
        self.assertEqual(self.normalize(published=(NOW - timedelta(seconds=300)).isoformat())["freshness"], "fresh")

    def test_unknown_status_is_labeled_and_closed_status_is_excluded(self):
        for raw_status in (None, "unbekannt", "besetzt", "open", "opening soon"):
            with self.subTest(raw_status=raw_status):
                result = self.normalize(status=raw_status)
                self.assertEqual(result["status_raw"], raw_status)
                self.assertEqual(result["status"], "unknown")
                self.assertTrue(result["eligible_for_ranking"])
                self.assertIn("not confirmed open", " ".join(result["warnings"]))
        self.assertFalse(self.normalize(status="geschlossen")["eligible_for_ranking"])
        self.assertEqual(self.normalize(status=" OFFEN ")["status"], "open")

    def test_invalid_or_missing_coordinates_do_not_become_zero_coordinates(self):
        for point in (None, {}, {"lat": 91, "lon": 7}, {"lat": True, "lon": 7},
                      {"lat": 47, "lon": float("nan")}, {"lat": 47, "lon": 181}):
            with self.subTest(point=point):
                record = self.normalize(geo_point_2d=point)
                self.assertIsNone(record["latitude"])
                self.assertIsNone(record["longitude"])
                self.assertIsNone(parking.straight_line_distance(47, 7, record))

    def test_distance_is_straight_line_in_metres(self):
        record = self.normalize()
        self.assertEqual(parking.straight_line_distance(record["latitude"], record["longitude"], record), 0)
        distance = parking.straight_line_distance(47.5630411, 7.5967098, record)
        self.assertGreater(distance, 1090)
        self.assertLess(distance, 1100)


class ToolRegistry:
    def __init__(self):
        self.tools = {}

    def tool(self, **options):
        def decorate(function):
            self.tools[options.get("name", function.__name__)] = function
            return function
        return decorate


class ParkingToolTests(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry()
        parking.register(self.registry, {})
        clock = patch.object(parking, "utc_now", return_value=NOW)
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        fetch = patch.object(parking.httpx, "get", return_value=response(FIXTURE))
        self.fetch = fetch.start()
        self.addCleanup(fetch.stop)

    def use_records(self, records):
        self.fetch.return_value = response({"total_count": len(records), "results": records})

    def test_register_exposes_four_parking_tools_without_fetching(self):
        registry = ToolRegistry()
        with patch.object(parking.httpx, "get") as fetch:
            parking.register(registry, {})
        fetch.assert_not_called()
        self.assertEqual(set(registry.tools), {
            "list_parking_facilities", "get_parking_status",
            "find_available_parking", "get_parking_summary",
        })

    def test_listing_filters_without_losing_snapshot_metadata(self):
        self.use_records([
            {**FIXTURE["results"][0], "id": "first", "name": "City Centre", "address": "Example 42"},
            {**FIXTURE["results"][0], "id": "second", "name": "Station", "address": "Other 1"},
        ])
        listing = self.registry.tools["list_parking_facilities"]
        self.assertEqual(listing()["matching_facilities"], 2)
        for query in (" FIRST ", "centre", "EXAMPLE"):
            with self.subTest(query=query):
                result = listing(query)
                self.assertEqual(result["total_facilities"], 2)
                self.assertEqual(result["matching_facilities"], 1)
                self.assertEqual(result["facilities"][0]["parking_id"], "first")
                self.assertEqual(result["source_dataset"], "100088")
                self.assertEqual(result["source_url"], parking.SOURCE_URL)
        self.assertEqual(listing("no match")["facilities"], [])
        self.fetch.assert_called_once()

    def test_status_uses_exact_id_and_reports_unknown_id(self):
        status = self.registry.tools["get_parking_status"]
        record = FIXTURE["results"][0]
        result = status(record["id"])
        self.assertEqual(result["facility"]["parking_id"], record["id"])
        self.assertEqual(result["facility"]["free_spaces"], record["free"])
        self.assertNotIn("facilities", result)
        with self.assertRaisesRegex(ValueError, "Unknown parking_id"):
            status("does-not-exist")
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            status(" ")

    def test_cached_records_are_reevaluated_for_freshness(self):
        listing = self.registry.tools["list_parking_facilities"]
        with patch.object(parking.time, "monotonic", return_value=100):
            first = listing()
            self.clock.return_value = NOW + timedelta(seconds=301)
            second = listing()
        self.fetch.assert_called_once()
        self.assertTrue(second["cached"])
        self.assertEqual(first["retrieved_at"], second["retrieved_at"])
        self.assertNotEqual(first["evaluated_at"], second["evaluated_at"])
        self.assertTrue(all(facility["freshness"] == "stale" for facility in second["facilities"]))
        self.assertTrue(all(not facility["eligible_for_ranking"] for facility in second["facilities"]))

    def test_nearby_search_excludes_unusable_data_and_orders_by_distance(self):
        base = FIXTURE["results"][0]
        self.use_records([
            {**base, "id": "far", "geo_point_2d": {"lat": 47.56, "lon": 7.60}},
            {**base, "id": "near", "geo_point_2d": {"lat": 47.55, "lon": 7.60}},
            {**base, "id": "unknown", "status": None, "total": None,
             "geo_point_2d": {"lat": 47.555, "lon": 7.60}},
            {**base, "id": "closed", "status": "geschlossen"},
            {**base, "id": "stale", "published": (NOW - timedelta(seconds=301)).isoformat()},
            {**base, "id": "missing", "free": None},
            {**base, "id": "invalid", "free": -1},
            {**base, "id": "full", "free": 0},
            {**base, "id": "no-point", "geo_point_2d": None},
        ])
        nearby = self.registry.tools["find_available_parking"]
        result = nearby(47.55, 7.60, limit=2)
        self.assertEqual(result["matching_facilities"], 3)
        self.assertEqual([facility["parking_id"] for facility in result["facilities"]], ["near", "unknown"])
        self.assertEqual(result["facilities"][0]["distance_meters"], 0)
        self.assertEqual(result["distance_type"], "straight_line")
        self.assertTrue(result["facilities"][1]["warnings"])
        self.assertEqual(nearby(47.55, 7.60, radius_meters=1)["matching_facilities"], 1)
        self.assertEqual(nearby(47.55, 7.60, min_free_spaces=1000)["facilities"], [])

    def test_invalid_search_inputs_fail_before_network_access(self):
        nearby = self.registry.tools["find_available_parking"]
        for changes in (
            {"latitude": 91}, {"longitude": float("nan")}, {"latitude": True},
            {"radius_meters": 0}, {"radius_meters": float("inf")},
            {"min_free_spaces": 0}, {"min_free_spaces": True},
            {"limit": 0}, {"limit": 101}, {"limit": True},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                nearby(**{"latitude": 47.55, "longitude": 7.60, **changes})
        self.fetch.assert_not_called()

    def test_summary_uses_comparable_fresh_counts_for_weighted_occupancy(self):
        base = FIXTURE["results"][0]
        self.use_records([
            {**base, "id": "first", "free": 20, "total": 100},
            {**base, "id": "second", "free": 80, "total": 300},
            {**base, "id": "unknown-capacity", "free": 10, "total": None, "status": None},
            {**base, "id": "closed", "status": "geschlossen"},
            {**base, "id": "stale", "published": (NOW - timedelta(seconds=301)).isoformat()},
            {**base, "id": "unknown-free", "free": None},
        ])
        result = self.registry.tools["get_parking_summary"]()
        self.assertEqual(result["total_facilities"], 6)
        self.assertEqual(result["eligible_facilities"], 3)
        self.assertEqual(result["known_free_spaces"], 110)
        self.assertEqual(result["occupancy_facilities"], 2)
        self.assertEqual(result["occupancy_total_spaces"], 400)
        self.assertEqual(result["occupancy_free_spaces"], 100)
        self.assertEqual(result["occupancy_percent"], 75)
        self.assertEqual(result["status_counts"], {"open": 4, "closed": 1, "unknown": 1})
        self.assertEqual(result["freshness_counts"]["stale"], 1)
        self.assertIn("not confirmed open", " ".join(result["warnings"]))

    def test_empty_summary_does_not_invent_totals(self):
        self.use_records([])
        result = self.registry.tools["get_parking_summary"]()
        self.assertEqual(result["total_facilities"], 0)
        for field in ("known_free_spaces", "occupancy_total_spaces", "occupancy_free_spaces", "occupancy_percent"):
            self.assertIsNone(result[field])

    def test_tools_propagate_source_failure_instead_of_returning_empty_results(self):
        self.fetch.side_effect = httpx.ConnectError("offline")
        with self.assertRaises(httpx.ConnectError):
            self.registry.tools["list_parking_facilities"]()

    def test_freshness_configuration_rejects_invalid_values(self):
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "freshness_seconds"):
                parking.register(ToolRegistry(), {"freshness_seconds": value})