from copy import deepcopy
from datetime import datetime, timezone
import math
import ssl
import sys
from threading import Lock
import time
import unicodedata
from typing import Literal
from zoneinfo import ZoneInfo
import logging

import httpx
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations


logger = logging.getLogger("bicimad")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False

DEFAULT_BASE_URL = "https://madrid.publicbikesystem.net/customer/gbfs/v2"
DISCOVERY_URL = f"{DEFAULT_BASE_URL}/gbfs.json"
SOURCE_URL = "https://www.bicimad.com/"
ATTRIBUTION = "Datos: BiciMAD (EMT Madrid), GBFS"
FEEDS = ("station_information", "station_status", "vehicle_types")
MADRID_TZ = ZoneInfo("Europe/Madrid")
LAT_RANGE = (40.2, 40.7)
LON_RANGE = (-4.0, -3.4)
FRESHNESS_STATES = ("fresh", "stale", "missing", "invalid", "future")
VEHICLE_KINDS = {"ebike", "pedal", "scooter"}
TOOL_ANNOTATIONS = {
    "open": ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
    "closed": ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False),
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def fold(text: object) -> str:
    decomposed = unicodedata.normalize("NFKD", str(text or "")).casefold()
    return "".join(char for char in decomposed if not unicodedata.combining(char)).strip()


