from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

import httpx
import yaml


ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = ROOT / "servers" / "public-transport"
spec = importlib.util.spec_from_file_location("public_transport", SERVER_DIR / "tools" / "public_transport.py")
transport = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transport)
CONFIG = yaml.safe_load((SERVER_DIR / "config" / "tools_config.yaml").read_text())["public_transport"]
NOW = datetime(2026, 9, 24, 11, 42, tzinfo=timezone.utc)
STOP = """
<PlaceResult><Place><StopPlace>
<StopPlaceRef>ch:1:sloid:3000</StopPlaceRef>
<StopPlaceName><Text>Zuerich HB</Text></StopPlaceName>
</StopPlace><GeoPosition><siri:Longitude>8.54021</siri:Longitude>
<siri:Latitude>47.37818</siri:Latitude></GeoPosition>
<Mode><PtMode>rail</PtMode></Mode></Place><Probability>1</Probability></PlaceResult>
"""
CALL = """
<siri:StopPointRef>ch:1:sloid:3090</siri:StopPointRef>
<StopPointName><Text>Zuerich Selnau</Text></StopPointName>
<PlannedQuay><Text>A</Text></PlannedQuay><EstimatedQuay><Text>B</Text></EstimatedQuay>
<ServiceDeparture><TimetabledTime>2026-09-24T11:45:00Z</TimetabledTime>
<EstimatedTime>2026-09-24T11:47:00Z</EstimatedTime></ServiceDeparture>
"""
SERVICE = """
<Service><Mode><PtMode>rail</PtMode></Mode>
<PublishedServiceName><Text>S10</Text></PublishedServiceName>
<DestinationText><Text>Test destination</Text></DestinationText>
<JourneyRef>test-journey</JourneyRef><OperatingDayRef>2026-09-24</OperatingDayRef>
<Cancelled>false</Cancelled></Service>
"""
DEPARTURE = f"<StopEventResult><StopEvent><ThisCall><CallAtStop>{CALL}</CallAtStop></ThisCall>{SERVICE}</StopEvent></StopEventResult>"
TRIP = f"""
<TripResult><Trip><Id>test-trip</Id><Duration>PT15M</Duration>
<StartTime>2026-09-24T11:45:00Z</StartTime><EndTime>2026-09-24T12:00:00Z</EndTime>
<Transfers>0</Transfers><Leg><Id>1</Id><Duration>PT10M</Duration><TimedLeg>
<LegBoard>{CALL}</LegBoard><LegAlight>
<siri:StopPointRef>ch:1:sloid:3000</siri:StopPointRef>
<StopPointName><Text>Zuerich HB</Text></StopPointName>
<ServiceArrival><TimetabledTime>2026-09-24T11:55:00Z</TimetabledTime></ServiceArrival>
</LegAlight>{SERVICE}</TimedLeg></Leg>
<Leg><Id>2</Id><TransferLeg><TransferType>walk</TransferType>
<LegStart><siri:StopPointRef>ch:1:sloid:3000</siri:StopPointRef></LegStart>
<LegEnd><siri:StopPointRef>ch:1:sloid:3000:1</siri:StopPointRef></LegEnd>
<Duration>PT2M</Duration></TransferLeg></Leg>
<Leg><Id>3</Id><ContinuousLeg><LegStart><Name><Text>Station</Text></Name></LegStart>
<LegEnd><Name><Text>Exit</Text></Name></LegEnd><Service><PersonalMode>foot</PersonalMode></Service>
<Duration>PT3M</Duration><Length>120</Length></ContinuousLeg></Leg>
</Trip></TripResult>
"""
SITUATION = """
<StopEventResponseContext><Situations><PtSituation>
<siri:SituationNumber>test-incident</siri:SituationNumber>
<siri:ValidityPeriod><siri:StartTime>2026-09-24T11:00:00Z</siri:StartTime></siri:ValidityPeriod>
<siri:PublishingActions><siri:PublishingAction><siri:PassengerInformationAction>
<siri:TextualContent><siri:SummaryContent><siri:SummaryText>Service disruption</siri:SummaryText>
</siri:SummaryContent></siri:TextualContent></siri:PassengerInformationAction>
</siri:PublishingAction></siri:PublishingActions></PtSituation></Situations></StopEventResponseContext>
"""


