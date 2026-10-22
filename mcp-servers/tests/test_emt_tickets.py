import asyncio
from datetime import datetime
import importlib.util
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo

import yaml
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError


SERVER_DIR = Path(__file__).resolve().parents[1] / "servers" / "emt-tickets"
spec = importlib.util.spec_from_file_location("emt_tickets", SERVER_DIR / "tools" / "emt_tickets.py")
emt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(emt)
CONFIG = yaml.safe_load((SERVER_DIR / "config" / "tools_config.yaml").read_text(encoding="utf-8"))["emt_tickets"]
MADRID = ZoneInfo("Europe/Madrid")
NOW = datetime(2026, 10, 22, 10, 0, tzinfo=MADRID)
READ_ONLY_TOOLS = {"list_ticket_products", "quote_tickets", "list_airport_departures", "get_booking"}
BOOKING_TOOLS = {"purchase_ticket", "reserve_airport_seat", "cancel_booking"}


class Clock:
    def __init__(self, value=NOW):
        self.value = value

    def __call__(self):
        return self.value


class EmtTicketsTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.service = emt.EmtTickets(CONFIG, clock=self.clock)

    def roomy_trip(self, minimum=1, origin="cibeles", date=None):
        trips = self.service.list_departures(origin, date, seats=1, limit=20)["trips"]
        for trip in trips:
            if trip["seats_available"] >= minimum:
                return trip
        self.fail("no trip with enough seats")

    def test_tools_list_has_all_tools_with_annotations(self):
        server = MCPServer("emt", instructions="x")
        emt.register(server, CONFIG)
        tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
        self.assertEqual(set(tools), READ_ONLY_TOOLS | BOOKING_TOOLS)
        for name, tool in tools.items():
            annotations = tool.annotations
            self.assertIs(annotations.open_world_hint, False, name)
            if name in READ_ONLY_TOOLS:
                self.assertIs(annotations.read_only_hint, True, name)
                self.assertIs(annotations.destructive_hint, False, name)
                self.assertIs(annotations.idempotent_hint, True, name)
            else:
                self.assertIs(annotations.read_only_hint, False, name)
                self.assertIs(annotations.destructive_hint, True, name)

    def test_instructions_present(self):
        text = (SERVER_DIR / "config" / "server_instructions.md").read_text(encoding="utf-8")
        self.assertIn("card", text.lower())
        self.assertIn("restart", text.lower())

    def test_catalogue_matches_config(self):
        result = self.service.list_products()
        products = {p["product_id"]: p for p in result["products"]}
        self.assertEqual(len(products), 16)
        expected = {"single": 1.5, "metrobus_10": 7.3, "bicibus": 1.8, "airport_express": 5.0,
                    "tourist_A_1d": 10.3, "tourist_A_7d": 43.2, "tourist_T_1d": 15.4, "tourist_T_7d": 62.8}
        for product_id, price in expected.items():
            self.assertEqual(products[product_id]["price_eur"], price)
        for raw in CONFIG["products"]:
            self.assertEqual(products[raw["id"]]["price_eur"], float(raw["price"]))
        for product in products.values():
            self.assertTrue(product["source_url"].startswith("https://"))
        self.assertEqual({p["category"] for p in self.service.list_products("tourist")["products"]}, {"tourist"})
        with self.assertRaises(ToolError):
            self.service.list_products("nope")

    def test_quote_math_and_child_rules(self):
        quote = self.service.quote("tourist_A_3d", 2, 1)
        self.assertEqual(quote["total_eur"], 57.75)
        self.assertEqual([line["unit_price_eur"] for line in quote["items"]], [23.1, 11.55])
        self.assertEqual(self.service.quote("metrobus_10", 3)["total_eur"], 21.9)
        self.assertEqual(self.service.quote("tourist_T_1d", 1, 1)["total_eur"], 23.1)
        with self.assertRaises(ToolError):
            self.service.quote("single", 1, 1)
        with self.assertRaises(ToolError):
            self.service.quote("tourist_A_1d", 6, 5)
        with self.assertRaises(ToolError):
            self.service.quote("nope")

    def test_input_validation(self):
        for quantity in (0, 11, -1, True, "2"):
            with self.subTest(quantity=quantity), self.assertRaises(ToolError):
                self.service.quote("single", quantity)
        for name in ("x" * 61, "bad\nname", "tab\tname"):
            with self.subTest(name=name), self.assertRaises(ToolError):
                self.service.purchase("single", passenger_name=name)
        self.assertEqual(self.service.purchase("single", passenger_name="  Ana  ")["passenger_name"], "Ana")
        for locator in ("", "EMT-12345", "EMT-AAAAAA0", "XYZ-ABCDEF", "EMT-ABCDE1", None):
            with self.subTest(locator=locator), self.assertRaises(ToolError):
                self.service.get_booking(locator)
        trip = self.roomy_trip()
        for seats in (0, 5):
            with self.subTest(seats=seats), self.assertRaises(ToolError):
                self.service.reserve(trip["trip_id"], seats)
        for trip_id in ("", "203-20261022-1008", "bad", "203-20261022-1007-CIB"):
            with self.subTest(trip_id=trip_id), self.assertRaises(ToolError):
                self.service.reserve(trip_id)
        with self.assertRaises(ToolError):
            self.service.reserve(trip["trip_id"], 1, "T3")

    def test_purchase_roundtrip(self):
        order = self.service.purchase("tourist_A_2d", 1, 1, "Ana")
        self.assertRegex(order["locator"], r"^EMT-[A-HJ-NP-Z2-9]{6}$")
        self.assertEqual(order["total_eur"], 26.25)
        self.assertEqual(order["status"], "confirmed")
        self.assertEqual(order["valid_from"], NOW.isoformat())
        self.assertEqual(order["valid_until"], "2026-10-23T23:59:00+02:00")
        self.assertEqual(order["qr_payload"], f"EMTDEMO|ORDER|{order['locator']}|tourist_A_2d|2")
        self.assertEqual(self.service.get_booking(order["locator"].lower()), order)
        self.assertIsNone(self.service.purchase("single")["valid_until"])
        with self.assertRaises(ToolError):
            self.service.get_booking("EMT-AAAAAA")

    def test_reserve_reduces_seats_and_cancel_releases(self):
        trip = self.roomy_trip(2)
        before = trip["seats_available"]
        booking = self.service.reserve(trip["trip_id"], 2, "T2", "Ana")
        self.assertEqual(booking["total_eur"], 10.0)
        self.assertEqual(booking["boarding_stop"]["stop_id"], "72")
        self.assertEqual(booking["qr_payload"], f"EMTDEMO|SEAT|{booking['locator']}|{trip['trip_id']}|T2|2")
        self.assertEqual(booking["departure"], trip["departure"])
        self.assertEqual(booking["arrival"], trip["arrivals"]["T2"])
        self.assertEqual(self.service.get_booking(booking["locator"])["type"], "seat_booking")
        after = self.service.list_departures("cibeles", seats=1, limit=20)["trips"]
        self.assertEqual(next(t for t in after if t["trip_id"] == trip["trip_id"])["seats_available"], before - 2)
        cancelled = self.service.cancel(booking["locator"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["refund_eur"], 10.0)
        again = self.service.list_departures("cibeles", seats=1, limit=20)["trips"]
        self.assertEqual(next(t for t in again if t["trip_id"] == trip["trip_id"])["seats_available"], before)
        with self.assertRaisesRegex(ToolError, "already cancelled"):
            self.service.cancel(booking["locator"])

    def test_cancel_ticket_order(self):
        order = self.service.purchase("single")
        result = self.service.cancel(order["locator"])
        self.assertEqual((result["status"], result["refund_eur"]), ("cancelled", 1.5))

    def test_overbooking_rejected(self):
        trip = self.roomy_trip(1)
        for _ in range(trip["seats_available"]):
            self.service.reserve(trip["trip_id"], 1)
        with self.assertRaisesRegex(ToolError, "left"):
            self.service.reserve(trip["trip_id"], 1)

    def test_shared_seats_between_origins_of_same_run(self):
        ids = {"atocha": "203-20261023-1200-ATO", "cibeles": "203-20261023-1208-CIB"}
        def available(origin):
            trips = self.service.list_departures(origin, "2026-10-23", after="12:00", limit=2, seats=1)["trips"]
            return {t["trip_id"]: t["seats_available"] for t in trips}.get(ids[origin])
        before = available("atocha")
        if not before:
            self.skipTest("run full")
        self.service.reserve(ids["cibeles"])
        self.assertEqual(available("atocha"), before - 1)
        self.assertEqual(self.service.reserve(ids["atocha"])["boarding_stop"]["stop_id"], "5709")

    def test_departed_trip_rejected(self):
        trip = self.roomy_trip()
        self.clock.value = datetime.fromisoformat(trip["departure"])
        with self.assertRaisesRegex(ToolError, "departed"):
            self.service.reserve(trip["trip_id"])
        with self.assertRaisesRegex(ToolError, "departed"):
            self.service.reserve("203-20261021-1200-ATO")

    def test_departed_booking_cannot_be_cancelled(self):
        trip = self.roomy_trip()
        booking = self.service.reserve(trip["trip_id"])
        self.clock.value = datetime.fromisoformat(trip["departure"])
        with self.assertRaisesRegex(ToolError, "departed"):
            self.service.cancel(booking["locator"])

    def test_date_window(self):
        self.service.list_departures("cibeles", "2026-11-21")
        with self.assertRaisesRegex(ToolError, "30 days"):
            self.service.list_departures("cibeles", "2026-11-22")
        with self.assertRaisesRegex(ToolError, "past"):
            self.service.list_departures("cibeles", "2026-10-21")
        with self.assertRaises(ToolError):
            self.service.list_departures("cibeles", "22/10/2026")
        with self.assertRaisesRegex(ToolError, "30 days"):
            self.service.reserve("203-20261122-1000-ATO")

    def test_no_past_departures_listed(self):
        result = self.service.list_departures("cibeles", "2026-10-22", limit=20)
        self.assertTrue(result["trips"])
        for trip in result["trips"]:
            self.assertGreater(datetime.fromisoformat(trip["departure"]), NOW)
        self.assertEqual(result["trips"][0]["departure"], "2026-10-22T10:08:00+02:00")
        self.assertEqual(result["trips"][0]["arrivals"]["T4"], "2026-10-22T10:40:00+02:00")
        self.assertEqual(result["trips"][0]["trip_id"], "203-20261022-1008-CIB")

    def test_filters(self):
        result = self.service.list_departures("atocha", "2026-10-23", after="12:00", terminal="t1", limit=3)
        self.assertEqual([t["departure"][11:16] for t in result["trips"]], ["12:00", "12:15", "12:30"])
        self.assertEqual(set(result["trips"][0]["arrivals"]), {"T1"})
        self.assertEqual(result["trips"][0]["arrivals"]["T1"], "2026-10-23T12:30:00+02:00")
        self.assertTrue(result["trips"][0]["trip_id"].endswith("-ATO"))
        with self.assertRaises(ToolError):
            self.service.list_departures("atocha", after="25:00")
        with self.assertRaises(ToolError):
            self.service.list_departures("sol")
        with self.assertRaises(ToolError):
            self.service.list_departures("cibeles", limit=0)

    def test_night_schedule_only_from_cibeles(self):
        for origin, expected in (("atocha", 0), ("cibeles", 1)):
            result = self.service.list_departures(origin, "2026-10-23", after="00:00", limit=20)
            night = [t for t in result["trips"] if t["departure"][11:16] < "06:00" or t["departure"][11:16] >= "23:25"]
            self.assertEqual(bool(night), bool(expected), origin)
        trips = self.service.list_departures("cibeles", "2026-10-23", limit=20)["trips"]
        self.assertTrue(any(t["trip_id"].endswith("-CIB") and t["departure"][11:16] == "00:05" for t in trips))
        times = [t["departure"][11:16] for t in self.service.list_departures("cibeles", "2026-10-23", limit=20)["trips"]]
        self.assertEqual(times[:3], ["00:05", "00:40", "01:15"])
        atocha_times = [t["departure"][11:16] for t in self.service.list_departures(
            "atocha", "2026-10-23", limit=20)["trips"]]
        self.assertEqual(atocha_times[0], "06:00")

    def test_pre_occupancy_is_deterministic_and_bounded(self):
        other = emt.EmtTickets(CONFIG, clock=self.clock)
        keys = [f"203-20261023-{h:02d}{m:02d}-ATO" for h in range(6, 24) for m in (0, 15, 30, 45)]
        values = [self.service.pre_occupied(key) for key in keys]
        self.assertEqual(values, [other.pre_occupied(key) for key in keys])
        self.assertTrue(all(0 <= v <= CONFIG["airport"]["capacity"] for v in values))
        self.assertGreater(len(set(values)), 5)
        self.assertEqual(self.service.pre_occupied("203-20261023-0600-ATO"), 5)

    def test_booking_store_is_capped_and_releases_evicted_seats(self):
        service = emt.EmtTickets({**CONFIG, "max_bookings": 2}, clock=self.clock)
        trip = service.list_departures("cibeles", limit=20)["trips"][0]
        before = service.list_departures("cibeles", limit=20)["trips"][0]["seats_available"]
        if before < 1:
            self.skipTest("first trip full")
        first = service.reserve(trip["trip_id"])
        service.purchase("single")
        service.purchase("single")
        with self.assertRaises(ToolError):
            service.get_booking(first["locator"])
        self.assertEqual(service.list_departures("cibeles", limit=20)["trips"][0]["seats_available"], before)

    def test_no_network_access(self):
        import socket
        original = socket.socket.connect
        socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError("network"))
        try:
            self.service.purchase("single")
            self.service.list_departures()
        finally:
            socket.socket.connect = original


if __name__ == "__main__":
    unittest.main()
