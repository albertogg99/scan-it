from collections import OrderedDict, deque
from copy import deepcopy
from datetime import datetime, timezone
import math
import os
import re
import ssl
from threading import Lock
import time
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

from defusedxml import ElementTree as safe_xml
from defusedxml.common import DefusedXmlException
import httpx
from mcp.server.mcpserver.exceptions import ToolError


API_URL = "https://api.opentransportdata.swiss/ojp20"
SOURCE_URL = "https://opentransportdata.swiss/en/cookbook/open-journey-planner-ojp/"
NS = {"ojp": "http://www.vdv.de/ojp", "siri": "http://www.siri.org.uk/siri"}
LOCAL_ZONE = ZoneInfo("Europe/Zurich")
MAX_RESPONSE_BYTES = 2_000_000
MAX_CACHE_ENTRIES = 128
RESPONSE_FRESHNESS_SECONDS = 300
NONFATAL_PROBLEMS = {
    "TRIP_REALTIMEINCOMPLETE",
    "STOPEVENT_NOREALTIME",
    "STOPEVENT_LASTSERVICEOFTHISLINE",
    "OJPGENERIC_EXCEPTIONFROMREQUESTEDLANGUAGE",
}
REALTIME_NOTE = (
    "Real-time predictions and incident coverage depend on operator feeds. "
    "A missing estimate does not mean on time. The response timestamp is not "
    "the observation time of each vehicle."
)

ET.register_namespace("", NS["ojp"])
ET.register_namespace("siri", NS["siri"])


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def text(node: ET.Element | None, path: str) -> str | None:
    found = node.find(path, NS) if node is not None else None
    return found.text.strip() or None if found is not None and found.text else None


def add(parent: ET.Element, name: str, value: object = None) -> ET.Element:
    prefix, local_name = name.split(":", 1) if ":" in name else ("ojp", name)
    child = ET.SubElement(parent, f"{{{NS[prefix]}}}{local_name}")
    if value is not None:
        child.text = str(value)
    return child


def checked_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 160:
        raise ValueError(f"{label} must contain 1 to 160 characters")
    if re.search(r"[\x00-\x1f\x7f]", value):
        raise ValueError(f"{label} must not contain control characters")
    return value.strip()


def checked_limit(value: int, maximum: int = 20) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"limit must be an integer between 1 and {maximum}")
    return value


def checked_stop_id(value: str) -> str:
    value = checked_text(value, "stop_id")
    if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9:._-]*", value) is None:
        raise ValueError("Use an exact stop_id returned by search_transport_stops")
    return value


def source_time(value: str | None) -> datetime | None:
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is not None:
                return parsed.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            pass
    return None


def request_time(value: str | None) -> str:
    parsed = utc_now() if value is None else source_time(value)
    if parsed is None:
        raise ValueError("at must be an ISO 8601 datetime with a UTC offset, or omitted for now")
    return parsed.isoformat(timespec="seconds")


def request_document(kind: str, language: str) -> tuple[ET.Element, ET.Element]:
    root = ET.Element(f"{{{NS['ojp']}}}OJP", version="2.0")
    service = add(add(root, "OJPRequest"), "siri:ServiceRequest")
    add(add(service, "siri:ServiceRequestContext"), "siri:Language", language)
    timestamp = utc_now().isoformat(timespec="seconds")
    add(service, "siri:RequestTimestamp", timestamp)
    add(service, "siri:RequestorRef", "DynamicMCP-PublicTransport")
    request = add(service, f"OJP{kind}Request")
    add(request, "siri:RequestTimestamp", timestamp)
    return root, request


def source_problems(root: ET.Element) -> list[dict]:
    return [{
        "code": next((
            child.text.strip() for child in condition
            if child.tag.endswith("ProblemType") and child.text
        ), None),
        "details": text(condition, "ojp:Details/ojp:Text"),
    } for condition in root.findall(".//ojp:ErrorCondition", NS)]


