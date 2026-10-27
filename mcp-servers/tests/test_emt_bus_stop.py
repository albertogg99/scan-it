import asyncio
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx
import yaml


ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = ROOT / "servers" / "emt-bus-stop"
FIXTURES = ROOT / "tests" / "fixtures" / "emt_bus_stop"
spec = importlib.util.spec_from_file_location("emt_bus_stop", SERVER_DIR / "tools" / "emt_bus_stop.py")
emt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(emt)
CONFIG = yaml.safe_load((SERVER_DIR / "config" / "tools_config.yaml").read_text(encoding="utf-8"))["emt_bus_stop"]
NOW = datetime(2026, 10, 22, 8, 15, 0, tzinfo=timezone.utc)
SECRETS = {
    "EMT_CLIENT_ID": "client-id-secret", "EMT_PASSKEY": "passkey-secret",
    "EMT_EMAIL": "", "EMT_PASSWORD": "",
}
TOOLS = {
    "get_stop_info", "get_arrivals", "get_line_info", "get_line_stops", "get_line_timetable", "get_line_incidents",
    "plan_journey", "search_connected_stops",
}


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def response(payload, status: int = 200, text: str | None = None) -> httpx.Response:
    request = httpx.Request("GET", CONFIG["base_url"])
    if text is not None:
        return httpx.Response(status, text=text, headers={"content-type": "text/html"}, request=request)
    return httpx.Response(status, json=payload, request=request)


class FakeEmt:
    """Routes upstream calls by URL path; records calls."""

    def __init__(self):
        self.calls = []
        self.login = lambda: response(fixture("login_ok"))
        self.arrivals = lambda: response(fixture("arrivals"))
        self.routes = {
            "/v1/transport/busemtmad/stops/72/detail/": lambda: response(fixture("stop_detail")),
            "/v1/transport/busemtmad/lines/005/info/20261022/": lambda: response(fixture("line_info")),
            "/v1/transport/busemtmad/lines/005/stops/1/": lambda: response(fixture("line_stops")),
            "/v1/transport/busemtmad/lines/005/timetable/": lambda: response(fixture("timetable")),
            "/v1/transport/busemtmad/calendar/20261022/20261022/": lambda: response(fixture("calendar")),
            "/v1/transport/busemtmad/lines/incidents/all/": lambda: response(fixture("incidents")),
            "/v1/transport/busemtmad/lines/incidents/005/": lambda: response(fixture("incidents")),
        }

    def __call__(self, method, url, **kwargs):
        path = url.removeprefix(CONFIG["base_url"])
        self.calls.append({"method": method, "path": path, **kwargs})
        if path == "/v3/mobilitylabs/user/login/":
            return nullcontext(self.login())
        if "/arrives/" in path:
            return nullcontext(self.arrivals())
        return nullcontext(self.routes[path]())

    def paths(self, kind=None):
        return [c["path"] for c in self.calls if kind is None or kind in c["path"]]


class ToolRegistry:
    def __init__(self):
        self.tools, self.options = {}, {}

    def tool(self, **options):
        def decorate(function):
            name = options.get("name", function.__name__)
            self.tools[name], self.options[name] = function, options
            return function
        return decorate


