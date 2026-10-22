"""DEMO EMT Madrid ticketing and airport seat reservation. Nothing here is real."""
from collections import OrderedDict
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
import hashlib
import re
import secrets
from threading import Lock
from zoneinfo import ZoneInfo

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

MADRID = ZoneInfo("Europe/Madrid")
LOCATOR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
LOCATOR_RE = re.compile(r"^EMT-[A-HJ-NP-Z2-9]{6}$")
TRIP_ID_RE = re.compile(r"^203-(\d{8})-(\d{4})-(ATO|CIB)$")
AFTER_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
CENT = Decimal("0.01")
MAX_QUANTITY = 10
MAX_SEATS = 4
MAX_NAME = 60

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
BOOKING = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)


def now() -> datetime:
    return datetime.now(MADRID)


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _demo(payload: dict) -> dict:
    return payload


def _hhmm(text: str) -> time:
    hour, minute = text.split(":")
    return time(int(hour), int(minute))


def _check_int(value: object, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ToolError(f"{name} must be an integer between {low} and {high}.")
    return value


def _clean_name(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ToolError("passenger_name must be text.")
    value = value.strip()
    if not value:
        return None
    if len(value) > MAX_NAME or not value.isprintable():
        raise ToolError(f"passenger_name must be at most {MAX_NAME} printable characters.")
    return value


def _clean_locator(value: object) -> str:
    text = value.strip().upper() if isinstance(value, str) else ""
    if not LOCATOR_RE.fullmatch(text):
        raise ToolError("Invalid locator; expected the format EMT-XXXXXX.")
    return text


class EmtTickets:
    def __init__(self, config: dict, clock=now):
        self.clock = clock
        self.max_bookings = int(config.get("max_bookings", 5000))
        tourist = config.get("tourist", {})
        self.child_percent = Decimal(str(tourist.get("child_percent", 50)))
        self.products: dict[str, dict] = {}
        for raw in config["products"]:
            product = dict(raw)
            product["price"] = money(Decimal(str(product["price"])))
            if product["category"] == "tourist":
                days, zone = product["days"], product["zone"]
                product.setdefault("name", f"Abono Turístico zona {zone} - {days} {'día' if days == 1 else 'días'}")
                product.setdefault("name_en", f"Tourist Pass zone {zone} - {days} {'day' if days == 1 else 'days'}")
                product.setdefault("description", f"Abono turístico de {days} días consecutivos, zona {zone}.")
                product.setdefault("description_en", f"Tourist pass for {days} consecutive days, zone {zone}.")
                product.setdefault("validity", f"{days} días naturales consecutivos.")
                product.setdefault("validity_en", f"{days} consecutive calendar days.")
                product.setdefault("source_url", tourist.get("source_url"))
                product["child_discount_percent"] = int(self.child_percent)
            else:
                product["child_discount_percent"] = None
            self.products[product["id"]] = product
        air = config["airport"]
        self.air = air
        self.capacity = int(air["capacity"])
        self.max_days_ahead = int(air["max_days_ahead"])
        self.origins = air["origins"]
        self.terminals = {name: int(minutes) for name, minutes in air["terminals"].items()}
        self.air_product = self.products[air["product_id"]]
        self.bookings: OrderedDict[str, dict] = OrderedDict()
        self.seats_booked: dict[str, int] = {}
        self.lock = Lock()

    # -- catalogue and quotes -------------------------------------------------
    def _public_product(self, product: dict) -> dict:
        return {
            "product_id": product["id"], "category": product["category"],
            "name": product["name"], "name_en": product["name_en"],
            "description": product["description"], "description_en": product["description_en"],
            "price_eur": float(product["price"]),
            "child_price_eur": float(money(product["price"] * self.child_percent / 100))
            if product["child_discount_percent"] else None,
            "currency": "EUR",
            "source_url": product["source_url"],
        }

    def list_products(self, category: str | None = None) -> dict:
        if category is not None and not isinstance(category, str):
            raise ToolError("category must be text.")
        category = category.strip().lower() if category else None
        categories = sorted({p["category"] for p in self.products.values()})
        if category and category not in categories:
            raise ToolError(f"Unknown category {category!r}; use one of: {', '.join(categories)}.")
        items = [self._public_product(p) for p in self.products.values()
                 if category is None or p["category"] == category]
        return _demo({"categories": categories, "products": items})

    def _price_lines(self, product_id: str, quantity: int, child_quantity: int) -> tuple[dict, list[dict], Decimal]:
        product = self.products.get(product_id) if isinstance(product_id, str) else None
        if product is None:
            raise ToolError(f"Unknown product_id {product_id!r}; use list_ticket_products.")
        _check_int(quantity, "quantity", 1, MAX_QUANTITY)
        _check_int(child_quantity, "child_quantity", 0, MAX_QUANTITY)
        if quantity + child_quantity > MAX_QUANTITY:
            raise ToolError(f"quantity plus child_quantity must not exceed {MAX_QUANTITY}.")
        if child_quantity and not product["child_discount_percent"]:
            raise ToolError("child_quantity only applies to tourist passes (50% for children under 11).")
        lines = [self._line(product["id"], "adult", quantity, product["price"])]
        if child_quantity:
            lines.append(self._line(product["id"], "child", child_quantity,
                                    money(product["price"] * self.child_percent / 100)))
        return product, lines, sum((Decimal(str(line["line_total_eur"])) for line in lines), Decimal("0"))

    @staticmethod
    def _line(product_id: str, kind: str, quantity: int, unit: Decimal) -> dict:
        return {"product_id": product_id, "passenger_type": kind, "quantity": quantity,
                "unit_price_eur": float(unit), "line_total_eur": float(money(unit * quantity))}

    def quote(self, product_id: str, quantity: int = 1, child_quantity: int = 0) -> dict:
        product, lines, total = self._price_lines(product_id, quantity, child_quantity)
        return _demo({"product_id": product["id"], "name": product["name"], "items": lines,
                      "total_eur": float(money(total)), "currency": "EUR"})

    # -- store ------------------------------------------------------------------
    def _new_locator(self) -> str:
        while True:
            locator = "EMT-" + "".join(secrets.choice(LOCATOR_ALPHABET) for _ in range(6))
            if locator not in self.bookings:
                return locator

    def _store(self, record: dict) -> None:
        # Caller holds the lock.
        self.bookings[record["locator"]] = record
        if record["type"] == "seat_booking":
            self.seats_booked[record["run_key"]] = self.seats_booked.get(record["run_key"], 0) + record["seats"]
        while len(self.bookings) > self.max_bookings:
            _, old = self.bookings.popitem(last=False)
            if old["type"] == "seat_booking" and old["status"] == "confirmed":
                self.seats_booked[old["run_key"]] -= old["seats"]

    @staticmethod
    def _public_booking(record: dict) -> dict:
        return _demo({key: value for key, value in record.items() if key != "run_key"})

    def purchase(self, product_id: str, quantity: int = 1, child_quantity: int = 0,
                 passenger_name: str | None = None) -> dict:
        product, lines, total = self._price_lines(product_id, quantity, child_quantity)
        name = _clean_name(passenger_name)
        current = self.clock()
        valid_until = None
        if product["category"] == "tourist":
            end_day = current.date() + timedelta(days=product["days"] - 1)
            valid_until = datetime.combine(end_day, time(23, 59), MADRID).isoformat()
        with self.lock:
            locator = self._new_locator()
            record = {
                "type": "ticket_order", "locator": locator, "status": "confirmed",
                "product_id": product["id"], "product_name": product["name"], "items": lines,
                "total_eur": float(money(total)), "currency": "EUR", "passenger_name": name,
                "created_at": current.isoformat(), "valid_from": current.isoformat(),
                "valid_until": valid_until, "validity": product["validity"], "validity_en": product["validity_en"],
                "qr_payload": f"EMTDEMO|ORDER|{locator}|{product['id']}|{quantity + child_quantity}",
            }
            self._store(record)
            return self._public_booking(record)

    def get_booking(self, locator: str) -> dict:
        locator = _clean_locator(locator)
        with self.lock:
            record = self.bookings.get(locator)
            if record is None:
                raise ToolError(f"No booking found for {locator}; bookings are lost when the server restarts.")
            return self._public_booking(record)

    def cancel(self, locator: str) -> dict:
        locator = _clean_locator(locator)
        with self.lock:
            record = self.bookings.get(locator)
            if record is None:
                raise ToolError(f"No booking found for {locator}; bookings are lost when the server restarts.")
            if record["status"] == "cancelled":
                raise ToolError(f"Booking {locator} is already cancelled.")
            if record["type"] == "seat_booking":
                if datetime.fromisoformat(record["departure"]) <= self.clock():
                    raise ToolError("This trip has already departed and can no longer be cancelled.")
                self.seats_booked[record["run_key"]] -= record["seats"]
            record["status"] = "cancelled"
            record["cancelled_at"] = self.clock().isoformat()
            record["refund_eur"] = record["total_eur"]
            return self._public_booking(record)

    # -- airport schedule -------------------------------------------------------
    def _run_times(self, day: date) -> list[tuple[datetime, str]]:
        """Runs whose first stop falls on `day`: (first-stop departure, 'ATO'|'CIB')."""
        air = self.air
        runs = []
        current = datetime.combine(day, _hhmm(air["day_first"]), MADRID)
        last = datetime.combine(day, _hhmm(air["day_last"]), MADRID)
        while current <= last:
            runs.append((current, "ATO"))
            current += timedelta(minutes=int(air["day_interval_min"]))
        step = timedelta(minutes=int(air["night_interval_min"]))
        start = datetime.combine(day - timedelta(days=1), _hhmm(air["night_first"]), MADRID)
        day_start = datetime.combine(day, time(0, 0), MADRID)
        day_end = datetime.combine(day, _hhmm(air["night_until"]), MADRID)
        current = start
        while current < day_end:
            if current >= day_start:
                runs.append((current, "CIB"))
            current += step
        runs.append((datetime.combine(day, _hhmm(air["night_first"]), MADRID), "CIB"))
        return sorted(runs)

    def _trips(self, day: date) -> list[dict]:
        by_code = {o["code"]: (key, o) for key, o in self.origins.items()}
        trips = []
        for first, code in self._run_times(day):
            run_key = f"203-{first:%Y%m%d-%H%M}-{code}"
            if code == "ATO":
                base = first
                variants = [(o["code"], first + timedelta(minutes=int(o["offset_min"]))) for o in self.origins.values()]
            else:
                base = first - timedelta(minutes=int(by_code[code][1]["offset_min"]))
                variants = [(code, first)]
            for variant_code, departure in variants:
                key, origin = by_code[variant_code]
                trips.append({
                    "trip_id": run_key if variant_code == code else f"203-{departure:%Y%m%d-%H%M}-{variant_code}",
                    "run_key": run_key, "origin_key": key, "origin": origin, "departure": departure,
                    "arrivals": {t: base + timedelta(minutes=m) for t, m in self.terminals.items()},
                })
        return trips

    def pre_occupied(self, run_key: str) -> int:
        digest = hashlib.sha256(run_key.encode("ascii")).digest()
        fraction = int.from_bytes(digest[:4], "big") / 2**32
        return int(self.capacity * (0.15 + 0.85 * fraction * fraction))

    def seats_available(self, run_key: str) -> int:
        return max(0, self.capacity - self.pre_occupied(run_key) - self.seats_booked.get(run_key, 0))

    def _public_trip(self, trip: dict, terminal: str | None) -> dict:
        origin = trip["origin"]
        return {
            "trip_id": trip["trip_id"], "line": "203", "origin": trip["origin_key"],
            "boarding_stop": {"name": origin["name"], "stop_id": origin["stop_id"]},
            "departure": trip["departure"].isoformat(),
            "arrivals": {t: a.isoformat() for t, a in trip["arrivals"].items() if terminal in (None, t)},
            "seats_available": self.seats_available(trip["run_key"]),
            "price_per_seat_eur": float(self.air_product["price"]),
        }

    def _date_window(self, current: datetime) -> tuple[date, date]:
        return current.date(), current.date() + timedelta(days=self.max_days_ahead)

    def list_departures(self, origin: str = "cibeles", date: str | None = None, after: str | None = None,
                        terminal: str | None = None, seats: int = 1, limit: int = 8) -> dict:
        origin = origin.strip().lower() if isinstance(origin, str) else ""
        if origin not in self.origins:
            raise ToolError(f"Unknown origin; use one of: {', '.join(self.origins)}.")
        _check_int(seats, "seats", 1, MAX_SEATS)
        _check_int(limit, "limit", 1, 20)
        if terminal is not None:
            terminal = terminal.strip().upper() if isinstance(terminal, str) else ""
            if terminal not in self.terminals:
                raise ToolError(f"Unknown terminal; use one of: {', '.join(self.terminals)}.")
        after_time = None
        if after is not None:
            match = AFTER_RE.fullmatch(after.strip()) if isinstance(after, str) else None
            if not match:
                raise ToolError("after must be a time in HH:MM format.")
            after_time = time(int(match[1]), int(match[2]))
        current = self.clock()
        first_day, last_day = self._date_window(current)
        if date is None:
            days = [first_day, first_day + timedelta(days=1)]
        else:
            days = [self._parse_day(date, current)]
        trips = []
        notes = []
        for index, day in enumerate(days):
            for trip in self._trips(day):
                if trip["origin_key"] != origin or trip["departure"] <= current:
                    continue
                if after_time and index == 0 and trip["departure"].time() < after_time:
                    continue
                if self.seats_available(trip["run_key"]) < seats:
                    continue
                trips.append(self._public_trip(trip, terminal))
        trips = trips[:limit]
        if not trips:
            notes.append("No matching departures. Night service (23:30-06:00) runs from Cibeles only.")
        return _demo({"line": "203", "origin": origin, "date": days[0].isoformat() if date else None,
                      "trips": trips, "notes": notes,
                      "max_date": last_day.isoformat()})

    def _parse_day(self, text: object, current: datetime) -> date:
        try:
            day = date.fromisoformat(text.strip()) if isinstance(text, str) else None
        except ValueError:
            day = None
        if day is None:
            raise ToolError("date must be in YYYY-MM-DD format.")
        first_day, last_day = self._date_window(current)
        if day < first_day:
            raise ToolError("date is in the past.")
        if day > last_day:
            raise ToolError(f"date must be at most {self.max_days_ahead} days ahead (up to {last_day.isoformat()}).")
        return day

    def reserve(self, trip_id: str, seats: int = 1, terminal: str = "T4", passenger_name: str | None = None) -> dict:
        match = TRIP_ID_RE.fullmatch(trip_id.strip()) if isinstance(trip_id, str) else None
        if not match:
            raise ToolError("Invalid trip_id; use an id from list_airport_departures.")
        _check_int(seats, "seats", 1, MAX_SEATS)
        terminal = terminal.strip().upper() if isinstance(terminal, str) else ""
        if terminal not in self.terminals:
            raise ToolError(f"Unknown terminal; use one of: {', '.join(self.terminals)}.")
        name = _clean_name(passenger_name)
        trip_id = trip_id.strip()
        current = self.clock()
        try:
            day = datetime.strptime(match[1], "%Y%m%d").date()
        except ValueError:
            raise ToolError("Invalid trip_id date.") from None
        first_day, last_day = self._date_window(current)
        if day < first_day:
            raise ToolError("This trip has already departed.")
        if day > last_day:
            raise ToolError(f"Trips can only be reserved up to {self.max_days_ahead} days ahead.")
        trip = next((t for t in self._trips(day) if t["trip_id"] == trip_id), None)
        if trip is None:
            raise ToolError("Unknown trip_id; it is not in the schedule.")
        if trip["departure"] <= current:
            raise ToolError("This trip has already departed.")
        total = money(self.air_product["price"] * seats)
        with self.lock:
            available = self.seats_available(trip["run_key"])
            if available < seats:
                raise ToolError(f"Only {available} seat(s) left on this trip; requested {seats}.")
            locator = self._new_locator()
            origin = trip["origin"]
            record = {
                "type": "seat_booking", "locator": locator, "status": "confirmed", "run_key": trip["run_key"],
                "trip_id": trip_id, "line": "203", "seats": seats, "terminal": terminal,
                "boarding_stop": {"name": origin["name"], "stop_id": origin["stop_id"]},
                "departure": trip["departure"].isoformat(), "arrival": trip["arrivals"][terminal].isoformat(),
                "items": [self._line(self.air_product["id"], "adult", seats, self.air_product["price"])],
                "total_eur": float(total), "currency": "EUR", "passenger_name": name,
                "created_at": current.isoformat(),
                "qr_payload": f"EMTDEMO|SEAT|{locator}|{trip_id}|{terminal}|{seats}",
            }
            self._store(record)
            return self._public_booking(record)


def register(mcp, config: dict):
    service = EmtTickets(config, clock=lambda: now())

    @mcp.tool(
        name="list_ticket_products",
        description="List EMT Madrid ticket products and prices in EUR. Optional category: "
                    "single, multi, tourist, airport.",
        annotations=READ_ONLY,
    )
    def list_ticket_products(category: str | None = None) -> dict:
        return service.list_products(category)

    @mcp.tool(
        name="quote_tickets",
        description="Price a ticket purchase without buying. child_quantity (50%) applies to tourist passes only. "
                    "Quantities 1-10 in total. Always quote before purchase_ticket.",
        annotations=READ_ONLY,
    )
    def quote_tickets(product_id: str, quantity: int = 1, child_quantity: int = 0) -> dict:
        return service.quote(product_id, quantity, child_quantity)

    @mcp.tool(
        name="purchase_ticket",
        description="Create a ticket order. Returns locator and qr_payload. "
                    "Requires explicit user confirmation after a quote.",
        annotations=BOOKING,
    )
    def purchase_ticket(product_id: str, quantity: int = 1, child_quantity: int = 0,
                        passenger_name: str | None = None) -> dict:
        return service.purchase(product_id, quantity, child_quantity, passenger_name)

    @mcp.tool(
        name="list_airport_departures",
        description="List Exprés Aeropuerto (line 203) departures from atocha or cibeles with "
                    "per-terminal arrival times and seats_available. Dates: today to 30 days ahead; after is HH:MM.",
        annotations=READ_ONLY,
    )
    def list_airport_departures(origin: str = "cibeles", date: str | None = None, after: str | None = None,
                                terminal: str | None = None, seats: int = 1, limit: int = 8) -> dict:
        return service.list_departures(origin, date, after, terminal, seats, limit)

    @mcp.tool(
        name="reserve_airport_seat",
        description="Reserve 1-4 seats on an Exprés Aeropuerto trip (5 EUR per seat). Returns "
                    "locator, qr_payload, boarding stop and time. Requires explicit user confirmation.",
        annotations=BOOKING,
    )
    def reserve_airport_seat(trip_id: str, seats: int = 1, terminal: str = "T4",
                             passenger_name: str | None = None) -> dict:
        return service.reserve(trip_id, seats, terminal, passenger_name)

    @mcp.tool(
        name="get_booking",
        description="Look up a ticket order or seat booking by locator (EMT-XXXXXX).",
        annotations=READ_ONLY,
    )
    def get_booking(locator: str) -> dict:
        return service.get_booking(locator)

    @mcp.tool(
        name="cancel_booking",
        description="Cancel an order or seat booking by locator; seats are released and refund_eur equals the "
                    "total. Requires explicit user confirmation.",
        annotations=BOOKING,
    )
    def cancel_booking(locator: str) -> dict:
        return service.cancel(locator)