def parse_delivery(content: bytes, kind: str) -> ET.Element:
    try:
        root = safe_xml.fromstring(content, forbid_dtd=True)
    except (ET.ParseError, DefusedXmlException, ValueError):
        raise RuntimeError("OJP returned invalid or unsafe XML") from None
    if root.tag != f"{{{NS['ojp']}}}OJP" or root.get("version") != "2.0":
        raise RuntimeError("Expected an OJP 2.0 response")
    if (
        any((node.text or "").strip() in ("false", "0") for node in root.findall(".//siri:Status", NS))
        or root.find(".//siri:ErrorCondition", NS) is not None
        or any(problem["code"] not in NONFATAL_PROBLEMS for problem in source_problems(root))
        or root.find(".//ojp:ErrorMessage", NS) is not None
    ):
        raise RuntimeError("OJP reported a request error; this is not an empty timetable")
    delivery = root.find(f"ojp:OJPResponse/siri:ServiceDelivery/ojp:OJP{kind}Delivery", NS)
    if delivery is None:
        raise RuntimeError(f"OJP response is missing the {kind} delivery")
    return delivery


def response_metadata(delivery: ET.Element, retrieved_at: str, cached: bool) -> dict:
    now = utc_now()
    timestamp = text(delivery, "siri:ResponseTimestamp")
    parsed = source_time(timestamp)
    age = (now - parsed).total_seconds() if parsed is not None else None
    if timestamp is None:
        freshness = "missing"
    elif age is None:
        freshness = "invalid"
    elif age < 0:
        freshness = "future"
    elif age > RESPONSE_FRESHNESS_SECONDS:
        freshness = "stale"
    else:
        freshness = "fresh"
    return {
        "source_url": SOURCE_URL,
        "response_timestamp": timestamp,
        "response_age_seconds": age,
        "response_freshness": freshness,
        "retrieved_at": retrieved_at,
        "evaluated_at": now.isoformat(),
        "cached": cached,
        "timezone": LOCAL_ZONE.key,
        "source_warnings": source_problems(delivery),
        "warnings": [] if freshness == "fresh" else [
            f"OJP response timestamp is {freshness}; do not describe this as current live information."
        ],
    }


def flag(node: ET.Element | None, path: str) -> bool | None:
    return {"true": True, "1": True, "false": False, "0": False}.get(text(node, path))


def number(node: ET.Element | None, path: str) -> float | None:
    value = text(node, path)
    if value is not None:
        try:
            parsed = float(value)
            if math.isfinite(parsed):
                return parsed
        except ValueError:
            pass
    return None


def local_time(value: str | None) -> str | None:
    parsed = source_time(value)
    return parsed.astimezone(LOCAL_ZONE).isoformat() if parsed is not None else None


def service_time(call: ET.Element, name: str) -> dict | None:
    block = call.find(f"ojp:{name}", NS)
    if block is None:
        return None
    scheduled = text(block, "ojp:TimetabledTime")
    estimated = text(block, "ojp:EstimatedTime")
    scheduled_time = source_time(scheduled)
    estimated_time = source_time(estimated)
    if scheduled_time is None or (estimated is not None and estimated_time is None):
        raise RuntimeError("OJP returned an invalid or timezone-less service time")
    return {
        "scheduled_at": scheduled,
        "estimated_at": estimated,
        "scheduled_local": local_time(scheduled),
        "estimated_local": local_time(estimated),
        "delay_seconds": (estimated_time - scheduled_time).total_seconds() if estimated_time is not None else None,
        "has_realtime": estimated_time is not None,
    }


def call_details(call: ET.Element | None) -> dict:
    if call is None or text(call, "siri:StopPointRef") is None:
        raise RuntimeError("OJP returned an incomplete stop call")
    return {
        "stop_id": text(call, "siri:StopPointRef"),
        "name": text(call, "ojp:StopPointName/ojp:Text"),
        "planned_platform": text(call, "ojp:PlannedQuay/ojp:Text"),
        "estimated_platform": text(call, "ojp:EstimatedQuay/ojp:Text"),
        "arrival": service_time(call, "ServiceArrival"),
        "departure": service_time(call, "ServiceDeparture"),
        "not_serviced": flag(call, "ojp:NotServicedStop"),
        "no_boarding": flag(call, "ojp:NoBoardingAtStop"),
        "no_alighting": flag(call, "ojp:NoAlightingAtStop"),
        "accessibility_note": text(call, "ojp:NameSuffix/ojp:Text"),
    }


