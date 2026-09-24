from copy import deepcopy
from datetime import datetime, timezone
import math
import ssl
from threading import Lock
import time

import httpx


SOURCE_DATASET = "100088"
SOURCE_URL = "https://data.bs.ch/explore/dataset/100088/"
API_URL = "https://data.bs.ch/api/explore/v2.1/catalog/datasets/100088/records"
PAGE_SIZE = 100


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def source_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        pass
    return None


def valid_coordinate(value: object, bound: float) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and -bound <= value <= bound


def normalize_record(record: dict, now: datetime, freshness_seconds: float) -> dict:
    warnings = []
    published = record.get("published")
    published_time = source_time(published)
    age_seconds = (now - published_time).total_seconds() if published_time is not None else None
    if published is None:
        freshness = "missing"
    elif age_seconds is None:
        freshness = "invalid"
    elif age_seconds < 0:
        freshness = "future"
    elif age_seconds > freshness_seconds:
        freshness = "stale"
    else:
        freshness = "fresh"
    if freshness != "fresh":
        warnings.append(f"Publication timestamp is {freshness}; not current availability.")

    raw_free = record.get("free")
    raw_total = record.get("total")
    free = raw_free if type(raw_free) is int and raw_free >= 0 else None
    total = raw_total if type(raw_total) is int and raw_total >= 0 else None
    invalid_counts = (
        (raw_free is not None and free is None)
        or (raw_total is not None and total is None)
        or (free is not None and total is not None and free > total)
    )
    if invalid_counts:
        count_quality = "invalid"
        warnings.append("Counts are invalid or inconsistent; no occupancy calculation or ranking.")
    elif free is None or total is None:
        count_quality = "missing"
        warnings.append("A count is missing; unknown counts remain null and occupancy is unavailable.")
    elif total == 0:
        count_quality = "zero_capacity"
        warnings.append("Capacity is zero; no occupancy calculation or ranking.")
    else:
        count_quality = "valid"

    raw_status = record.get("status")
    status_key = raw_status.strip().casefold() if isinstance(raw_status, str) else ""
    status = {"offen": "open", "geschlossen": "closed"}.get(status_key, "unknown")
    if status == "unknown":
        warnings.append("Opening status is unknown; this facility is not confirmed open.")
    elif status == "closed":
        warnings.append("The source reports this facility as closed.")

    point = record.get("geo_point_2d")
    point = point if isinstance(point, dict) else {}
    latitude, longitude = point.get("lat"), point.get("lon")
    if not valid_coordinate(latitude, 90) or not valid_coordinate(longitude, 180):
        latitude, longitude = None, None
        warnings.append("Coordinates are missing or invalid; distance is unavailable.")

    usable_availability = free is not None and not invalid_counts and total != 0
    return {
        "parking_id": record["id"],
        "name": record.get("name") or record.get("title") or record["id"],
        "title": record.get("title"),
        "address": record.get("address"),
        "details_url": record.get("link"),
        "latitude": latitude,
        "longitude": longitude,
        "free_spaces": free,
        "total_spaces": total,
        "count_quality": count_quality,
        "occupancy_percent": round(100 * (total - free) / total, 2) if count_quality == "valid" else None,
        "free_percent": round(100 * free / total, 2) if count_quality == "valid" else None,
        "status_raw": raw_status,
        "status": status,
        "published_at": published,
        "last_downloaded": record.get("last_downloaded"),
        "age_seconds": age_seconds,
        "freshness": freshness,
        "eligible_for_ranking": freshness == "fresh" and status != "closed" and usable_availability,
        "warnings": warnings,
    }


def straight_line_distance(latitude: float, longitude: float, record: dict) -> float | None:
    if record["latitude"] is None or record["longitude"] is None:
        return None
    start_latitude = math.radians(latitude)
    end_latitude = math.radians(record["latitude"])
    latitude_delta = end_latitude - start_latitude
    longitude_delta = math.radians(record["longitude"] - longitude)
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(start_latitude) * math.cos(end_latitude) * math.sin(longitude_delta / 2) ** 2
    )
    return 6371008.8 * 2 * math.asin(math.sqrt(min(1.0, max(0.0, haversine))))