def xml_response(kind: str = "LocationInformation", body: str = STOP, status: int = 200) -> httpx.Response:
    content = (
        f'<OJP xmlns="{transport.NS["ojp"]}" xmlns:siri="{transport.NS["siri"]}" version="2.0">'
        f'<OJPResponse><siri:ServiceDelivery><OJP{kind}Delivery>'
        f'<siri:ResponseTimestamp>{NOW.isoformat()}</siri:ResponseTimestamp>{body}'
        f'</OJP{kind}Delivery></siri:ServiceDelivery></OJPResponse></OJP>'
    )
    return httpx.Response(status, text=content, headers={"content-type": "application/xml"},
                          request=httpx.Request("POST", transport.API_URL))


class ToolRegistry:
    def __init__(self):
        self.tools = {}

    def tool(self, **options):
        def decorate(function):
            self.tools[options.get("name", function.__name__)] = function
            return function
        return decorate


class TransportClientTests(unittest.TestCase):
    def setUp(self):
        token = patch.dict(transport.os.environ, {"OJP_API_TOKEN": "test-token"})
        token.start()
        self.addCleanup(token.stop)
        clock = patch.object(transport, "utc_now", return_value=NOW)
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        self.client = transport.TransportClient(deepcopy(CONFIG))

    def test_stop_search_uses_ojp20_xml_bearer_auth_and_safe_tls(self):
        with patch.object(transport.httpx, "stream", return_value=nullcontext(xml_response())) as fetch:
            result = self.client.search_stops("HB & <test>")
        self.assertEqual(result["stops"][0]["stop_id"], "ch:1:sloid:3000")
        self.assertEqual(result["stops"][0]["modes"], ["rail"])
        self.assertEqual(fetch.call_args.args, ("POST", transport.API_URL))
        options = fetch.call_args.kwargs
        self.assertEqual(options["headers"]["Authorization"], "Bearer test-token")
        self.assertFalse(options["follow_redirects"])
        self.assertTrue(options["verify"].check_hostname)
        request = ET.fromstring(options["content"])
        self.assertEqual(request.get("version"), "2.0")
        self.assertEqual(transport.text(request, ".//ojp:InitialInput/ojp:Name"), "HB & <test>")
        self.assertEqual(transport.text(request, ".//ojp:Restrictions/ojp:Type"), "stop")
        self.assertNotIn("test-token", str(result))

    def test_cache_is_bounded_isolated_and_reevaluates_timestamps(self):
        with (
            patch.object(transport.httpx, "stream", side_effect=lambda *args, **kwargs: nullcontext(xml_response())) as fetch,
            patch.object(transport.time, "monotonic", return_value=100) as clock,
            patch.object(transport, "MAX_CACHE_ENTRIES", 2),
        ):
            first = self.client.search_stops("HB")
            first["stops"][0]["name"] = "changed"
            self.clock.return_value = NOW + timedelta(seconds=301)
            second = self.client.search_stops("HB")
            self.assertTrue(second["cached"])
            self.assertEqual(second["response_freshness"], "stale")
            self.assertEqual(second["retrieved_at"], first["retrieved_at"])
            self.assertEqual(second["stops"][0]["name"], "Zuerich HB")
            fetch.assert_called_once()
            clock.return_value = 131
            self.assertFalse(self.client.search_stops("HB")["cached"])
            self.client.search_stops("Bern")
            self.client.search_stops("Basel")
            self.assertEqual(len(self.client.cache), 2)

    def test_origin_is_available_without_token_and_cannot_be_mutated(self):
        with patch.dict(transport.os.environ, {"OJP_API_TOKEN": ""}):
            registry = ToolRegistry()
            transport.register(registry, CONFIG)
        context = registry.tools["get_transport_origin"]()
        self.assertEqual(context["origin"]["stop_id"], "ch:1:sloid:3090")
        self.assertEqual(context["origin"]["name"], "Z\u00fcrich Selnau")
        self.assertEqual(context["current_time"], "2026-09-24T13:42:00+02:00")
        self.assertFalse(context["token_configured"])
        context["origin"]["stop_id"] = "elsewhere"
        self.assertEqual(registry.tools["get_transport_origin"]()["origin"]["stop_id"], "ch:1:sloid:3090")
        with self.assertRaisesRegex(transport.ToolError, "not configured"):
            registry.tools["search_transport_stops"]("HB")

    def test_http_errors_are_not_empty_results_and_do_not_leak_secrets(self):
        for status in (302, 401, 403, 429, 503):
            with self.subTest(status=status), patch.object(transport.httpx, "stream", return_value=nullcontext(xml_response(status=status))):
                with self.assertRaises(RuntimeError) as caught:
                    self.client.search_stops("HB")
                self.assertNotIn("test-token", str(caught.exception))
                self.assertFalse(self.client.cache)
        with patch.object(transport.httpx, "stream", side_effect=httpx.ReadTimeout("test-token")):
            with self.assertRaisesRegex(RuntimeError, "unavailable") as caught:
                self.client.search_stops("HB")
            self.assertNotIn("test-token", str(caught.exception))

    def test_gateway_authentication_and_access_denial_have_distinct_safe_errors(self):
        for status, gateway_error, message in (
            (401, "Authorization field missing", r"authentication failed \(HTTP 401\)"),
            (403, "Access to this API has been disallowed", r"access denied \(HTTP 403\)"),
        ):
            response = httpx.Response(
                status, json={"error": gateway_error, "token": "test-token"},
                request=httpx.Request("POST", transport.API_URL),
            )
            with self.subTest(status=status), patch.object(transport.httpx, "stream", return_value=nullcontext(response)):
                with self.assertRaisesRegex(RuntimeError, message) as caught:
                    self.client.search_stops("HB")
                self.assertNotIn("test-token", str(caught.exception))
                self.assertFalse(self.client.cache)
                if status == 403:
                    self.assertIn("active OJP 2.0 subscription", str(caught.exception))
                    self.assertIn("TOKEN, not its token hash", str(caught.exception))

    def test_invalid_xml_unsafe_entities_and_missing_delivery_are_rejected(self):
        for content in (
            b"not xml", b"<OJP/>",
            b'<!DOCTYPE OJP [<!ENTITY secret SYSTEM "file:///etc/passwd">]><OJP>&secret;</OJP>',
            xml_response(kind="Trip").content,
            xml_response(body='<siri:Status>false</siri:Status>').content,
            xml_response(body='<siri:ErrorCondition/>').content,
        ):
            with self.subTest(content=content), self.assertRaises(RuntimeError):
                transport.parse_delivery(content, "LocationInformation")

    def test_size_limit_and_wrong_content_type_are_rejected(self):
        with (
            patch.object(transport, "MAX_RESPONSE_BYTES", 10),
            patch.object(transport.httpx, "stream", return_value=nullcontext(xml_response())),
            self.assertRaisesRegex(RuntimeError, "size limit"),
        ):
            self.client.search_stops("HB")
        response = xml_response()
        response.headers["content-type"] = "text/html"
        with patch.object(transport.httpx, "stream", return_value=nullcontext(response)):
            with self.assertRaisesRegex(RuntimeError, "non-XML"):
                self.client.search_stops("HB")

    def test_rate_limit_counts_uncached_requests_and_expires(self):
        self.client.rate_limit = 1
        with (
            patch.object(transport.httpx, "stream", side_effect=lambda *args, **kwargs: nullcontext(xml_response())),
            patch.object(transport.time, "monotonic", return_value=100) as clock,
        ):
            self.client.search_stops("HB")
            self.assertTrue(self.client.search_stops("HB")["cached"])
            with self.assertRaisesRegex(RuntimeError, "Local OJP request limit"):
                self.client.search_stops("Bern")
            clock.return_value = 160
            self.client.search_stops("Bern")

    def test_empty_stop_results_succeed_but_incomplete_records_fail(self):
        with patch.object(transport.httpx, "stream", return_value=nullcontext(xml_response(body=""))):
            self.assertEqual(self.client.search_stops("unknown")["stops"], [])
        with patch.object(transport.httpx, "stream", return_value=nullcontext(xml_response(body="<PlaceResult/>"))):
            with self.assertRaisesRegex(RuntimeError, "incomplete stop"):
                self.client.search_stops("broken")

    def test_bad_configuration_and_input_fail_before_network_access(self):
        for changes in (
            {"origin": None}, {"timeout_seconds": 0}, {"cache_ttl_seconds": -1},
            {"cache_ttl_seconds": float("nan")}, {"requests_per_minute": 51},
            {"requests_per_minute": True}, {"language": "xx"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                transport.TransportClient({**CONFIG, **changes})
        with patch.object(transport.httpx, "stream") as fetch:
            for query, limit in (("", 5), ("a" * 161, 5), ("HB", 0), ("HB", True), ("HB\x00", 1)):
                with self.subTest(query=query, limit=limit), self.assertRaises(ValueError):
                    self.client.search_stops(query, limit)
            fetch.assert_not_called()

    def test_departures_are_fixed_origin_with_predictions_and_situations(self):
        with patch.object(transport.httpx, "stream", return_value=nullcontext(xml_response("StopEvent", DEPARTURE + SITUATION))) as fetch:
            result = self.client.departures()
        request = ET.fromstring(fetch.call_args.kwargs["content"])
        self.assertEqual(transport.text(request, ".//ojp:Location/ojp:PlaceRef/ojp:StopPlaceRef"), CONFIG["origin"]["stop_id"])
        self.assertEqual(transport.text(request, ".//ojp:StopEventType"), "departure")
        self.assertEqual(transport.text(request, ".//ojp:UseRealtimeData"), "explanatory")
        departure = result["departures"][0]
        self.assertEqual(departure["service"]["line"], "S10")
        self.assertEqual(departure["service"]["mode"], "rail")
        self.assertFalse(departure["service"]["cancelled"])
        self.assertEqual(departure["call"]["departure"]["delay_seconds"], 120)
        self.assertEqual(departure["call"]["departure"]["estimated_local"], "2026-09-24T13:47:00+02:00")
        self.assertEqual(departure["call"]["planned_platform"], "A")
        self.assertEqual(departure["call"]["estimated_platform"], "B")
        self.assertEqual(result["situations"][0]["summary"], ["Service disruption"])

    def test_timetable_only_missing_flags_and_early_estimates_are_not_invented(self):
        timetable = DEPARTURE.replace("<EstimatedTime>2026-09-24T11:47:00Z</EstimatedTime>", "").replace("<Cancelled>false</Cancelled>", "")
        with patch.object(transport.httpx, "stream", return_value=nullcontext(xml_response("StopEvent", timetable))):
            departure = self.client.departures()["departures"][0]
        self.assertIsNone(departure["service"]["cancelled"])
        self.assertFalse(departure["call"]["departure"]["has_realtime"])
        self.assertIsNone(departure["call"]["departure"]["delay_seconds"])
        self.assertIsNone(departure["call"]["departure"]["estimated_at"])
        early = DEPARTURE.replace("11:47:00Z", "11:44:00Z")
        with patch.object(transport.httpx, "stream", return_value=nullcontext(xml_response("StopEvent", early))):
            self.assertEqual(self.client.departures(force_refresh=True)["departures"][0]["call"]["departure"]["delay_seconds"], -60)

    def test_cached_now_preserves_original_request_time_and_force_refresh_bypasses(self):
        with patch.object(transport.httpx, "stream", side_effect=lambda *args, **kwargs: nullcontext(xml_response("StopEvent", DEPARTURE))) as fetch:
            first = self.client.departures()
            self.clock.return_value = NOW + timedelta(seconds=5)
            second = self.client.departures()
            self.assertEqual(first["requested_at"], second["requested_at"])
            self.assertEqual(first["retrieved_at"], second["retrieved_at"])
            self.assertTrue(second["cached"])
            fresh = self.client.departures(force_refresh=True)
            self.assertFalse(fresh["cached"])
            self.assertNotEqual(first["requested_at"], fresh["requested_at"])
            self.assertEqual(fetch.call_count, 2)

    def test_depart_after_and_arrive_by_target_correct_xml_endpoint(self):
        for arrive_by in (False, True):
            with self.subTest(arrive_by=arrive_by), patch.object(transport.httpx, "stream", return_value=nullcontext(xml_response("Trip", TRIP))) as fetch:
                result = self.client.journeys("ch:1:sloid:3000", "2026-09-24T14:00:00+02:00", arrive_by, max_transfers=0)
            request = ET.fromstring(fetch.call_args.kwargs["content"])
            endpoint, other = ("Destination", "Origin") if arrive_by else ("Origin", "Destination")
            self.assertEqual(transport.text(request, f".//ojp:{endpoint}/ojp:DepArrTime"), "2026-09-24T12:00:00+00:00")
            self.assertIsNone(transport.text(request, f".//ojp:{other}/ojp:DepArrTime"))
            self.assertEqual(transport.text(request, ".//ojp:Origin/ojp:PlaceRef/ojp:StopPlaceRef"), CONFIG["origin"]["stop_id"])
            self.assertEqual(transport.text(request, ".//ojp:TransferLimit"), "0")
            self.assertEqual(transport.text(request, ".//ojp:NumberOfResultsBefore"), "3" if arrive_by else "0")
            self.assertEqual(transport.text(request, ".//ojp:NumberOfResultsAfter"), "0" if arrive_by else "3")
            self.assertEqual([node.tag.rsplit("}", 1)[-1] for node in request.find(".//ojp:Params", transport.NS)], [
                "NumberOfResultsBefore", "NumberOfResultsAfter", "UseRealtimeData", "TransferLimit",
                "IncludeSituationsContext", "IncludeIntermediateStops",
            ])
            trip = result["trips"][0]
            self.assertEqual(trip["duration"], "PT15M")
            self.assertEqual(trip["transfers"], 0)
            self.assertEqual([leg["type"] for leg in trip["legs"]], ["public_transport", "transfer", "continuous"])
            self.assertEqual(trip["legs"][2]["distance_meters"], 120)
            self.assertFalse(result["walking_time_from_venue_included"])

    def test_cancelled_and_unboardable_journeys_are_flagged(self):
        for changed in (
            TRIP.replace("<Cancelled>false</Cancelled>", "<Cancelled>true</Cancelled>"),
            TRIP.replace("<LegBoard>", "<LegBoard><NoBoardingAtStop>true</NoBoardingAtStop>"),
            TRIP.replace("<LegAlight>", "<LegAlight><NotServicedStop>true</NotServicedStop>"),
        ):
            with patch.object(transport.httpx, "stream", return_value=nullcontext(xml_response("Trip", changed))):
                self.assertTrue(self.client.journeys("ch:1:sloid:3000", force_refresh=True)["trips"][0]["warnings"])

    def test_unknown_and_empty_trip_or_departure_responses_are_distinct(self):
        for kind, body in (("Trip", "<TripResult/>"), ("StopEvent", "<StopEventResult/>")):
            with self.subTest(kind=kind), patch.object(transport.httpx, "stream", return_value=nullcontext(xml_response(kind, body))):
                with self.assertRaisesRegex(RuntimeError, "incomplete"):
                    self.client.journeys("ch:1:sloid:3000") if kind == "Trip" else self.client.departures()
        for kind, field in (("Trip", "trips"), ("StopEvent", "departures")):
            with self.subTest(kind=kind), patch.object(transport.httpx, "stream", return_value=nullcontext(xml_response(kind, ""))):
                result = self.client.journeys("ch:1:sloid:3000", force_refresh=True) if kind == "Trip" else self.client.departures(force_refresh=True)
                self.assertEqual(result[field], [])

    def test_expired_cache_never_masks_an_outage(self):
        with (
            patch.object(transport.httpx, "stream", return_value=nullcontext(xml_response("StopEvent", DEPARTURE))) as fetch,
            patch.object(transport.time, "monotonic", return_value=100) as clock,
        ):
            self.client.departures()
            clock.return_value = 131
            fetch.side_effect = httpx.ConnectError("offline")
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                self.client.departures()

    def test_timezones_and_journey_arguments_are_validated_before_fetching(self):
        self.assertEqual(transport.local_time("2026-01-24T11:42:00Z"), "2026-01-24T12:42:00+01:00")
        with patch.object(transport.httpx, "stream") as fetch:
            for value in ("tomorrow", "2026-09-24", "2026-09-24T12:00:00", "", 123):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    self.client.departures(value)
            for options in ({"arrive_by": True}, {"arrive_by": "yes"}, {"max_transfers": -1}, {"limit": 7}):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    self.client.journeys("ch:1:sloid:3000", **options)
            with self.assertRaisesRegex(ValueError, "exact stop_id"):
                self.client.journeys("Zurich HB")
            fetch.assert_not_called()

    def test_known_nonfatal_source_warnings_preserve_usable_results(self):
        warning = "<ErrorCondition><TripProblemType>TRIP_REALTIMEINCOMPLETE</TripProblemType><Details><Text>No prediction for one leg</Text></Details></ErrorCondition>"
        with patch.object(transport.httpx, "stream", return_value=nullcontext(xml_response("Trip", warning + TRIP))):
            result = self.client.journeys("ch:1:sloid:3000")
        self.assertEqual(len(result["trips"]), 1)
        self.assertEqual(result["source_warnings"][0]["code"], "TRIP_REALTIMEINCOMPLETE")
        for body in ("<ErrorCondition/>", warning.replace("TRIP_REALTIMEINCOMPLETE", "TRIP_ORIGINUNKNOWN")):
            with self.subTest(body=body), self.assertRaises(RuntimeError):
                transport.parse_delivery(xml_response("Trip", body).content, "Trip")

    def test_missing_invalid_and_future_source_timestamps_are_not_fresh(self):
        for value, expected in ((None, "missing"), ("bad", "invalid"),
                                ("2026-09-24T11:43:00Z", "future"), ("2026-09-24T11:42:00", "invalid")):
            with self.subTest(value=value):
                delivery = transport.parse_delivery(xml_response().content, "LocationInformation")
                timestamp = delivery.find("siri:ResponseTimestamp", transport.NS)
                timestamp.text = value
                result = transport.response_metadata(delivery, NOW.isoformat(), True)
                self.assertEqual(result["response_freshness"], expected)
                self.assertTrue(result["warnings"])

    def test_malformed_tokens_are_rejected_without_disclosing_them(self):
        for token in ("sensitive\nvalue", "sensitive value", "sensitive\u00e9value"):
            with self.subTest(), patch.dict(transport.os.environ, {"OJP_API_TOKEN": token}):
                with self.assertRaises(ValueError) as caught:
                    transport.TransportClient(CONFIG)
                self.assertNotIn("sensitive", str(caught.exception))
        with patch.dict(transport.os.environ, {"OJP_API_TOKEN": "Bearer test-token"}):
            self.assertEqual(transport.TransportClient(CONFIG).token, "test-token")

    def test_registered_tools_expose_no_origin_override(self):
        import inspect

        registry = ToolRegistry()
        transport.register(registry, CONFIG)
        self.assertEqual(set(registry.tools), {"get_transport_origin", "search_transport_stops", "get_departures", "plan_journey"})
        for name in ("get_departures", "plan_journey"):
            self.assertNotIn("origin", inspect.signature(registry.tools[name]).parameters)
            self.assertNotIn("origin_stop_id", inspect.signature(registry.tools[name]).parameters)

    def test_mcp_tools_preserve_actionable_errors_without_credentials(self):
        registry = ToolRegistry()
        transport.register(registry, CONFIG)
        for name, arguments, message in (
            ("search_transport_stops", {"query": ""}, "query"),
            ("get_departures", {"at": "tomorrow"}, "ISO 8601"),
            ("plan_journey", {"destination_stop_id": "Zurich HB"}, "exact stop_id"),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(transport.ToolError, message):
                registry.tools[name](**arguments)
        with patch.object(transport.httpx, "stream", return_value=nullcontext(xml_response(status=401))):
            with self.assertRaisesRegex(transport.ToolError, "authentication failed") as caught:
                registry.tools["get_departures"]()
            self.assertNotIn("test-token", str(caught.exception))


if __name__ == "__main__":
    unittest.main()