def service_details(service: ET.Element | None) -> dict:
    if service is None:
        raise RuntimeError("OJP returned a missing service")
    return {
        "line": text(service, "ojp:PublishedServiceName/ojp:Text") or text(service, "ojp:PublishedLineName/ojp:Text"),
        "mode": text(service, "ojp:Mode/ojp:PtMode"),
        "destination": text(service, "ojp:DestinationText/ojp:Text"),
        "journey_ref": text(service, "ojp:JourneyRef"),
        "operating_day": text(service, "ojp:OperatingDayRef"),
        "line_ref": text(service, "siri:LineRef"),
        "operator_ref": text(service, "siri:OperatorRef"),
        "cancelled": flag(service, "ojp:Cancelled"),
        "infeasible": flag(service, "ojp:Infeasible"),
        "unplanned": flag(service, "ojp:Unplanned"),
        "situation_refs": [node.text for node in service.findall(".//siri:SituationNumber", NS) if node.text],
        "attributes": [{
            "code": text(attribute, "ojp:Code"),
            "text": text(attribute, "ojp:UserText/ojp:Text") or text(attribute, "ojp:Text/ojp:Text"),
            "mandatory": flag(attribute, "ojp:Mandatory"),
        } for attribute in service.findall("ojp:Attribute", NS)],
    }


def situations(delivery: ET.Element) -> list[dict]:
    results = []
    for situation in delivery.findall(".//ojp:PtSituation", NS):
        result = {
            "situation_number": text(situation, "siri:SituationNumber"),
            "created_at": text(situation, "siri:CreationTime"),
            "validity_periods": [{
                "start": text(period, "siri:StartTime"),
                "end": text(period, "siri:EndTime"),
            } for period in situation.findall("siri:ValidityPeriod", NS)],
        }
        for field, tag in (("summary", "Summary"), ("description", "Description"),
                           ("reason", "Reason"), ("recommendation", "Recommendation")):
            nodes = situation.findall(f".//siri:{tag}Text", NS) or situation.findall(f".//siri:{tag}", NS)
            result[field] = list(dict.fromkeys(" ".join(node.itertext()).strip() for node in nodes))
        results.append(result)
    return results


def place_reference(parent: ET.Element, name: str, stop_id: str, stop_name: str) -> ET.Element:
    context = add(parent, name)
    place = add(context, "PlaceRef")
    add(place, "StopPlaceRef", stop_id)
    add(add(place, "Name"), "Text", stop_name)
    return context


def leg_place(node: ET.Element | None) -> dict:
    return {
        "stop_id": text(node, "siri:StopPointRef") or text(node, "ojp:StopPlaceRef"),
        "name": text(node, "ojp:Name/ojp:Text"),
        "latitude": number(node, "ojp:GeoPosition/siri:Latitude"),
        "longitude": number(node, "ojp:GeoPosition/siri:Longitude"),
    }


def trip_details(trip: ET.Element | None) -> dict:
    if trip is None:
        raise RuntimeError("OJP returned an incomplete trip")
    start, end = text(trip, "ojp:StartTime"), text(trip, "ojp:EndTime")
    if source_time(start) is None or source_time(end) is None:
        raise RuntimeError("OJP returned invalid trip start or end times")
    legs = []
    warnings = []
    for leg in trip.findall("ojp:Leg", NS):
        timed = leg.find("ojp:TimedLeg", NS)
        if timed is not None:
            board = call_details(timed.find("ojp:LegBoard", NS))
            alight = call_details(timed.find("ojp:LegAlight", NS))
            service = service_details(timed.find("ojp:Service", NS))
            if board["departure"] is None or alight["arrival"] is None:
                raise RuntimeError("OJP returned a timed leg without departure or arrival")
            if any(value is True for value in (
                service["cancelled"], service["infeasible"], board["not_serviced"],
                board["no_boarding"], alight["not_serviced"], alight["no_alighting"],
            )):
                warnings.append("A leg is cancelled, infeasible, or unavailable for boarding/alighting; do not recommend it.")
            details = {"type": "public_transport", "board": board, "alight": alight, "service": service}
        else:
            continuous = leg.find("ojp:ContinuousLeg", NS)
            transfer = leg.find("ojp:TransferLeg", NS)
            section = continuous if continuous is not None else transfer
            if section is None:
                raise RuntimeError("OJP returned an unsupported leg type")
            details = {
                "type": "continuous" if continuous is not None else "transfer",
                "start": leg_place(section.find("ojp:LegStart", NS)),
                "end": leg_place(section.find("ojp:LegEnd", NS)),
                "mode": text(section, "ojp:Service/ojp:PersonalMode"),
                "transfer_type": text(section, "ojp:TransferType"),
                "duration": text(section, "ojp:Duration"),
                "distance_meters": number(section, "ojp:Length"),
            }
        legs.append({"id": text(leg, "ojp:Id"), "duration": text(leg, "ojp:Duration"), **details})
    if not legs:
        raise RuntimeError("OJP returned a trip without legs")
    return {
        "id": text(trip, "ojp:Id"),
        "duration": text(trip, "ojp:Duration"),
        "start_at": start,
        "end_at": end,
        "start_local": local_time(start),
        "end_local": local_time(end),
        "transfers": number(trip, "ojp:Transfers"),
        "cancelled": flag(trip, "ojp:Cancelled"),
        "infeasible": flag(trip, "ojp:Infeasible"),
        "legs": legs,
        "warnings": list(dict.fromkeys(warnings)),
    }