class ParkingClient:
    def __init__(self, config: dict):
        self.timeout = float(config.get("timeout_seconds", 15))
        self.cache_ttl = float(config.get("cache_ttl_seconds", 30))
        self.max_pages = config.get("max_pages", 10)
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        if not math.isfinite(self.cache_ttl) or self.cache_ttl < 0:
            raise ValueError("cache_ttl_seconds must be nonnegative and finite")
        if type(self.max_pages) is not int or not 1 <= self.max_pages <= 10:
            raise ValueError("max_pages must be an integer between 1 and 10")
        self.ssl_context = ssl.create_default_context()
        self.cache: tuple[float, dict] | None = None
        self.lock = Lock()

    def _fetch_records(self) -> list[dict]:
        records = []
        seen_ids = set()
        expected_total = None
        for _page in range(self.max_pages):
            response = httpx.get(
                API_URL,
                params={"limit": PAGE_SIZE, "offset": len(records), "order_by": "id"},
                timeout=self.timeout,
                follow_redirects=False,
                verify=self.ssl_context,
                headers={"User-Agent": "DynamicMCP-BaselParking/1.0", "Accept": "application/json"},
            )
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                raise ValueError("Expected a JSON response from the Basel parking API")
            try:
                payload = response.json()
            except ValueError as error:
                raise ValueError("The Basel parking API returned invalid JSON") from error
            if not isinstance(payload, dict):
                raise ValueError("Invalid Basel parking response: expected an object")
            total = payload.get("total_count")
            page = payload.get("results")
            if type(total) is not int or total < 0 or not isinstance(page, list):
                raise ValueError("Invalid Basel parking response: expected total_count and results")
            if total > PAGE_SIZE * self.max_pages:
                raise ValueError("The Basel parking dataset exceeds the pagination limit")
            if expected_total is not None and total != expected_total:
                raise ValueError("The Basel parking dataset changed during pagination; try again")
            expected_total = total
            if len(page) > PAGE_SIZE or len(records) + len(page) > total:
                raise ValueError("Invalid Basel parking response: inconsistent record count")
            for record in page:
                if not isinstance(record, dict):
                    raise ValueError("Invalid Basel parking record: expected an object")
                parking_id = record.get("id")
                if not isinstance(parking_id, str) or not parking_id.strip():
                    raise ValueError("Invalid Basel parking record: missing parking ID")
                if parking_id in seen_ids:
                    raise ValueError("The Basel parking dataset contains duplicate parking IDs")
                seen_ids.add(parking_id)
                records.append(record)
            if len(records) == total:
                return records
            if not page:
                raise ValueError("The Basel parking API returned an incomplete snapshot")
        raise ValueError("The Basel parking dataset exceeds the pagination limit")

    def snapshot(self) -> dict:
        with self.lock:
            if self.cache is not None and time.monotonic() < self.cache[0]:
                return {**deepcopy(self.cache[1]), "cached": True}
            records = self._fetch_records()
            snapshot = {"records": records, "retrieved_at": utc_now().isoformat()}
            self.cache = (time.monotonic() + self.cache_ttl, snapshot)
            return {**deepcopy(snapshot), "cached": False}


