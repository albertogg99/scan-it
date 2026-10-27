from collections import OrderedDict, deque
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
import html
import json
import logging
import math
import os
import re
import ssl
from threading import RLock
import time
import unicodedata
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations


logger = logging.getLogger("dynamic-mcp.emt-bus-stop")
MAX_RESPONSE_BYTES = 2_000_000
MAX_CACHE_ENTRIES = 128
RESPONSE_FRESHNESS_SECONDS = 120
TOKEN_REFRESH_MARGIN_SECONDS = 60
BEYOND_HORIZON_SECONDS = 999999
OK_CODES = ("00", "01")
TEXT_LIMIT = 400
MAX_INCIDENTS_ARRIVALS = 5
MAX_INCIDENTS_LINE = 10
LINE_PATTERN = re.compile(r"[A-Za-z0-9]{1,5}")
READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True,
)
TAG_PATTERN = re.compile(r"<[^>]*>")
NOT_CONFIGURED = (
    "EMT MobilityLabs credentials are not configured; set EMT_CLIENT_ID and EMT_PASSKEY "
    "(or EMT_EMAIL and EMT_PASSWORD) in the server environment and restart"
)
UNAVAILABLE = "EMT API is unavailable or timed out; this does not mean no buses are running"
MIN_SIMILARITY = 0.6


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def clean_text(value: object, limit: int = TEXT_LIMIT) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = html.unescape(TAG_PATTERN.sub(" ", value))
    stripped = re.sub(r"[\x00-\x1f\x7f]", " ", stripped)
    stripped = " ".join(stripped.split())
    if not stripped:
        return None
    return stripped if len(stripped) <= limit else stripped[:limit - 1].rstrip() + "\u2026"


def dig(value: object, *path: object) -> object:
    for step in path:
        if isinstance(step, int):
            if not isinstance(value, list) or not -len(value) <= step < len(value):
                return None
        elif not isinstance(value, dict):
            return None
        value = value[step] if isinstance(step, int) else value.get(step)
    return value


def as_list(value: object) -> list:
    return value if isinstance(value, list) else []


def http_url(value: object) -> str | None:
    if isinstance(value, dict):
        value = value.get("@url")
    return value if isinstance(value, str) and value.startswith(("https://", "http://")) and len(value) <= 500 else None


def checked_line(value: object) -> str:
    if not isinstance(value, str) or LINE_PATTERN.fullmatch(value.strip()) is None:
        raise ValueError("line must be a public line label of 1 to 5 letters or digits, for example 5, N1 or C03")
    return value.strip().upper()


def checked_limit(value: object, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"limit must be an integer between 1 and {maximum}")
    return value


def checked_direction(value: object) -> int:
    if type(value) is not int or value not in (1, 2):
        raise ValueError("direction must be 1 or 2")
    return value


def checked_date(value: object) -> date:
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise ValueError("date must be an ISO date in YYYY-MM-DD format")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("date must be a valid calendar date in YYYY-MM-DD format") from None