class TransportClient:
    def __init__(self, config: dict):
        self.timeout = config.get("timeout_seconds", 20)
        self.cache_ttl = config.get("cache_ttl_seconds", 30)
        for label, value, minimum, maximum in (
            ("timeout_seconds", self.timeout, 0.1, 60),
            ("cache_ttl_seconds", self.cache_ttl, 0, 60),
        ):
            if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
                raise ValueError(f"{label} must be between {minimum} and {maximum}")
        self.rate_limit = config.get("requests_per_minute", 45)
        if type(self.rate_limit) is not int or not 1 <= self.rate_limit <= 50:
            raise ValueError("requests_per_minute must be an integer between 1 and 50")
        self.language = config.get("language", "en")
        if self.language not in ("en", "de", "fr", "it"):
            raise ValueError("language must be en, de, fr, or it")
        origin = config.get("origin")
        if not isinstance(origin, dict):
            raise ValueError("A fixed origin must be configured")
        self.origin = deepcopy(origin)
        self.origin["stop_id"] = checked_stop_id(origin.get("stop_id"))
        self.origin["name"] = checked_text(origin.get("name"), "origin.name")
        for name, bound in (("latitude", 90), ("longitude", 180)):
            value = origin.get(name)
            if type(value) not in (int, float) or not math.isfinite(value) or not -bound <= value <= bound:
                raise ValueError(f"origin.{name} must be a valid coordinate")
        self.token = os.environ.get("OJP_API_TOKEN", "").strip()
        if self.token.lower().startswith("bearer "):
            self.token = self.token[7:].strip()
        if any(not 33 <= ord(character) <= 126 for character in self.token):
            raise ValueError("OJP_API_TOKEN must be a single ASCII token without whitespace")
        self.ssl_context = ssl.create_default_context()
        self.cache = OrderedDict()
        self.requests = deque()
        self.lock = Lock()

    def origin_context(self) -> dict:
        return {
            "origin": deepcopy(self.origin),
            "timezone": LOCAL_ZONE.key,
            "current_time": utc_now().astimezone(LOCAL_ZONE).isoformat(),
            "token_configured": bool(self.token),
            "origin_is_fixed": True,
            "walking_time_from_venue_included": False,
        }

    def _fetch(self, document: ET.Element, kind: str) -> ET.Element:
        try:
            with httpx.stream(
                "POST", API_URL,
                content=ET.tostring(document, encoding="utf-8", xml_declaration=True),
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/xml",
                    "Accept": "application/xml",
                    "User-Agent": "DynamicMCP-PublicTransport/1.0",
                },
                timeout=self.timeout,
                verify=self.ssl_context,
                follow_redirects=False,
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type not in ("application/xml", "text/xml"):
                    raise RuntimeError("OJP returned a non-XML response")
                content = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    content.extend(chunk)
                    if len(content) > MAX_RESPONSE_BYTES:
                        raise RuntimeError("OJP response exceeded the size limit")
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            if status == 401:
                raise RuntimeError("OJP authentication failed (HTTP 401); check OJP_API_TOKEN") from None
            if status == 403:
                raise RuntimeError(
                    "OJP access denied (HTTP 403); verify the app's active OJP 2.0 subscription "
                    "and use its TOKEN, not its token hash, as OJP_API_TOKEN"
                ) from None
            if status == 429:
                raise RuntimeError("OJP rate limit reached; retry later") from None
            raise RuntimeError(f"OJP API returned HTTP {status}; transport data is unavailable") from None
        except httpx.HTTPError:
            raise RuntimeError("OJP API is unavailable or timed out; retry later") from None
        return parse_delivery(bytes(content), kind)

    def exchange(self, document: ET.Element, kind: str, key: tuple, force_refresh: bool = False) -> tuple[ET.Element, dict]:
        if not self.token:
            raise RuntimeError("OJP_API_TOKEN is not configured; set it in the server environment and restart")
        cache_key = (kind, *key)
        with self.lock:
            now = time.monotonic()
            entry = self.cache.get(cache_key)
            cached = not force_refresh and entry is not None and now < entry[0]
            if cached:
                self.cache.move_to_end(cache_key)
                _, delivery, retrieved_at, requested_at = entry
            else:
                while self.requests and now - self.requests[0] >= 60:
                    self.requests.popleft()
                if len(self.requests) >= self.rate_limit:
                    raise RuntimeError("Local OJP request limit reached; retry in one minute")
                self.requests.append(now)
                delivery = self._fetch(document, kind)
                retrieved_at = utc_now().isoformat()
                requested_at = text(document, ".//ojp:DepArrTime")
                self.cache[cache_key] = (time.monotonic() + self.cache_ttl, delivery, retrieved_at, requested_at)
                self.cache.move_to_end(cache_key)
                while len(self.cache) > MAX_CACHE_ENTRIES:
                    self.cache.popitem(last=False)
            return deepcopy(delivery), {
                **response_metadata(delivery, retrieved_at, cached), "requested_at": requested_at,
            }

    def search_stops(self, query: str, limit: int = 5) -> dict:
        query = checked_text(query, "query")
        limit = checked_limit(limit)
        document, request = request_document("LocationInformation", self.language)
        add(add(request, "InitialInput"), "Name", query)
        restrictions = add(request, "Restrictions")
        add(restrictions, "Type", "stop")
        add(restrictions, "NumberOfResults", limit)
        add(restrictions, "IncludePtModes", "true")
        delivery, metadata = self.exchange(document, "LocationInformation", (query, limit))
        stops = []
        for result in delivery.findall("ojp:PlaceResult", NS):
            place = result.find("ojp:Place", NS)
            stop_id = text(place, "ojp:StopPlace/ojp:StopPlaceRef")
            name = text(place, "ojp:StopPlace/ojp:StopPlaceName/ojp:Text")
            if stop_id is None or name is None:
                raise RuntimeError("OJP returned an incomplete stop result")
            stops.append({
                "stop_id": stop_id,
                "name": name,
                "latitude": number(place, "ojp:GeoPosition/siri:Latitude"),
                "longitude": number(place, "ojp:GeoPosition/siri:Longitude"),
                "modes": [mode.text for mode in place.findall("ojp:Mode/ojp:PtMode", NS)],
                "probability": number(result, "ojp:Probability"),
            })
        return {**metadata, "query": query, "stops": stops[:limit], "truncated": len(stops) > limit}

    def departures(self, at: str | None = None, limit: int = 10, force_refresh: bool = False) -> dict:
        requested_at = request_time(at)
        limit = checked_limit(limit)
        document, request = request_document("StopEvent", self.language)
        location = place_reference(request, "Location", self.origin["stop_id"], self.origin["name"])
        add(location, "DepArrTime", requested_at)
        params = add(request, "Params")
        add(params, "NumberOfResults", limit)
        add(params, "StopEventType", "departure")
        add(params, "IncludePreviousCalls", "false")
        add(params, "IncludeOnwardCalls", "false")
        add(params, "UseRealtimeData", "explanatory")
        add(params, "IncludeSituationsContext", "true")
        delivery, metadata = self.exchange(document, "StopEvent", (at, limit), force_refresh)
        departures = []
        for result in delivery.findall("ojp:StopEventResult", NS):
            event = result.find("ojp:StopEvent", NS)
            if event is None:
                raise RuntimeError("OJP returned an incomplete departure event")
            call = call_details(event.find("ojp:ThisCall/ojp:CallAtStop", NS))
            if call["departure"] is None:
                raise RuntimeError("OJP returned a departure event without a departure time")
            departures.append({"call": call, "service": service_details(event.find("ojp:Service", NS))})
        departures.sort(key=lambda event: source_time(
            event["call"]["departure"]["estimated_at"] or event["call"]["departure"]["scheduled_at"]
        ))
        return {
            **metadata,
            "origin": deepcopy(self.origin),
            "departures": departures[:limit],
            "truncated": len(departures) > limit,
            "situations": situations(delivery),
            "realtime_note": REALTIME_NOTE,
        }

    def journeys(self, destination_stop_id: str, at: str | None = None, arrive_by: bool = False,
                 limit: int = 3, max_transfers: int | None = None, force_refresh: bool = False) -> dict:
        destination_stop_id = checked_stop_id(destination_stop_id)
        limit = checked_limit(limit, 6)
        if type(arrive_by) is not bool:
            raise ValueError("arrive_by must be a boolean")
        if arrive_by and at is None:
            raise ValueError("at is required when arrive_by is true")
        if max_transfers is not None and (type(max_transfers) is not int or not 0 <= max_transfers <= 8):
            raise ValueError("max_transfers must be an integer between 0 and 8, or omitted")
        requested_at = request_time(at)
        document, request = request_document("Trip", self.language)
        origin = place_reference(request, "Origin", self.origin["stop_id"], self.origin["name"])
        destination = place_reference(request, "Destination", destination_stop_id, destination_stop_id)
        add(destination if arrive_by else origin, "DepArrTime", requested_at)
        params = add(request, "Params")
        add(params, "NumberOfResultsBefore", limit if arrive_by else 0)
        add(params, "NumberOfResultsAfter", 0 if arrive_by else limit)
        add(params, "UseRealtimeData", "explanatory")
        if max_transfers is not None:
            add(params, "TransferLimit", max_transfers)
        add(params, "IncludeSituationsContext", "true")
        add(params, "IncludeIntermediateStops", "false")
        delivery, metadata = self.exchange(
            document, "Trip", (destination_stop_id, at, arrive_by, limit, max_transfers), force_refresh,
        )
        trips = [trip_details(result.find("ojp:Trip", NS)) for result in delivery.findall("ojp:TripResult", NS)]
        return {
            **metadata,
            "origin": deepcopy(self.origin),
            "destination_stop_id": destination_stop_id,
            "time_mode": "arrive_by" if arrive_by else "depart_after",
            "max_transfers": max_transfers,
            "trips": trips[:limit],
            "truncated": len(trips) > limit,
            "situations": situations(delivery),
            "realtime_note": REALTIME_NOTE,
            "walking_time_from_venue_included": False,
        }