def register(mcp, config: dict):
    client = ParkingClient(config)
    freshness_seconds = float(config.get("freshness_seconds", 300))
    if not math.isfinite(freshness_seconds) or freshness_seconds <= 0:
        raise ValueError("freshness_seconds must be positive and finite")

    def snapshot() -> dict:
        fetched = client.snapshot()
        now = utc_now()
        facilities = [normalize_record(record, now, freshness_seconds) for record in fetched["records"]]
        facilities.sort(key=lambda facility: facility["parking_id"])
        return {
            "source_dataset": SOURCE_DATASET,
            "source_url": SOURCE_URL,
            "retrieved_at": fetched["retrieved_at"],
            "evaluated_at": now.isoformat(),
            "cached": fetched["cached"],
            "freshness_seconds": freshness_seconds,
            "total_facilities": len(facilities),
            "facilities": facilities,
        }

    @mcp.tool(
        name="list_parking_facilities",
        description=(
            "List Basel parking facilities from the official dataset 100088. Optionally filter "
            "by ID, name, title, or address. Includes source timestamps, null counts, and quality "
            "warnings; stale records are not current availability."
        ),
    )
    def list_parking_facilities(query: str = "") -> dict:
        result = snapshot()
        query = query.strip().casefold()
        if query:
            result["facilities"] = [
                facility for facility in result["facilities"]
                if any(query in str(facility[field] or "").casefold()
                       for field in ("parking_id", "name", "title", "address"))
            ]
        return {**result, "matching_facilities": len(result["facilities"])}

    @mcp.tool(
        name="get_parking_status",
        description=(
            "Get one Basel parking facility by its exact parking_id from list_parking_facilities. "
            "Returns reported free spaces, capacity, occupancy where valid, opening status, "
            "source timestamps, and freshness warnings. Unknown IDs fail explicitly."
        ),
    )
    def get_parking_status(parking_id: str) -> dict:
        parking_id = parking_id.strip()
        if not parking_id:
            raise ValueError("parking_id must not be empty; use list_parking_facilities to find an ID")
        result = snapshot()
        for facility in result.pop("facilities"):
            if facility["parking_id"] == parking_id:
                return {**result, "facility": facility}
        raise ValueError(f"Unknown parking_id {parking_id!r}; use list_parking_facilities to find an ID")

    @mcp.tool(
        name="find_available_parking",
        description=(
            "Find Basel parking near latitude/longitude, nearest first by straight-line metres, "
            "not driving distance. Only fresh, usable counts meeting min_free_spaces are ranked. "
            "Closed facilities are excluded; unknown opening status is returned with a warning. "
            "Availability is an observation, not a reservation or guarantee."
        ),
    )
    def find_available_parking(
        latitude: float,
        longitude: float,
        min_free_spaces: int = 1,
        radius_meters: float = 5000,
        limit: int = 5,
    ) -> dict:
        if not valid_coordinate(latitude, 90) or not valid_coordinate(longitude, 180):
            raise ValueError("latitude and longitude must be finite coordinates in valid ranges")
        if type(min_free_spaces) is not int or min_free_spaces < 1:
            raise ValueError("min_free_spaces must be a positive integer")
        if type(radius_meters) not in (int, float) or not math.isfinite(radius_meters) or radius_meters <= 0:
            raise ValueError("radius_meters must be positive and finite")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        result = snapshot()
        candidates = []
        for facility in result.pop("facilities"):
            if not facility["eligible_for_ranking"] or facility["free_spaces"] < min_free_spaces:
                continue
            distance = straight_line_distance(latitude, longitude, facility)
            if distance is not None and distance <= radius_meters:
                candidates.append({**facility, "distance_meters": distance})
        candidates.sort(key=lambda facility: (
            facility["distance_meters"], -facility["free_spaces"], facility["parking_id"],
        ))
        return {
            **result,
            "latitude": latitude,
            "longitude": longitude,
            "radius_meters": radius_meters,
            "min_free_spaces": min_free_spaces,
            "distance_type": "straight_line",
            "matching_facilities": len(candidates),
            "facilities": [
                {**facility, "distance_meters": round(facility["distance_meters"], 1)}
                for facility in candidates[:limit]
            ],
            "warnings": ["Distances are not driving distances. Availability is not guaranteed or reserved."],
        }

    @mcp.tool(
        name="get_parking_summary",
        description=(
            "Summarize the Basel parking snapshot, including status and data-quality counts. "
            "Free-space totals use fresh, usable, non-closed facilities only. Weighted occupancy "
            "uses only that subset with valid capacity; missing values are never treated as zero."
        ),
    )
    def get_parking_summary() -> dict:
        result = snapshot()
        facilities = result.pop("facilities")
        eligible = [facility for facility in facilities if facility["eligible_for_ranking"]]
        comparable = [facility for facility in eligible if facility["count_quality"] == "valid"]
        total_spaces = sum(facility["total_spaces"] for facility in comparable) if comparable else None
        free_spaces = sum(facility["free_spaces"] for facility in comparable) if comparable else None
        warnings = ["Totals exclude stale, invalid, missing-free-count, zero-capacity, and closed facilities."]
        if any(facility["status"] == "unknown" for facility in eligible):
            warnings.append("Some included facilities have unknown opening status; they are not confirmed open.")
        return {
            **result,
            "status_counts": {
                status: sum(facility["status"] == status for facility in facilities)
                for status in ("open", "closed", "unknown")
            },
            "freshness_counts": {
                freshness: sum(facility["freshness"] == freshness for facility in facilities)
                for freshness in ("fresh", "stale", "missing", "invalid", "future")
            },
            "count_quality_counts": {
                quality: sum(facility["count_quality"] == quality for facility in facilities)
                for quality in ("valid", "missing", "invalid", "zero_capacity")
            },
            "eligible_facilities": len(eligible),
            "known_free_spaces": sum(facility["free_spaces"] for facility in eligible) if eligible else None,
            "occupancy_facilities": len(comparable),
            "occupancy_total_spaces": total_spaces,
            "occupancy_free_spaces": free_spaces,
            "occupancy_percent": round(100 * (total_spaces - free_spaces) / total_spaces, 2) if comparable else None,
            "warnings": warnings,
        }