def read_credential(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if any(not 33 <= ord(character) <= 126 for character in value):
        raise ValueError(f"{name} must be a single ASCII token without whitespace")
    return value


def number(value: object) -> float | int | None:
    if type(value) in (int, float) and math.isfinite(value):
        return value
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
        if math.isfinite(parsed):
            return int(parsed) if parsed.is_integer() else parsed
    return None


def coordinates(geometry: object) -> dict | None:
    pair = dig(geometry, "coordinates")
    if isinstance(pair, list) and len(pair) >= 2:
        longitude, latitude = number(pair[0]), number(pair[1])
        if longitude is not None and latitude is not None:
            return {"latitude": latitude, "longitude": longitude}
    return None


def minutes_text(value: object) -> int | float | None:
    return number(value)


def normalized(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())


def similarity(query: str, name: str) -> float:
    if not query or not name:
        return 0.0
    if query == name:
        return 1.0
    if query in name:
        return 0.9
    words = name.split()
    hits = sum(any(SequenceMatcher(None, token, word).ratio() >= 0.8 for word in words) for token in query.split())
    return max(SequenceMatcher(None, query, name).ratio(), 0.85 * hits / len(query.split()))


class EmtClient:
    def __init__(self, config: dict):
        self.base_url = config.get("base_url", "https://openapi.emtmadrid.es")
        if not isinstance(self.base_url, str) or not self.base_url.startswith("https://") or self.base_url.endswith("/"):
            raise ValueError("base_url must be an https URL without a trailing slash")
        self.timeout = config.get("timeout_seconds", 15)
        self.arrivals_ttl = config.get("arrivals_cache_ttl_seconds", 20)
        self.static_ttl = config.get("static_cache_ttl_seconds", 3600)
        for label, value, minimum, maximum in (
            ("timeout_seconds", self.timeout, 0.1, 60),
            ("arrivals_cache_ttl_seconds", self.arrivals_ttl, 0, 60),
            ("static_cache_ttl_seconds", self.static_ttl, 0, 86400),
        ):
            if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
                raise ValueError(f"{label} must be between {minimum} and {maximum}")
        self.rate_limit = config.get("requests_per_minute", 60)
        if type(self.rate_limit) is not int or not 1 <= self.rate_limit <= 600:
            raise ValueError("requests_per_minute must be an integer between 1 and 600")
        self.culture = config.get("culture", "ES")
        if self.culture not in ("ES", "EN"):
            raise ValueError("culture must be ES or EN")
        self.zone = ZoneInfo(config.get("timezone", "Europe/Madrid"))
        self.source_url = config.get("source_url", "https://apidocs.emtmadrid.es/")
        self.attribution = config.get("attribution", "Datos: EMT Madrid MobilityLabs")
        stop = config.get("stop")
        if not isinstance(stop, dict):
            raise ValueError("A fixed stop must be configured")
        self.stop = deepcopy(stop)
        self.stop["id"] = str(stop.get("id", ""))
        if re.fullmatch(r"[0-9]{1,6}", self.stop["id"]) is None:
            raise ValueError("stop.id must be numeric")
        if not isinstance(stop.get("name"), str) or not stop["name"].strip():
            raise ValueError("stop.name must be configured")
        client_id, passkey = read_credential("EMT_CLIENT_ID"), read_credential("EMT_PASSKEY")
        email, password = read_credential("EMT_EMAIL"), read_credential("EMT_PASSWORD")
        if client_id and passkey:
            self.login_headers = {"X-ClientId": client_id, "passKey": passkey}
        elif email and password:
            self.login_headers = {"email": email, "password": password}
        else:
            self.login_headers = None
        self.ssl_context = ssl.create_default_context()
        self.token = None
        self.token_expires = 0.0
        self.cache = OrderedDict()
        self.requests = deque()
        self.lock = RLock()

    # --- time and metadata -------------------------------------------------

    def now(self) -> datetime:
        return utc_now().astimezone(self.zone)

    def stop_ref(self) -> dict:
        return {"id": self.stop["id"], "name": self.stop["name"]}

    def envelope(self, retrieved_at: str, cached: bool) -> dict:
        return {
            "stop": self.stop_ref(),
            "source_url": self.source_url,
            "attribution": self.attribution,
            "retrieved_at": retrieved_at,
            "cached": cached,
            "timezone": self.zone.key,
            "current_time": self.now().isoformat(timespec="seconds"),
        }

    def freshness(self, timestamp: object) -> dict:
        now = utc_now()
        parsed = None
        if isinstance(timestamp, str) and timestamp:
            try:
                parsed = datetime.fromisoformat(timestamp)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=self.zone)
                parsed = parsed.astimezone(timezone.utc)
            except (ValueError, OverflowError):
                parsed = None
        age = (now - parsed).total_seconds() if parsed is not None else None
        if not timestamp:
            state = "missing"
        elif age is None:
            state = "invalid"
        elif age < 0:
            state = "future"
        elif age > RESPONSE_FRESHNESS_SECONDS:
            state = "stale"
        else:
            state = "fresh"
        return {
            "response_timestamp": parsed.astimezone(self.zone).isoformat(timespec="seconds") if parsed else None,
            "response_age_seconds": age,
            "response_freshness": state,
            "warnings": [] if state == "fresh" else [
                f"EMT response timestamp is {state}; do not describe this as current live information."
            ],
        }

    # --- HTTP --------------------------------------------------------------

    def _send(self, method: str, path: str, headers: dict, body: dict | None) -> tuple[int, dict | None]:
        now = time.monotonic()
        while self.requests and now - self.requests[0] >= 60:
            self.requests.popleft()
        if len(self.requests) >= self.rate_limit:
            raise RuntimeError("Local EMT request limit reached; retry in one minute")
        self.requests.append(now)
        try:
            with httpx.stream(
                method, f"{self.base_url}{path}",
                headers={"Accept": "application/json", "User-Agent": "DynamicMCP-EmtBusStop/1.0", **headers},
                json=body,
                timeout=self.timeout,
                verify=self.ssl_context,
                follow_redirects=False,
            ) as response:
                status = response.status_code
                content = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    content.extend(chunk)
                    if len(content) > MAX_RESPONSE_BYTES:
                        raise RuntimeError("EMT response exceeded the size limit")
        except httpx.HTTPError:
            raise RuntimeError(UNAVAILABLE) from None
        try:
            payload = json.loads(bytes(content))
        except (ValueError, UnicodeDecodeError):
            payload = None
        if not isinstance(payload, dict):
            payload = None
        if status == 401:
            return status, payload
        if status == 429:
            raise RuntimeError("EMT API rate limit reached; retry later")
        if not 200 <= status < 300:
            raise RuntimeError(f"EMT API returned HTTP {status}; bus data is unavailable, which does not mean no buses are running")
        if payload is None:
            raise RuntimeError("EMT API returned a non-JSON response; bus data is unavailable, which does not mean no buses are running")
        return status, payload

    def _login(self) -> str:
        status, payload = self._send("GET", "/v3/mobilitylabs/user/login/", self.login_headers, None)
        code = payload.get("code") if payload else None
        if status != 200 or code not in OK_CODES:
            raise RuntimeError(
                "EMT MobilityLabs login failed; check the configured credentials"
                + (f" (code {clean_text(str(code), 10)})" if code is not None else f" (HTTP {status})")
            )
        token = dig(payload, "data", 0, "accessToken")
        lifetime = number(dig(payload, "data", 0, "tokenSecExpiration"))
        if not isinstance(token, str) or not token or any(not 33 <= ord(c) <= 126 for c in token):
            raise RuntimeError("EMT MobilityLabs login returned no usable access token")
        lifetime = lifetime if lifetime is not None and lifetime > 0 else 300
        margin = TOKEN_REFRESH_MARGIN_SECONDS if lifetime > 2 * TOKEN_REFRESH_MARGIN_SECONDS else lifetime / 2
        self.token = token
        self.token_expires = time.monotonic() + lifetime - margin
        logger.info("EMT MobilityLabs login succeeded")
        return token

    def _access_token(self) -> str:
        if self.token is not None and time.monotonic() < self.token_expires:
            return self.token
        return self._login()

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        if self.login_headers is None:
            raise RuntimeError(NOT_CONFIGURED)
        for attempt in (1, 2):
            token = self._access_token()
            status, payload = self._send(method, path, {"accessToken": token}, body)
            if status == 401 or (payload or {}).get("code") == "80":
                self.token = None
                if attempt == 2:
                    raise RuntimeError("EMT rejected the access token after a new login; data is unavailable")
                continue
            break
        code = payload.get("code")
        if code not in OK_CODES:
            description = clean_text(payload.get("description"), 120) or "no description"
            raise RuntimeError(
                f"EMT API reported an error (code {clean_text(str(code), 10)}: {description}); "
                "this is not evidence that no buses are running"
            )
        return payload

    def fetch(self, ttl: float, method: str, path: str, body: dict | None = None,
              force_refresh: bool = False) -> tuple[dict, str, bool]:
        if self.login_headers is None:
            raise RuntimeError(NOT_CONFIGURED)
        key = (method, path, json.dumps(body, sort_keys=True))
        with self.lock:
            entry = self.cache.get(key)
            if not force_refresh and entry is not None and time.monotonic() < entry[0]:
                self.cache.move_to_end(key)
                return deepcopy(entry[1]), entry[2], True
            payload = self._call(method, path, body)
            retrieved_at = self.now().isoformat(timespec="seconds")
            self.cache[key] = (time.monotonic() + ttl, payload, retrieved_at)
            self.cache.move_to_end(key)
            while len(self.cache) > MAX_CACHE_ENTRIES:
                self.cache.popitem(last=False)
            return deepcopy(payload), retrieved_at, False

    def static(self, path: str, force_refresh: bool = False) -> tuple[dict, str, bool]:
        return self.fetch(self.static_ttl, "GET", path, None, force_refresh)

    # --- stop detail and line resolution ----------------------------------

    def stop_detail(self) -> tuple[dict, str, bool]:
        payload, retrieved_at, cached = self.static(f"/v1/transport/busemtmad/stops/{self.stop['id']}/detail/")
        stop = dig(payload, "data", 0, "stops", 0)
        if not isinstance(stop, dict):
            raise RuntimeError("EMT returned an incomplete stop detail")
        return stop, retrieved_at, cached

    def resolve_line(self, label: str) -> str:
        try:
            stop, _, _ = self.stop_detail()
        except RuntimeError:
            if self.login_headers is None:
                raise
            return label
        for entry in as_list(stop.get("dataLine")):
            if not isinstance(entry, dict):
                continue
            for candidate in (entry.get("label"), entry.get("line")):
                if isinstance(candidate, str) and candidate.strip().upper() == label:
                    code = entry.get("line")
                    if isinstance(code, str) and LINE_PATTERN.fullmatch(code.strip()):
                        return code.strip().upper()
        return label

    # --- tools -------------------------------------------------------------

    def connected_stops(self) -> tuple[list[dict], list[str], str, bool]:
        """Stops reachable without transfers from this stop, one entry per line, direction and stop."""
        stop, retrieved_at, cached = self.stop_detail()
        codes = []
        for entry in as_list(stop.get("dataLine")):
            code = entry.get("line") if isinstance(entry, dict) else None
            if isinstance(code, str) and LINE_PATTERN.fullmatch(code.strip()) and code.strip() not in codes:
                codes.append(code.strip())
        connections, warnings = [], []
        for code in codes:
            for direction in (1, 2):
                try:
                    payload, _, _ = self.static(f"/v1/transport/busemtmad/lines/{code}/stops/{direction}/")
                except RuntimeError:
                    warnings.append(f"Stops of line {code} direction {direction} are unavailable; results may be incomplete.")
                    continue
                stops = [s for s in as_list(dig(payload, "data", 0, "stops")) if isinstance(s, dict)]
                ids = [str(s.get("stop")) for s in stops]
                if self.stop["id"] not in ids:
                    continue
                # EMT may list this stop several times in a row; count from its last occurrence.
                start = len(ids) - 1 - ids[::-1].index(self.stop["id"])
                label = clean_text(dig(payload, "data", 0, "label"), 20) or code
                toward = clean_text(stops[-1].get("name"), 120)
                seen, count = {self.stop["id"]}, 0
                for index in range(start + 1, len(stops)):
                    if ids[index] == ids[index - 1]:
                        continue
                    count += 1
                    name = clean_text(stops[index].get("name"), 120)
                    if ids[index] in seen or not name:
                        continue
                    seen.add(ids[index])
                    connections.append({
                        "line": label, "line_code": code, "direction": direction, "toward": toward,
                        "stop_id": clean_text(ids[index], 10), "name": name, "stops": count,
                    })
        if not connections:
            raise RuntimeError("EMT returned no usable line stop lists for this stop; connected stops are unavailable")
        return connections, warnings, retrieved_at, cached

    def search_connected_stops(self, query: str, limit: int = 8) -> dict:
        if not isinstance(query, str) or not query.strip() or len(query.strip()) > 120:
            raise ValueError("query must be a stop or place name of at most 120 characters")
        limit = checked_limit(limit, 20)
        connections, warnings, retrieved_at, cached = self.connected_stops()
        wanted = normalized(query)
        scores = {}
        for name in {c["name"] for c in connections}:
            score = similarity(wanted, normalized(name))
            if score >= MIN_SIMILARITY:
                scores[name] = max(score, scores.get(name, 0))
        names = sorted(scores, key=lambda n: (-scores[n], n))[:limit]
        return {
            **self.envelope(retrieved_at, cached), "query": query.strip(), "stops": names, "warnings": warnings,
            "note": "Only stops reachable from this stop without transfers are searched. Pass a name to plan_journey.",
        }

    def plan_journey(self, stop_name: str) -> dict:
        if not isinstance(stop_name, str) or not stop_name.strip() or len(stop_name.strip()) > 120:
            raise ValueError("stop_name must be a stop name of at most 120 characters")
        connections, warnings, retrieved_at, cached = self.connected_stops()
        wanted = normalized(stop_name)
        matches = [c for c in connections if normalized(c["name"]) == wanted]
        if not matches:
            suggestions = self.search_connected_stops(stop_name, 5)["stops"]
            raise ValueError(
                f"'{clean_text(stop_name, 120)}' is not a stop reachable from stop {self.stop['id']} without transfers."
                + (f" Closest connected stops: {', '.join(suggestions)}." if suggestions else
                   " Use search_connected_stops to find a connected stop.")
            )
        options = sorted(
            ({k: c[k] for k in ("line", "line_code", "direction", "toward", "stops", "stop_id")} for c in matches),
            key=lambda o: (o["stops"], o["line"]),
        )
        return {
            **self.envelope(retrieved_at, cached), "destination": matches[0]["name"], "options": options,
            "warnings": warnings,
            "note": (
                "Direct EMT lines from this stop, scheduled network data. stops is the number of stops to ride; "
                "toward is the line's final stop in that direction. Night lines (N...) only run at night; "
                "use get_arrivals for live times."
            ),
        }

    def get_stop_info(self) -> dict:
        stop, retrieved_at, cached = self.stop_detail()
        lines = []
        for entry in as_list(stop.get("dataLine"))[:60]:
            if not isinstance(entry, dict):
                continue
            lines.append({
                "label": clean_text(entry.get("label"), 20),
                "line_code": clean_text(entry.get("line"), 20),
                "header_a": clean_text(entry.get("headerA"), 120),
                "header_b": clean_text(entry.get("headerB"), 120),
                "direction_at_stop": clean_text(entry.get("direction"), 5),
                "first_bus": clean_text(entry.get("startTime"), 10),
                "last_bus": clean_text(entry.get("stopTime"), 10),
                "min_frequency_minutes": minutes_text(entry.get("minFreq")),
                "max_frequency_minutes": minutes_text(entry.get("maxFreq")),
                "day_type": clean_text(entry.get("dayType"), 5),
            })
        return {
            **self.envelope(retrieved_at, cached),
            "name": clean_text(stop.get("name"), 120) or self.stop["name"],
            "address": clean_text(stop.get("postalAddress"), 200),
            "coordinates": coordinates(stop.get("geometry"))
            or {"latitude": self.stop.get("latitude"), "longitude": self.stop.get("longitude")},
            "panel_url": self.stop.get("source_url"),
            "lines": lines,
            "schedule_note": "first_bus, last_bus and frequencies are scheduled data for the day_type shown, not live estimates.",
        }

    def incidents_from_arrivals(self, data: object) -> list[dict]:
        incident = dig(data, "Incident", "ListaIncident", "data")
        results = []
        for item in as_list(incident)[:MAX_INCIDENTS_ARRIVALS]:
            if isinstance(item, dict):
                results.append({
                    "title": clean_text(item.get("title"), 160),
                    "description": clean_text(item.get("description")),
                    "valid_from": clean_text(item.get("rssFrom"), 40),
                    "valid_to": clean_text(item.get("rssTo"), 40),
                    "cause": clean_text(item.get("cause"), 80),
                    "effect": clean_text(item.get("effect"), 80),
                    "more_info_url": http_url(dig(item, "moreInfo")),
                })
        return results

    def get_arrivals(self, line: str | None = None, limit: int = 12, include_incidents: bool = True,
                     force_refresh: bool = False) -> dict:
        limit = checked_limit(limit, 30)
        if type(include_incidents) is not bool or type(force_refresh) is not bool:
            raise ValueError("include_incidents and force_refresh must be booleans")
        label = checked_line(line) if line is not None else None
        code = self.resolve_line(label) if label is not None else None
        path = f"/v2/transport/busemtmad/stops/{self.stop['id']}/arrives/" + (f"{code}/" if code else "")
        yes_no = "Y" if include_incidents else "N"
        body = {
            "cultureInfo": self.culture,
            "Text_StopRequired_YN": "Y",
            "Text_EstimationsRequired_YN": "Y",
            "Text_IncidencesRequired_YN": yes_no,
            "DateTime_Referenced_Incidencies_YYYYMMDD": self.now().strftime("%Y%m%d"),
        }
        payload, retrieved_at, cached = self.fetch(self.arrivals_ttl, "POST", path, body, force_refresh)
        data = dig(payload, "data", 0)
        if data is None and payload.get("data") == []:
            data = {}
        if not isinstance(data, dict):
            raise RuntimeError("EMT returned an incomplete arrivals response")
        freshness = self.freshness(payload.get("datetime"))
        base = datetime.fromisoformat(freshness["response_timestamp"]) if (
            freshness["response_freshness"] == "fresh" and freshness["response_timestamp"]
        ) else self.now()
        arrivals = []
        for item in as_list(data.get("Arrive")):
            if not isinstance(item, dict):
                continue
            seconds = number(item.get("estimateArrive"))
            beyond = seconds is not None and seconds >= BEYOND_HORIZON_SECONDS
            known = seconds is not None and not beyond and seconds >= 0
            arrivals.append({
                "line": clean_text(item.get("line"), 20),
                "destination": clean_text(item.get("destination"), 120),
                "bus": clean_text(str(item["bus"]), 20) if item.get("bus") is not None else None,
                "estimated_seconds": int(seconds) if known else None,
                "estimated_minutes": math.floor(seconds / 60 + 0.5) if known else None,
                "estimated_arrival_local": (base + timedelta(seconds=seconds)).isoformat(timespec="seconds") if known else None,
                "beyond_horizon": beyond,
                "distance_m": number(item.get("DistanceBus")),
            })
        arrivals.sort(key=lambda a: (a["estimated_seconds"] is None, a["estimated_seconds"] or 0))
        stop_info = dig(data, "StopInfo", 0)
        result = {
            **self.envelope(retrieved_at, cached),
            **freshness,
            "line_filter": label,
            "arrivals": arrivals[:limit],
            "total_arrivals": len(arrivals),
            "truncated": len(arrivals) > limit,
            "beyond_horizon_note": "beyond_horizon=true means the bus is more than 45 minutes away (90 for night lines), not that there is no bus.",
            "stop_address": clean_text(dig(stop_info, "Direction"), 200),
        }
        if include_incidents:
            result["incidents"] = self.incidents_from_arrivals(data)
        return result

    def get_line_info(self, line: str, date_text: str | None = None) -> dict:
        label = checked_line(line)
        day = checked_date(date_text) if date_text is not None else self.now().date()
        code = self.resolve_line(label)
        stamp = day.strftime("%Y%m%d")
        payload, retrieved_at, cached = self.static(f"/v1/transport/busemtmad/lines/{code}/info/{stamp}/")
        info = dig(payload, "data", 0)
        if not isinstance(info, dict):
            raise RuntimeError("EMT returned an incomplete line detail")
        warnings = []
        day_type, strike = None, None
        try:
            calendar, _, _ = self.static(f"/v1/transport/busemtmad/calendar/{stamp}/{stamp}/")
            for entry in as_list(calendar.get("data")):
                if isinstance(entry, dict) and str(entry.get("date", "")).startswith(day.strftime("%d/%m/%Y")):
                    day_type = clean_text(entry.get("dayType"), 5)
                    strike = {"S": True, "N": False}.get(entry.get("strike"))
        except RuntimeError as error:
            warnings.append(f"Calendar unavailable: {error}")
        timetable = []
        for entry in as_list(info.get("timeTable"))[:6]:
            if not isinstance(entry, dict):
                continue
            directions = {}
            for index in (1, 2):
                block = entry.get(f"Direction{index}")
                if isinstance(block, dict):
                    directions[f"direction_{index}"] = {
                        "first_bus": clean_text(block.get("StartTime"), 10),
                        "last_bus": clean_text(block.get("StopTime"), 10),
                        "min_frequency_minutes": minutes_text(block.get("MinimunFrequency")),
                        "max_frequency_minutes": minutes_text(block.get("MaximumFrequency")),
                        "frequency_text": clean_text(block.get("FrequencyText"), 160),
                    }
            timetable.append({"day_type": clean_text(entry.get("idDayType"), 20), **directions})
        return {
            **self.envelope(retrieved_at, cached),
            "line": clean_text(info.get("label"), 20) or label,
            "line_code": clean_text(info.get("line"), 20) or code,
            "name_a": clean_text(info.get("nameA"), 120),
            "name_b": clean_text(info.get("nameB"), 120),
            "date": day.isoformat(),
            "date_reference": clean_text(info.get("dateRef"), 40),
            "calendar_day_type": day_type,
            "is_strike_day": strike,
            "timetable": timetable,
            "warnings": warnings,
            "schedule_note": "Scheduled first/last bus and frequency data, not live estimates.",
        }

    def get_line_stops(self, line: str, direction: int = 1, limit: int = 80) -> dict:
        label = checked_line(line)
        direction = checked_direction(direction)
        limit = checked_limit(limit, 200)
        code = self.resolve_line(label)
        payload, retrieved_at, cached = self.static(f"/v1/transport/busemtmad/lines/{code}/stops/{direction}/")
        stops = dig(payload, "data", 0, "stops")
        if not isinstance(stops, list):
            raise RuntimeError("EMT returned an incomplete line stop list")
        entries, position = [], None
        for index, item in enumerate(stops, start=1):
            if not isinstance(item, dict):
                continue
            is_this = str(item.get("stop")) == self.stop["id"]
            if is_this:
                position = index
            entries.append({
                "position": index,
                "stop_id": clean_text(str(item.get("stop")), 10) if item.get("stop") is not None else None,
                "name": clean_text(item.get("name"), 120),
                "address": clean_text(item.get("postalAddress"), 200),
                "coordinates": coordinates(item.get("geometry")),
                "is_this_stop": is_this,
            })
        return {
            **self.envelope(retrieved_at, cached),
            "line": label,
            "line_code": code,
            "direction": direction,
            "total_stops": len(entries),
            "this_stop_position": position,
            "stops": entries[:limit],
            "truncated": len(entries) > limit,
        }

    def get_line_timetable(self, line: str) -> dict:
        label = checked_line(line)
        code = self.resolve_line(label)
        payload, retrieved_at, cached = self.static(f"/v1/transport/busemtmad/lines/{code}/timetable/")
        rows = []
        for entry in as_list(payload.get("data"))[:20]:
            if isinstance(entry, dict):
                rows.append({
                    "day_type": clean_text(entry.get("dayType"), 5),
                    "valid_from": clean_text(entry.get("dateIni"), 40),
                    "valid_to": clean_text(entry.get("dateEnd"), 40),
                    "direction_a": {
                        "first_service": clean_text(entry.get("firstTimeServiceA"), 10),
                        "last_service": clean_text(entry.get("endTimeServiceA"), 10),
                    },
                    "direction_b": {
                        "first_service": clean_text(entry.get("firstTimeServiceB"), 10),
                        "last_service": clean_text(entry.get("endTimeServiceB"), 10),
                    },
                })
        return {
            **self.envelope(retrieved_at, cached),
            "line": label,
            "line_code": code,
            "timetable": rows,
            "schedule_note": "Scheduled first/last service times, not live estimates.",
        }

    def get_line_incidents(self, line: str | None = None) -> dict:
        label = checked_line(line) if line is not None else None
        code = self.resolve_line(label) if label is not None else "all"
        payload, retrieved_at, cached = self.fetch(
            self.arrivals_ttl, "GET", f"/v1/transport/busemtmad/lines/incidents/{quote(code)}/",
        )
        items = as_list(dig(payload, "data", 0, "item"))
        incidents = []
        for item in items[:MAX_INCIDENTS_LINE]:
            if isinstance(item, dict):
                incidents.append({
                    "title": clean_text(item.get("title"), 160),
                    "description": clean_text(item.get("description")),
                    "lines": clean_text(item.get("category"), 120),
                    "valid_from": clean_text(item.get("rssAfectaDesde"), 40),
                    "valid_to": clean_text(item.get("rssAfectaHasta"), 40),
                    "cause": clean_text(item.get("GoogleTransitCause"), 80),
                    "effect": clean_text(item.get("GoogleTransitEffect"), 80),
                    "more_info_url": http_url(item.get("enclosure")),
                })
        return {
            **self.envelope(retrieved_at, cached),
            "line_filter": label,
            "incidents": incidents,
            "total_incidents": len(items),
            "truncated": len(items) > MAX_INCIDENTS_LINE,
            "note": "No listed notice does not guarantee disruption-free service.",
        }