def register(mcp, config: dict):
    client = TransportClient(config)

    @mcp.tool(
        name="get_transport_origin",
        description="Get the fixed departure stop near Kraftwerk, Zurich local time, and token configuration status. No network request or credential disclosure.",
    )
    def get_transport_origin() -> dict:
        return client.origin_context()

    @mcp.tool(
        name="search_transport_stops",
        description="Search official OJP destination stops by name. Use returned stop_id values for journey planning; ask the user when the destination is ambiguous. Does not change the fixed origin.",
    )
    def search_transport_stops(query: str, limit: int = 5) -> dict:
        try:
            return client.search_stops(query, limit)
        except (ValueError, RuntimeError) as error:
            raise ToolError(str(error)) from None

    @mcp.tool(
        name="get_departures",
        description=(
            "Get the next departures only at the configured Kraftwerk-area stop. Includes scheduled and "
            "estimated times, platforms, cancellation flags, and available incident reports. at is an ISO "
            "8601 datetime with offset; omit for now. Missing estimates are not evidence of on-time service. "
            "Use force_refresh for time-sensitive checks."
        ),
    )
    def get_departures(at: str | None = None, limit: int = 10, force_refresh: bool = False) -> dict:
        try:
            return client.departures(at, limit, force_refresh)
        except (ValueError, RuntimeError) as error:
            raise ToolError(str(error)) from None

    @mcp.tool(
        name="plan_journey",
        description=(
            "Plan public transport from the fixed Kraftwerk-area stop to a stop_id from search_transport_stops. "
            "Origin cannot be changed. Omit at to leave now; use an offset-aware ISO 8601 datetime and "
            "arrive_by=true for an arrival deadline. max_transfers=0 requests a direct connection. "
            "Returns legs, transfers, times, platforms, and supplied disruptions. Does not include the walk "
            "from Kraftwerk to the origin stop, tickets, or guaranteed accessibility."
        ),
    )
    def plan_journey(destination_stop_id: str, at: str | None = None, arrive_by: bool = False,
                     limit: int = 3, max_transfers: int | None = None, force_refresh: bool = False) -> dict:
        try:
            return client.journeys(destination_stop_id, at, arrive_by, limit, max_transfers, force_refresh)
        except (ValueError, RuntimeError) as error:
            raise ToolError(str(error)) from None