def is_number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def count_or_none(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def epoch_to_datetime(value: object) -> datetime | None:
    if type(value) is not int or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def freshness_of(value: object, now: datetime, stale_after: float) -> tuple[str, float | None, datetime | None]:
    moment = epoch_to_datetime(value)
    if value is None:
        return "missing", None, None
    if moment is None:
        return "invalid", None, None
    age = (now - moment).total_seconds()
    if age < 0:
        return "future", age, moment
    return ("stale" if age > stale_after else "fresh"), age, moment


def madrid_iso(moment: datetime | None) -> str | None:
    return moment.astimezone(MADRID_TZ).isoformat() if moment is not None else None


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 6371008.8 * 2 * math.asin(math.sqrt(min(1.0, max(0.0, a))))


def vehicle_kind(vehicle_type: dict) -> str:
    form = vehicle_type.get("form_factor")
    propulsion = vehicle_type.get("propulsion_type")
    if isinstance(form, str) and form.startswith("scooter"):
        return "scooter"
    if form == "bicycle" and propulsion == "electric_assist":
        return "ebike"
    if form == "bicycle" and propulsion == "human":
        return "pedal"
    return "other"


KIND_LABELS = {
    "ebike": "bicicleta eléctrica (e-bike)",
    "pedal": "bicicleta mecánica (pedal)",
    "scooter": "patinete eléctrico",
    "other": "otro vehículo",
    "unknown": "tipo desconocido",
}


def parse_vehicle_types(payload: dict) -> dict[str, dict]:
    types = {}
    for item in payload["data"]["vehicle_types"]:
        if not isinstance(item, dict) or not isinstance(item.get("vehicle_type_id"), str):
            continue
        kind = vehicle_kind(item)
        types[item["vehicle_type_id"]] = {
            "vehicle_type_id": item["vehicle_type_id"],
            "name": item.get("name") or item["vehicle_type_id"],
            "kind": kind,
            "kind_label": KIND_LABELS[kind],
        }
    return types


def normalize_station(info: dict, status: dict | None, types: dict[str, dict], now: datetime,
                      station_stale_after: float, feed_fresh: bool) -> dict:
    flags = []
    lat, lon = info.get("lat"), info.get("lon")
    if not (is_number(lat) and is_number(lon)):
        lat = lon = None
        flags.append("coordinates_missing")

    bikes = docks = disabled = None
    by_type = None
    state = None
    renting = returning = None
    last_reported = None
    if status is None:
        flags.append("status_missing")
    else:
        bikes = count_or_none(status.get("num_bikes_available"))
        docks = count_or_none(status.get("num_docks_available"))
        disabled = count_or_none(status.get("num_bikes_disabled"))
        state = status.get("status") if isinstance(status.get("status"), str) else None
        renting = status.get("is_renting") if isinstance(status.get("is_renting"), bool) else None
        returning = status.get("is_returning") if isinstance(status.get("is_returning"), bool) else None
        last_reported = status.get("last_reported")
        raw_types = status.get("vehicle_types_available")
        if isinstance(raw_types, list):
            by_type = []
            for entry in raw_types:
                if not isinstance(entry, dict) or not isinstance(entry.get("vehicle_type_id"), str):
                    continue
                count = count_or_none(entry.get("count"))
                geofenced = count_or_none(entry.get("geofenced_count")) or 0
                # num_bikes_available excludes bikes counted as geofenced, so subtract them here too.
                usable = max(0, count - geofenced) if count is not None else None
                meta = types.get(entry["vehicle_type_id"])
                kind = meta["kind"] if meta else "unknown"
                by_type.append({
                    "vehicle_type_id": entry["vehicle_type_id"],
                    "name": meta["name"] if meta else entry["vehicle_type_id"],
                    "kind": kind,
                    "kind_label": KIND_LABELS[kind],
                    "count": usable,
                })
        if bikes is None:
            flags.append("bike_count_missing")
        if docks is None:
            flags.append("dock_count_missing")
        if state != "IN_SERVICE":
            flags.append("status_not_in_service")
        if renting is False:
            flags.append("not_renting")
        if returning is False:
            flags.append("not_returning")

    report_freshness, report_age, report_time = freshness_of(last_reported, now, station_stale_after)
    if status is not None:
        if report_freshness == "stale":
            flags.append("station_report_stale")
        elif report_freshness != "fresh":
            flags.append(f"station_report_{report_freshness}")

    capacity = count_or_none(info.get("capacity"))
    current = status is not None and report_freshness == "fresh" and feed_fresh and state == "IN_SERVICE"
    return {
        "station_id": str(info["station_id"]),
        "name": info.get("name"),
        "short_name": info.get("short_name") if info.get("short_name") is None else str(info.get("short_name")),
        "address": info.get("address"),
        "latitude": lat,
        "longitude": lon,
        "capacity": capacity,
        "is_charging_station": info.get("is_charging_station") if isinstance(info.get("is_charging_station"), bool) else None,
        "rental_methods": info.get("rental_methods") if isinstance(info.get("rental_methods"), list) else None,
        "status": state,
        "is_renting": renting,
        "is_returning": returning,
        "bikes_available": bikes,
        "bikes_disabled": disabled,
        "docks_available": docks,
        "bikes_by_type": by_type,
        "last_reported_at": madrid_iso(report_time),
        "report_age_seconds": round(report_age) if report_age is not None else None,
        "report_freshness": report_freshness,
        "flags": flags,
        "usable_for_ranking": current,
    }


def parse_envelope(payload: object, feed: str) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise ValueError(f"Invalid BiciMAD {feed} response: expected an object with data")
    key = "vehicle_types" if feed == "vehicle_types" else "stations"
    if not isinstance(payload["data"].get(key), list):
        raise ValueError(f"Invalid BiciMAD {feed} response: missing {key} list")
    return payload


class BicimadClient:
    def __init__(self, config: dict):
        self.base_url = str(config.get("gbfs_base_url", DEFAULT_BASE_URL)).rstrip("/")
        if not self.base_url.startswith("https://"):
            raise ValueError("gbfs_base_url must use https")
        self.language = str(config.get("language", "es"))
        if not self.language.isalpha():
            raise ValueError("language must be a language code such as 'es'")
        self.timeout = float(config.get("timeout_seconds", 15))
        self.cache_ttl = float(config.get("cache_ttl_seconds", 30))
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        if not math.isfinite(self.cache_ttl) or self.cache_ttl < 0:
            raise ValueError("cache_ttl_seconds must be nonnegative and finite")
        self.ssl_context = ssl.create_default_context()
        self.cache: tuple[float, dict] | None = None
        self.lock = Lock()

    def _get(self, feed: str) -> dict:
        response = httpx.get(
            f"{self.base_url}/{self.language}/{feed}",
            timeout=self.timeout,
            follow_redirects=False,
            verify=self.ssl_context,
            headers={"User-Agent": "DynamicMCP-BiciMAD/1.0", "Accept": "application/json"},
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as error:
            raise ValueError(f"The BiciMAD {feed} feed returned invalid JSON") from error
        return parse_envelope(payload, feed)

    def snapshot(self, force_refresh: bool = False) -> dict:
        with self.lock:
            if not force_refresh and self.cache is not None and time.monotonic() < self.cache[0]:
                return {**deepcopy(self.cache[1]), "cached": True}
            feeds = {feed: self._get(feed) for feed in FEEDS}
            feed_ttls = [feeds[feed].get("ttl") for feed in FEEDS if is_number(feeds[feed].get("ttl"))]
            ttl = max([self.cache_ttl, *feed_ttls])
            snapshot = {"feeds": feeds, "retrieved_at": utc_now().isoformat()}
            self.cache = (time.monotonic() + ttl, snapshot)
            logger.info("Fetched BiciMAD GBFS feeds (%d stations)", len(feeds["station_information"]["data"]["stations"]))
            return {**deepcopy(snapshot), "cached": False}


def check_prices_config(prices: object) -> dict:
    if not isinstance(prices, dict) or not isinstance(prices.get("plans"), list):
        raise ValueError("prices must be a mapping with a plans list")
    return prices


def register(mcp, config: dict):
    client = BicimadClient(config)
    stale_after = float(config.get("stale_after_seconds", 300))
    station_stale_after = float(config.get("station_stale_after_seconds", 1800))
    default_radius = int(config.get("default_radius_m", 500))
    max_radius = int(config.get("max_radius_m", 3000))
    max_results = int(config.get("max_results", 10))
    source_url = str(config.get("source_url", SOURCE_URL))
    data_source_url = str(config.get("data_source_url", DISCOVERY_URL))
    attribution = str(config.get("attribution", ATTRIBUTION))
    prices = check_prices_config(config.get("prices", {"plans": []}))
    if stale_after <= 0 or station_stale_after <= 0 or not 1 <= default_radius <= max_radius or max_results < 1:
        raise ValueError("Invalid freshness, radius, or result limits in bicimad configuration")

    def envelope(fetched: dict, now: datetime, feed_freshness: str, feed_age: float | None, published: datetime | None) -> dict:
        return {
            "published_at": madrid_iso(published),
            "freshness": feed_freshness,
            "age_seconds": round(feed_age) if feed_age is not None else None,
            "retrieved_at": fetched["retrieved_at"],
            "evaluated_at": now.isoformat(),
            "cached": fetched["cached"],
            "source_url": source_url,
            "data_source_url": data_source_url,
            "attribution": attribution,
        }

    def load(force_refresh: bool) -> dict:
        try:
            fetched = client.snapshot(force_refresh)
            feeds = fetched["feeds"]
            now = utc_now()
            status_feed = feeds["station_status"]
            feed_freshness, feed_age, published = freshness_of(status_feed.get("last_updated"), now, stale_after)
            types = parse_vehicle_types(feeds["vehicle_types"])
            statuses = {}
            for item in status_feed["data"]["stations"]:
                if isinstance(item, dict) and item.get("station_id") is not None:
                    statuses[str(item["station_id"])] = item
            stations = []
            for info in feeds["station_information"]["data"]["stations"]:
                if isinstance(info, dict) and info.get("station_id") is not None:
                    stations.append(normalize_station(
                        info, statuses.get(str(info["station_id"])), types, now,
                        station_stale_after, feed_freshness == "fresh",
                    ))
        except httpx.HTTPStatusError as error:
            logger.error("BiciMAD HTTP error: %s", error.response.status_code)
            raise ToolError(f"The BiciMAD GBFS feed returned HTTP {error.response.status_code}; availability is unknown.") from None
        except httpx.HTTPError as error:
            logger.error("BiciMAD network error: %s", type(error).__name__)
            raise ToolError("The BiciMAD GBFS feed could not be reached; availability is unknown, not zero.") from None
        except (ValueError, KeyError, TypeError) as error:
            logger.error("BiciMAD invalid data: %s", error)
            raise ToolError(f"The BiciMAD GBFS feed returned unusable data ({error}); availability is unknown.") from None
        warnings = []
        if feed_freshness != "fresh":
            warnings.append(f"Station status feed is {feed_freshness}; availability may not be current.")
        return {
            "meta": envelope(fetched, now, feed_freshness, feed_age, published),
            "feed_fresh": feed_freshness == "fresh",
            "stations": stations,
            "types": types,
            "warnings": warnings,
        }

    def public(station: dict, **extra) -> dict:
        keep = ("station_id", "name", "short_name", "address", "latitude", "longitude", "status", "is_renting",
                "is_returning", "capacity", "bikes_available", "docks_available", "bikes_by_type",
                "last_reported_at", "report_age_seconds", "report_freshness", "flags")
        return {**{key: station[key] for key in keep}, **extra}

    @mcp.tool(
        name="search_bike_stations",
        description=(
            "Search BiciMAD stations by name, station number or address (accent/case-insensitive), e.g. "
            "'Sol', 'Atocha', 'Callao', 'Alcalá'. Returns live availability; null counts mean unknown."
        ),
        annotations=TOOL_ANNOTATIONS["open"],
    )
    def search_bike_stations(query: str, limit: int = 5, force_refresh: bool = False) -> dict:
        tokens = fold(query).split()
        if not tokens:
            raise ToolError("query must not be empty; use a station name, number or street.")
        if type(limit) is not int or not 1 <= limit <= max_results:
            raise ToolError(f"limit must be an integer between 1 and {max_results}.")
        data = load(force_refresh)
        matches = []
        for station in data["stations"]:
            name = fold(station["name"])
            haystack = " ".join((name, fold(station["short_name"]), fold(station["address"]), fold(station["station_id"])))
            if not all(token in haystack for token in tokens):
                continue
            exact = fold(query) in (fold(station["short_name"]), fold(station["station_id"]))
            rank = 0 if exact else 1 if all(token in name for token in tokens) else 2
            matches.append((rank, name, station))
        matches.sort(key=lambda item: (item[0], item[1], item[2]["station_id"]))
        return {
            **data["meta"],
            "query": query,
            "matching_stations": len(matches),
            "stations": [public(station) for _, _, station in matches[:limit]],
            "warnings": data["warnings"],
        }

    @mcp.tool(
        name="find_bike_stations_near",
        description=(
            "Find BiciMAD stations near a Madrid latitude/longitude, nearest first by straight-line metres. "
            "need='bikes' (take a bike), 'docks' (return a bike) or 'any'. Only in-service stations with "
            "fresh reports and known sufficient counts are ranked. vehicle_type: 'ebike', 'pedal', 'scooter' "
            "or a vehicle type id (applies to bikes only). Availability is not guaranteed on arrival."
        ),
        annotations=TOOL_ANNOTATIONS["open"],
    )
    def find_bike_stations_near(
        latitude: float,
        longitude: float,
        radius_m: int = default_radius,
        need: Literal["bikes", "docks", "any"] = "bikes",
        min_count: int = 1,
        vehicle_type: str | None = None,
        limit: int = 5,
        force_refresh: bool = False,
    ) -> dict:
        if not (is_number(latitude) and is_number(longitude)
                and LAT_RANGE[0] <= latitude <= LAT_RANGE[1] and LON_RANGE[0] <= longitude <= LON_RANGE[1]):
            raise ToolError("Coordinates must be in the Madrid area (latitude 40.2 to 40.7, longitude -4.0 to -3.4).")
        if type(radius_m) is not int or not 1 <= radius_m <= max_radius:
            raise ToolError(f"radius_m must be an integer between 1 and {max_radius}.")
        if need not in ("bikes", "docks", "any"):
            raise ToolError("need must be 'bikes', 'docks' or 'any'.")
        if type(min_count) is not int or min_count < 1:
            raise ToolError("min_count must be a positive integer.")
        if type(limit) is not int or not 1 <= limit <= max_results:
            raise ToolError(f"limit must be an integer between 1 and {max_results}.")
        wanted = vehicle_type.strip() if isinstance(vehicle_type, str) and vehicle_type.strip() else None
        data = load(force_refresh)
        warnings = list(data["warnings"])
        wanted_kind = wanted_id = None
        if wanted is not None:
            if wanted.casefold() in VEHICLE_KINDS:
                wanted_kind = wanted.casefold()
            else:
                wanted_id = next((vid for vid in data["types"] if vid.casefold() == wanted.casefold()), None)
                if wanted_id is None:
                    known = ", ".join(sorted(VEHICLE_KINDS) + sorted(data["types"]))
                    raise ToolError(f"Unknown vehicle_type {wanted!r}; use one of: {known}.")
            if need != "bikes":
                warnings.append("vehicle_type only applies to need='bikes'; it was ignored.")
                wanted_kind = wanted_id = None

        def matching_bikes(station: dict) -> int | None:
            if wanted_kind is None and wanted_id is None:
                return station["bikes_available"]
            if station["bikes_by_type"] is None:
                return None
            return sum(
                entry["count"] or 0 for entry in station["bikes_by_type"]
                if entry["vehicle_type_id"] == wanted_id or entry["kind"] == wanted_kind
            )

        in_radius = []
        for station in data["stations"]:
            if station["latitude"] is None:
                continue
            distance = haversine_m(latitude, longitude, station["latitude"], station["longitude"])
            if distance <= radius_m:
                in_radius.append((distance, station))
        eligible = []
        for distance, station in in_radius:
            if not station["usable_for_ranking"]:
                continue
            if need == "bikes":
                count = matching_bikes(station)
                ok = station["is_renting"] is True and count is not None and count >= min_count
            elif need == "docks":
                count = station["docks_available"]
                ok = station["is_returning"] is True and count is not None and count >= min_count
            else:
                count = None
                ok = station["is_renting"] is not False or station["is_returning"] is not False
            if ok:
                eligible.append((distance, station, count))
        eligible.sort(key=lambda item: (item[0], item[1]["station_id"]))

        reason = message = None
        if not data["feed_fresh"]:
            eligible = []
            reason = "feed_not_current"
            message = "The station status feed is not current, so no station is recommended; availability is unknown."
        elif not eligible:
            if not in_radius:
                reason = "no_stations_in_radius"
                message = f"No BiciMAD stations within {radius_m} m; try a larger radius (max {max_radius} m)."
            else:
                reason = "no_eligible_stations"
                message = (f"{len(in_radius)} station(s) within {radius_m} m, but none is in service with a fresh "
                           f"report and enough {'bikes' if need == 'bikes' else 'docks' if need == 'docks' else 'service'}.")
        results = [
            public(station, distance_m=round(distance), matched_count=count)
            for distance, station, count in eligible[:limit]
        ]
        warnings.append("Distances are straight-line metres, not walking routes. Availability is not guaranteed on arrival.")
        return {
            **data["meta"],
            "origin": {"latitude": latitude, "longitude": longitude},
            "radius_m": radius_m,
            "need": need,
            "min_count": min_count,
            "vehicle_type": wanted,
            "distance_type": "straight_line",
            "stations_in_radius": len(in_radius),
            "matching_stations": len(eligible),
            "stations": results,
            "reason": reason,
            "message": message,
            "warnings": warnings,
        }

    @mcp.tool(
        name="get_bike_station",
        description=(
            "Get one BiciMAD station by exact station_id (from search_bike_stations): capacity, bikes by "
            "vehicle type (e-bike/pedal/scooter), disabled bikes, free docks, status flags, last report "
            "time (Europe/Madrid), charging station and rental methods. Null means unknown."
        ),
        annotations=TOOL_ANNOTATIONS["open"],
    )
    def get_bike_station(station_id: str, force_refresh: bool = False) -> dict:
        wanted = str(station_id).strip()
        if not wanted:
            raise ToolError("station_id must not be empty; use search_bike_stations to find one.")
        data = load(force_refresh)
        for station in data["stations"]:
            if station["station_id"] == wanted:
                detail = public(
                    station,
                    bikes_disabled=station["bikes_disabled"],
                    is_charging_station=station["is_charging_station"],
                    rental_methods=station["rental_methods"],
                )
                return {**data["meta"], "station": detail, "warnings": data["warnings"]}
        raise ToolError(f"Unknown station_id {wanted!r}; use search_bike_stations to find a station.")

    @mcp.tool(
        name="get_bicimad_summary",
        description=(
            "City-wide BiciMAD totals: stations in service, bikes available (by type), free docks, stations "
            "with no bikes or no free docks, data coverage and warnings. Totals only include in-service "
            "stations with fresh reports and known counts."
        ),
        annotations=TOOL_ANNOTATIONS["open"],
    )
    def get_bicimad_summary(force_refresh: bool = False) -> dict:
        data = load(force_refresh)
        stations = data["stations"]
        in_service = [s for s in stations if s["status"] == "IN_SERVICE"]
        current = [s for s in in_service if s["report_freshness"] == "fresh"] if data["feed_fresh"] else []
        with_bikes = [s for s in current if s["bikes_available"] is not None]
        with_docks = [s for s in current if s["docks_available"] is not None]
        by_kind = {kind: 0 for kind in ("ebike", "pedal", "scooter", "other", "unknown")}
        for station in current:
            for entry in station["bikes_by_type"] or []:
                by_kind[entry["kind"]] += entry["count"] or 0
        warnings = list(data["warnings"])
        warnings.append("Totals exclude stations that are not in service, have stale reports, or have missing counts.")
        if not data["feed_fresh"]:
            warnings.append("Totals are empty because the feed is not current; this is not evidence of zero availability.")
        return {
            **data["meta"],
            "stations_total": len(stations),
            "stations_in_service": len(in_service),
            "stations_not_in_service": sum(s["status"] != "IN_SERVICE" for s in stations),
            "stations_with_fresh_reports": len(current),
            "stations_with_stale_or_invalid_reports": sum(s["report_freshness"] != "fresh" for s in in_service),
            "bikes_available": sum(s["bikes_available"] for s in with_bikes) if with_bikes else None,
            "bikes_available_by_kind": by_kind if current else None,
            "docks_available": sum(s["docks_available"] for s in with_docks) if with_docks else None,
            "stations_with_zero_bikes": sum(s["bikes_available"] == 0 for s in with_bikes),
            "stations_with_zero_docks": sum(s["docks_available"] == 0 for s in with_docks),
            "stations_with_missing_counts": sum(
                s["bikes_available"] is None or s["docks_available"] is None for s in current
            ),
            "warnings": warnings,
        }

    @mcp.tool(
        name="get_bicimad_prices",
        description=(
            "BiciMAD fares: pay-per-use, 30-day flat rate and BicibusEMT combined ticket."
        ),
        annotations=TOOL_ANNOTATIONS["closed"],
    )
    def get_bicimad_prices() -> dict:
        return {
            **deepcopy(prices),
            "source_url": source_url,
            "attribution": attribution,
        }