class EmtTestCase(unittest.TestCase):
    env = SECRETS

    def setUp(self):
        for patcher in (
            patch.dict(emt.os.environ, self.env),
            patch.object(emt, "utc_now", return_value=NOW),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.clock = [1000.0]
        monotonic = patch.object(emt.time, "monotonic", side_effect=lambda: self.clock[0])
        monotonic.start()
        self.addCleanup(monotonic.stop)
        self.fake = FakeEmt()
        stream = patch.object(emt.httpx, "stream", side_effect=self.fake)
        stream.start()
        self.addCleanup(stream.stop)
        self.client = emt.EmtClient(deepcopy(CONFIG))


class ConfigurationTests(EmtTestCase):
    def test_registers_read_only_tools_with_explicit_annotations(self):
        registry = ToolRegistry()
        emt.register(registry, CONFIG)
        self.assertEqual(set(registry.tools), TOOLS)
        for name, options in registry.options.items():
            self.assertRegex(name, r"^[a-zA-Z0-9_-]{1,64}$")
            annotations = options["annotations"]
            self.assertIs(annotations.read_only_hint, True)
            self.assertIs(annotations.destructive_hint, False)
            self.assertIs(annotations.idempotent_hint, True)
            self.assertIs(annotations.open_world_hint, True)

    def test_loads_through_real_server_with_instructions_and_annotations(self):
        from mcp.server.mcpserver import MCPServer

        instructions = (SERVER_DIR / "config" / "server_instructions.md").read_text(encoding="utf-8")
        self.assertIn("EMT Madrid MobilityLabs", instructions)
        self.assertIn("999999", instructions)
        server = MCPServer("emt-test", instructions=instructions.strip())
        emt.register(server, CONFIG)
        tools = asyncio.run(server.list_tools())
        self.assertEqual({tool.name for tool in tools}, TOOLS)
        for tool in tools:
            self.assertTrue(tool.annotations.read_only_hint)
            self.assertFalse(tool.annotations.destructive_hint)
            self.assertTrue(tool.annotations.idempotent_hint)
            self.assertTrue(tool.annotations.open_world_hint)

    def test_config_matches_expected_stop_and_limits(self):
        self.assertEqual(CONFIG["stop"]["id"], "72")
        self.assertEqual(CONFIG["arrivals_cache_ttl_seconds"], 20)
        self.assertEqual(CONFIG["attribution"], "Datos: EMT Madrid MobilityLabs")

    def test_bad_configuration_and_credentials_are_rejected_without_echo(self):
        for changes in ({"base_url": "http://x"}, {"timeout_seconds": 0}, {"requests_per_minute": True},
                        {"culture": "FR"}, {"stop": None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                emt.EmtClient({**CONFIG, **changes})
        with patch.dict(emt.os.environ, {"EMT_PASSKEY": "bad token"}):
            with self.assertRaises(ValueError) as caught:
                emt.EmtClient(CONFIG)
            self.assertNotIn("bad token", str(caught.exception))
            self.assertIn("EMT_PASSKEY", str(caught.exception))


class AuthenticationTests(EmtTestCase):
    def test_client_id_and_passkey_are_preferred_over_email_password(self):
        with patch.dict(emt.os.environ, {"EMT_EMAIL": "a@b.es", "EMT_PASSWORD": "pw"}):
            client = emt.EmtClient(deepcopy(CONFIG))
            client.get_stop_info()
        login = self.fake.calls[0]
        self.assertEqual(login["path"], "/v3/mobilitylabs/user/login/")
        self.assertEqual(login["headers"]["X-ClientId"], "client-id-secret")
        self.assertEqual(login["headers"]["passKey"], "passkey-secret")
        self.assertNotIn("email", login["headers"])
        self.assertEqual(self.fake.calls[1]["headers"]["accessToken"], "FIXTURE-ACCESS-TOKEN-0001")

    def test_email_password_fallback(self):
        with patch.dict(emt.os.environ, {"EMT_CLIENT_ID": "", "EMT_PASSKEY": "", "EMT_EMAIL": "a@b.es", "EMT_PASSWORD": "pw"}):
            emt.EmtClient(deepcopy(CONFIG)).get_stop_info()
        headers = self.fake.calls[0]["headers"]
        self.assertEqual((headers["email"], headers["password"]), ("a@b.es", "pw"))
        self.assertNotIn("X-ClientId", headers)

    def test_missing_credentials_raise_tool_error_not_empty_result(self):
        with patch.dict(emt.os.environ, {k: "" for k in SECRETS}):
            registry = ToolRegistry()
            emt.register(registry, CONFIG)
            for name, args in (("get_arrivals", ()), ("get_stop_info", ()), ("get_line_incidents", ())):
                with self.subTest(name=name), self.assertRaisesRegex(emt.ToolError, "credentials are not configured"):
                    registry.tools[name](*args)
        self.assertEqual(self.fake.calls, [])

    def test_token_is_cached_and_refreshed_before_expiry(self):
        self.client.get_stop_info()
        self.client.get_line_incidents()
        self.assertEqual(self.fake.paths("login"), ["/v3/mobilitylabs/user/login/"])
        self.clock[0] += 86400 - 30
        self.client.get_line_incidents(None)
        self.client.get_stop_info()
        self.assertEqual(len(self.fake.paths("login")), 2)

    def test_code_80_and_http_401_trigger_one_relogin_and_one_retry(self):
        path = "/v1/transport/busemtmad/lines/incidents/all/"
        original = self.fake.routes[path]
        for expired in (lambda: response(fixture("token_expired")), lambda: response(None, 401, text="denied")):
            self.fake.calls.clear()
            self.client.token, self.client.cache = None, type(self.client.cache)()
            answers = [expired, original]
            self.fake.routes[path] = lambda: answers.pop(0)()
            result = self.client.get_line_incidents()
            self.assertEqual(len(result["incidents"]), 2)
            self.assertEqual(len(self.fake.paths("login")), 2)
            self.assertEqual(len(self.fake.paths("incidents")), 2)

    def test_persistent_expiry_stops_after_one_retry(self):
        self.fake.routes["/v1/transport/busemtmad/lines/incidents/all/"] = lambda: response(fixture("token_expired"))
        with self.assertRaisesRegex(RuntimeError, "rejected the access token"):
            self.client.get_line_incidents()
        self.assertEqual(len(self.fake.paths("incidents")), 2)

    def test_login_failure_never_leaks_credentials(self):
        self.fake.login = lambda: response(fixture("login_error"))
        with self.assertRaisesRegex(RuntimeError, "login failed") as caught:
            self.client.get_arrivals()
        for secret in ("client-id-secret", "passkey-secret"):
            self.assertNotIn(secret, str(caught.exception))

    def test_credentials_and_token_never_appear_in_outputs(self):
        outputs = [
            self.client.get_stop_info(), self.client.get_arrivals(), self.client.get_line_info("5"),
            self.client.get_line_stops("5"), self.client.get_line_timetable("5"), self.client.get_line_incidents(),
        ]
        text = json.dumps(outputs, ensure_ascii=False)
        for secret in ("client-id-secret", "passkey-secret", "FIXTURE-ACCESS-TOKEN-0001"):
            self.assertNotIn(secret, text)


class ToolBehaviourTests(EmtTestCase):
    def connected_routes(self):
        lists = {
            ("005", 1): [("50", "Sol-Sevilla"), ("72", "Cibeles"), ("72", "Cibeles"), ("74", "Recoletos"),
                         ("80", "Colón"), ("90", "Plaza Castilla")],
            ("005", 2): [("90", "Plaza Castilla"), ("50", "Sol-Sevilla")],
            ("027", 1): [("72", "Cibeles"), ("80", "Colón"), ("81", "Plaza de Colón"), ("90", "Plaza Castilla")],
        }
        for code in ("005", "027", "053", "363", "501"):
            for direction in (1, 2):
                stops = [{"stop": i, "name": n} for i, n in lists.get((code, direction), [])]
                payload = {"code": "00", "data": [{"label": code.lstrip("0"), "stops": stops}]}
                if (code, direction) == ("027", 2):
                    payload = {"code": "90", "description": "unavailable"}
                self.fake.routes[f"/v1/transport/busemtmad/lines/{code}/stops/{direction}/"] = (
                    lambda payload=payload: response(payload)
                )

    def test_search_connected_stops_ranks_similar_reachable_names(self):
        self.connected_routes()
        result = self.client.search_connected_stops("colon")
        self.assertEqual(result["stops"][:2], ["Colón", "Plaza de Colón"])
        self.assertNotIn("Sol-Sevilla", self.client.search_connected_stops("sol sevilla")["stops"])
        self.assertEqual(self.client.search_connected_stops("plaza castila")["stops"][0], "Plaza Castilla")
        self.assertEqual(self.client.search_connected_stops("zzzz")["stops"], [])
        self.assertTrue(any("027" in w for w in result["warnings"]))
        for bad in ("  ", 5, "x" * 121):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.client.search_connected_stops(bad)

    def test_journey_lists_lines_direction_and_stop_count(self):
        self.connected_routes()
        result = self.client.plan_journey("colón")
        self.assertEqual(result["destination"], "Colón")
        self.assertEqual(
            [(o["line"], o["direction"], o["toward"], o["stops"]) for o in result["options"]],
            [("27", 1, "Plaza Castilla", 1), ("5", 1, "Plaza Castilla", 2)],
        )
        self.assertTrue(self.client.plan_journey("COLON")["cached"])

    def test_journey_rejects_unconnected_stop_with_suggestions(self):
        self.connected_routes()
        with self.assertRaisesRegex(ValueError, "not a stop reachable.*Closest connected stops: Plaza de Colón"):
            self.client.plan_journey("Plaza Colon")
        with self.assertRaisesRegex(ValueError, "not a stop reachable"):
            self.client.plan_journey("Sol-Sevilla")
        with self.assertRaises(ValueError):
            self.client.plan_journey("")

    def test_stop_info_lists_lines_with_schedule(self):
        result = self.client.get_stop_info()
        self.assertEqual(result["stop"], {"id": "72", "name": "Cibeles - Casa de Am\u00e9rica"})
        self.assertEqual(result["coordinates"], {"latitude": 40.42031, "longitude": -3.69214})
        first = result["lines"][0]
        self.assertEqual((first["label"], first["line_code"], first["day_type"]), ("5", "005", "LA"))
        self.assertEqual((first["first_bus"], first["last_bus"]), ("06:00", "23:30"))
        self.assertEqual((first["min_frequency_minutes"], first["max_frequency_minutes"]), (6, 12))
        self.assertEqual(result["attribution"], "Datos: EMT Madrid MobilityLabs")
        self.assertEqual(result["timezone"], "Europe/Madrid")
        self.assertFalse(result["cached"])

    def test_arrivals_request_body_and_path(self):
        self.client.get_arrivals()
        call = self.fake.calls[-1]
        self.assertEqual((call["method"], call["path"]), ("POST", "/v2/transport/busemtmad/stops/72/arrives/"))
        self.assertEqual(call["json"], {
            "cultureInfo": "ES", "Text_StopRequired_YN": "Y", "Text_EstimationsRequired_YN": "Y",
            "Text_IncidencesRequired_YN": "Y", "DateTime_Referenced_Incidencies_YYYYMMDD": "20261022",
        })
        self.client.get_arrivals("n1", include_incidents=False)
        call = self.fake.calls[-1]
        self.assertEqual(call["path"], "/v2/transport/busemtmad/stops/72/arrives/501/")
        self.assertEqual(call["json"]["Text_IncidencesRequired_YN"], "N")

    def test_arrivals_sorting_horizon_html_stripping_and_limit(self):
        result = self.client.get_arrivals()
        self.assertEqual([a["estimated_seconds"] for a in result["arrivals"]], [85, 150, 600, None])
        self.assertEqual([a["estimated_minutes"] for a in result["arrivals"]], [1, 3, 10, None])
        beyond = result["arrivals"][-1]
        self.assertTrue(beyond["beyond_horizon"])
        self.assertIsNone(beyond["estimated_minutes"])
        self.assertIsNone(beyond["estimated_arrival_local"])
        self.assertFalse(result["arrivals"][0]["beyond_horizon"])
        self.assertEqual(result["arrivals"][0]["estimated_arrival_local"], "2026-10-22T10:16:25+02:00")
        self.assertEqual(result["arrivals"][0]["bus"], "1235")
        self.assertEqual(result["arrivals"][0]["distance_m"], 300)
        incident = result["incidents"][0]
        self.assertNotIn("<", incident["description"])
        self.assertNotIn("&nbsp;", incident["description"])
        self.assertIn("Recoletos", incident["description"])
        self.assertEqual(incident["more_info_url"], "https://www.emtmadrid.es/Home")
        limited = self.client.get_arrivals(limit=2)
        self.assertEqual(len(limited["arrivals"]), 2)
        self.assertTrue(limited["truncated"])
        self.assertEqual(limited["total_arrivals"], 4)

    def test_long_incident_text_and_incident_count_are_bounded(self):
        payload = fixture("arrivals")
        item = payload["data"][0]["Incident"]["ListaIncident"]["data"][0]
        item["description"] = "<p>" + "x" * 2000 + "</p>"
        payload["data"][0]["Incident"]["ListaIncident"]["data"] = [deepcopy(item) for _ in range(9)]
        self.fake.arrivals = lambda: response(payload)
        incidents = self.client.get_arrivals()["incidents"]
        self.assertEqual(len(incidents), 5)
        self.assertLessEqual(len(incidents[0]["description"]), 400)

    def test_empty_arrivals_are_reported_as_empty_with_metadata(self):
        self.fake.arrivals = lambda: response({"code": "01", "description": "No data", "datetime": "2026-10-22T10:15:00", "data": []})
        result = self.client.get_arrivals(include_incidents=False)
        self.assertEqual(result["arrivals"], [])
        self.assertNotIn("incidents", result)

    def test_freshness_states(self):
        cases = {
            "2026-10-22T10:15:00.000000": "fresh",
            "2026-10-22T10:10:00": "stale",
            "2026-10-22T11:15:00": "future",
            "garbage": "invalid",
            None: "missing",
            "2026-10-22T08:14:00+00:00": "fresh",
        }
        for timestamp, expected in cases.items():
            with self.subTest(timestamp=timestamp):
                payload = fixture("arrivals")
                if timestamp is None:
                    del payload["datetime"]
                else:
                    payload["datetime"] = timestamp
                self.fake.arrivals = lambda payload=payload: response(payload)
                result = self.client.get_arrivals(force_refresh=True)
                self.assertEqual(result["response_freshness"], expected)
                self.assertEqual(bool(result["warnings"]), expected != "fresh")

    def test_arrivals_cache_hit_expiry_and_force_refresh(self):
        first = self.client.get_arrivals()
        self.assertFalse(first["cached"])
        second = self.client.get_arrivals()
        self.assertTrue(second["cached"])
        self.assertEqual(second["retrieved_at"], first["retrieved_at"])
        self.assertEqual(len(self.fake.paths("arrives")), 1)
        self.client.get_arrivals(force_refresh=True)
        self.assertEqual(len(self.fake.paths("arrives")), 2)
        self.clock[0] += 21
        self.assertFalse(self.client.get_arrivals()["cached"])
        self.assertEqual(len(self.fake.paths("arrives")), 3)

    def test_cached_results_are_isolated_copies(self):
        self.client.get_arrivals()["arrivals"].clear()
        self.assertEqual(len(self.client.get_arrivals()["arrivals"]), 4)

    def test_stop_detail_cached_for_static_ttl(self):
        self.client.get_stop_info()
        self.assertTrue(self.client.get_stop_info()["cached"])
        self.clock[0] += 3601
        self.assertFalse(self.client.get_stop_info()["cached"])

    def test_line_label_resolves_to_internal_code_via_stop_detail(self):
        self.client.get_line_info("5", "2026-10-22")
        self.assertIn("/v1/transport/busemtmad/lines/005/info/20261022/", self.fake.paths())
        self.client.get_arrivals("c03")
        self.assertIn("/v2/transport/busemtmad/stops/72/arrives/363/", self.fake.paths())
        self.assertEqual(len(self.fake.paths("detail")), 1)

    def test_unknown_line_label_is_passed_as_given(self):
        self.fake.routes["/v1/transport/busemtmad/lines/133/timetable/"] = lambda: response(fixture("timetable"))
        self.client.get_line_timetable("133")
        self.assertIn("/v1/transport/busemtmad/lines/133/timetable/", self.fake.paths())

    def test_line_info_includes_calendar_day_type_and_strike_flag(self):
        result = self.client.get_line_info("5")
        self.assertEqual(result["date"], "2026-10-22")
        self.assertEqual(result["calendar_day_type"], "LA")
        self.assertIs(result["is_strike_day"], False)
        self.assertEqual(result["name_a"], "Puerta del Sol/Sevilla")
        labor = result["timetable"][0]
        self.assertEqual(labor["day_type"], "LABORABLE")
        self.assertEqual(labor["direction_1"]["first_bus"], "06:00")
        self.assertEqual(labor["direction_2"]["max_frequency_minutes"], 12)
        self.assertEqual(labor["direction_1"]["frequency_text"], "6 a 12 minutos")

    def test_strike_day_is_flagged(self):
        payload = fixture("calendar")
        payload["data"][0]["strike"] = "S"
        self.fake.routes["/v1/transport/busemtmad/calendar/20261022/20261022/"] = lambda: response(payload)
        self.assertIs(self.client.get_line_info("5", "2026-10-22")["is_strike_day"], True)

    def test_line_stops_marks_this_stop_and_bounds_output(self):
        result = self.client.get_line_stops("5", 1)
        self.assertEqual(result["this_stop_position"], 2)
        self.assertEqual([s["is_this_stop"] for s in result["stops"]], [False, True, False])
        self.assertEqual(result["stops"][1]["coordinates"], {"latitude": 40.42031, "longitude": -3.69214})
        limited = self.client.get_line_stops("5", 1, limit=1)
        self.assertEqual(len(limited["stops"]), 1)
        self.assertTrue(limited["truncated"])
        self.assertEqual(limited["this_stop_position"], 2)

    def test_line_timetable_and_incidents(self):
        timetable = self.client.get_line_timetable("5")["timetable"]
        self.assertEqual([row["day_type"] for row in timetable], ["LA", "SA", "FE"])
        self.assertEqual(timetable[0]["direction_a"], {"first_service": "06:00", "last_service": "23:30"})
        incidents = self.client.get_line_incidents()
        self.assertEqual(incidents["incidents"][0]["lines"], "27")
        self.assertNotIn("<", incidents["incidents"][0]["description"])
        self.assertEqual(incidents["incidents"][0]["effect"], "DETOUR")
        self.assertIsNone(incidents["incidents"][1]["more_info_url"])
        self.assertIn("/v1/transport/busemtmad/lines/incidents/all/", self.fake.paths())
        self.client.get_line_incidents("5")
        self.assertIn("/v1/transport/busemtmad/lines/incidents/005/", self.fake.paths())

    def test_incident_list_is_bounded(self):
        payload = fixture("incidents")
        payload["data"][0]["item"] = payload["data"][0]["item"] * 8
        self.fake.routes["/v1/transport/busemtmad/lines/incidents/all/"] = lambda: response(payload)
        result = self.client.get_line_incidents()
        self.assertEqual(len(result["incidents"]), 10)
        self.assertTrue(result["truncated"])

    def test_every_response_has_envelope_fields(self):
        for result in (self.client.get_stop_info(), self.client.get_arrivals(), self.client.get_line_info("5"),
                       self.client.get_line_stops("5"), self.client.get_line_timetable("5"), self.client.get_line_incidents()):
            for key in ("stop", "source_url", "attribution", "retrieved_at", "cached", "timezone"):
                self.assertIn(key, result)
            self.assertEqual(result["attribution"], "Datos: EMT Madrid MobilityLabs")
            self.assertTrue(result["retrieved_at"].endswith("+02:00"))


class ValidationAndFailureTests(EmtTestCase):
    def test_invalid_inputs_fail_before_any_network_access(self):
        calls = (
            lambda: self.client.get_arrivals("bad line"), lambda: self.client.get_arrivals("../x"),
            lambda: self.client.get_arrivals("TOOLONG"), lambda: self.client.get_arrivals(""),
            lambda: self.client.get_arrivals(limit=0), lambda: self.client.get_arrivals(limit=True),
            lambda: self.client.get_line_info("5", "22/10/2026"), lambda: self.client.get_line_info("5", "2026-02-30"),
            lambda: self.client.get_line_info("5", "2026-1-1"), lambda: self.client.get_line_stops("5", 3),
            lambda: self.client.get_line_stops("5", True), lambda: self.client.get_line_stops("5", 1, 0),
            lambda: self.client.get_line_timetable("5/1"), lambda: self.client.get_line_incidents("a b"),
            lambda: self.client.get_line_info(5),
        )
        for index, call in enumerate(calls):
            with self.subTest(index=index), self.assertRaises(ValueError):
                call()
        self.assertEqual(self.fake.calls, [])

    def test_registered_tools_convert_errors_to_tool_error(self):
        registry = ToolRegistry()
        emt.register(registry, CONFIG)
        with self.assertRaises(emt.ToolError):
            registry.tools["get_line_stops"]("5", 9)
        with self.assertRaises(emt.ToolError):
            registry.tools["get_line_info"]("5", "nope")

    def test_non_json_body_is_a_neutral_error(self):
        self.fake.arrivals = lambda: response(None, 200, text="<html>maintenance</html>")
        with self.assertRaisesRegex(RuntimeError, "non-JSON") as caught:
            self.client.get_arrivals()
        self.assertIn("does not mean no buses", str(caught.exception))
        self.assertFalse([k for k in self.client.cache if "arrives" in k[1]])

    def test_http_errors_and_upstream_codes_are_errors(self):
        for status in (500, 503, 429):
            self.fake.arrivals = lambda status=status: response({"code": "00"}, status)
            with self.subTest(status=status), self.assertRaises(RuntimeError):
                self.client.get_arrivals(force_refresh=True)
        self.fake.arrivals = lambda: response({"code": "90", "description": "<b>Bad</b> " + "x" * 500, "data": []})
        with self.assertRaisesRegex(RuntimeError, "code 90") as caught:
            self.client.get_arrivals(force_refresh=True)
        self.assertNotIn("<b>", str(caught.exception))
        self.assertLess(len(str(caught.exception)), 300)

    def test_timeout_and_network_errors_are_neutral_tool_errors(self):
        registry = ToolRegistry()
        emt.register(registry, CONFIG)
        for error in (httpx.ReadTimeout("client-id-secret"), httpx.ConnectError("passkey-secret")):
            with patch.object(emt.httpx, "stream", side_effect=error), self.subTest(error=type(error)):
                with self.assertRaisesRegex(emt.ToolError, "unavailable or timed out") as caught:
                    registry.tools["get_arrivals"]()
                self.assertNotIn("secret", str(caught.exception))

    def test_oversized_response_is_rejected(self):
        with patch.object(emt, "MAX_RESPONSE_BYTES", 10), self.assertRaisesRegex(RuntimeError, "size limit"):
            self.client.get_arrivals()

    def test_local_rate_limit_counts_upstream_requests(self):
        self.client.rate_limit = 2
        self.client.get_arrivals()
        with self.assertRaisesRegex(RuntimeError, "Local EMT request limit"):
            self.client.get_arrivals(force_refresh=True)
        self.clock[0] += 61
        self.client.get_arrivals(force_refresh=True)


if __name__ == "__main__":
    unittest.main()