def register(mcp, config: dict):
    client = EmtClient(config)

    def guarded(function, *args):
        try:
            return function(*args)
        except (ValueError, RuntimeError) as error:
            raise ToolError(str(error)) from None

    @mcp.tool(
        name="get_stop_info",
        description=(
            "Get the fixed EMT stop 72 (Cibeles - Casa de America): address, coordinates and the lines serving it "
            "with today's scheduled first/last bus and frequency range per direction. Scheduled data, not live."
        ),
        annotations=READ_ONLY,
    )
    def get_stop_info() -> dict:
        return guarded(client.get_stop_info)

    @mcp.tool(
        name="search_connected_stops",
        description=(
            "Search the stops reachable from stop 72 without transfers by a free-text name (accents and case are "
            "ignored, typos tolerated). Returns the most similar stop names, best first. Pass one of them to "
            "plan_journey. An empty list means no connected stop resembles the text."
        ),
        annotations=READ_ONLY,
    )
    def search_connected_stops(query: str, limit: int = 8) -> dict:
        return guarded(client.search_connected_stops, query, limit)

    @mcp.tool(
        name="plan_journey",
        description=(
            "Given a stop name from search_connected_stops, list the EMT lines that go there directly from stop 72: "
            "line, direction (1 or 2) and toward (final stop), and the number of stops to ride, fewest first. "
            "Fails with suggestions if the name is not a connected stop. Scheduled network data; use get_arrivals "
            "for live times."
        ),
        annotations=READ_ONLY,
    )
    def plan_journey(stop_name: str) -> dict:
        return guarded(client.plan_journey, stop_name)

    @mcp.tool(
        name="get_arrivals",
        description=(
            "Get live bus arrival estimates at stop 72, sorted by arrival time, with minutes, seconds, distance, "
            "destination and bus id, plus notices affecting the stop. line is an optional public label such as 5 "
            "or N1. beyond_horizon=true means more than 45 minutes (90 for night lines), not no bus. Use "
            "force_refresh for time-sensitive checks; responses are cached about 20 seconds."
        ),
        annotations=READ_ONLY,
    )
    def get_arrivals(line: str | None = None, limit: int = 12, include_incidents: bool = True,
                     force_refresh: bool = False) -> dict:
        return guarded(client.get_arrivals, line, limit, include_incidents, force_refresh)

    @mcp.tool(
        name="get_line_info",
        description=(
            "Get a line's headers and scheduled timetable (first/last bus and frequency per day type and direction) "
            "for an ISO date (YYYY-MM-DD, default today in Madrid), with the calendar day type and strike flag."
        ),
        annotations=READ_ONLY,
    )
    def get_line_info(line: str, date: str | None = None) -> dict:
        return guarded(client.get_line_info, line, date)

    @mcp.tool(
        name="get_line_stops",
        description=(
            "List a line's ordered stops for direction 1 or 2 with name, address and coordinates; the entry with "
            "is_this_stop=true is stop 72 when the line serves it."
        ),
        annotations=READ_ONLY,
    )
    def get_line_stops(line: str, direction: int = 1, limit: int = 80) -> dict:
        return guarded(client.get_line_stops, line, direction, limit)

    @mcp.tool(
        name="get_line_timetable",
        description="Get scheduled first and last service times per direction and day type for a line. Not live data.",
        annotations=READ_ONLY,
    )
    def get_line_timetable(line: str) -> dict:
        return guarded(client.get_line_timetable, line)

    @mcp.tool(
        name="get_line_incidents",
        description="Get current EMT service notices for one line, or for all lines when line is omitted (at most 10).",
        annotations=READ_ONLY,
    )
    def get_line_incidents(line: str | None = None) -> dict:
        return guarded(client.get_line_incidents, line)
