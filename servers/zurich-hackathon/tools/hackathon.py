from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import re
import ssl
import time
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
import httpx
from markdownify import markdownify
from mcp.server.mcpserver.exceptions import ToolError


def strip_web_urls(text: str) -> str:
    return re.sub(r"(?i)\b(?:https?://|www\.)[^\s<>\[\]()*`\"']+", "", text).strip()


def extract_embedded_faq(soup: BeautifulSoup) -> str:
    chunks = []
    prefix = "self.__next_f.push("
    decoder = json.JSONDecoder()
    for script in soup.find_all("script"):
        text = script.get_text().strip()
        if not text.startswith(prefix):
            continue
        try:
            payload, _ = decoder.raw_decode(text[len(prefix):])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, list) and len(payload) > 1 and payload[0] == 1 and isinstance(payload[1], str):
            chunks.append(payload[1])
    records = {}
    for line in "".join(chunks).splitlines():
        record_id, separator, value = line.partition(":")
        if separator:
            try:
                records[record_id] = json.loads(value)
            except json.JSONDecodeError:
                continue

    def render(node, active: set[str]) -> str:
        if isinstance(node, str):
            if node.startswith("$$"):
                return node[1:]
            if node.startswith("$"):
                record_id = node[2:] if node.startswith("$L") else node[1:]
                if record_id in active:
                    return ""
                return render(records.get(record_id), active | {record_id})
            return node
        if isinstance(node, list):
            if len(node) == 4 and node[0] == "$" and isinstance(node[3], dict):
                text = render(node[3].get("children"), active)
                return text if node[1] in ("a", "span", "strong", "em", "b", "i") else text + "\n\n"
            return "".join(render(child, active) for child in node)
        return ""

    for record_id, node in records.items():
        if isinstance(node, list) and len(node) == 4 and node[0] == "$" and node[1] == "section":
            text = render(node, {record_id}).strip()
            if text.partition("\n")[0] == "FAQ":
                return text
    return ""


def extract_content(html: str, source_url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    content = soup.find("main") or soup.find("article")
    if content is None:
        raise ValueError("No readable handbook content found")
    for heading in content.find_all("h2"):
        if heading.get_text(strip=True) == "FAQ":
            faq = extract_embedded_faq(soup)
            if faq and heading.parent is not None:
                section = soup.new_tag("section")
                for paragraph in faq.split("\n\n"):
                    if paragraph.strip():
                        block = soup.new_tag("h2" if paragraph.strip() == "FAQ" else "p")
                        block.string = paragraph.strip()
                        section.append(block)
                heading.parent.replace_with(section)
            break
    for element in content.select(
        "script, style, nav, footer, noscript, svg, template, form, iframe, object, embed"
    ):
        element.decompose()
    for button in content.find_all("button"):
        button.unwrap()
    for link in content.find_all("a"):
        label = link.get_text(" ", strip=True)
        destination = urlsplit(urljoin(source_url, str(link.get("href", ""))))
        if label.rstrip("/") == destination.netloc + destination.path.rstrip("/") or re.fullmatch(
            r"(?i)(?:[a-z0-9-]+\.)+[a-z]{2,}(?:[/:?#]\S*)?", label
        ):
            link.decompose()
        else:
            link.replace_with(strip_web_urls(label))
    for image in content.find_all("img"):
        image.replace_with(str(image.get("alt", "")))
    text = strip_web_urls(markdownify(str(content), heading_style="ATX"))
    if not text:
        raise ValueError("No readable handbook content found")
    return text


def describe_fetch_error(error: Exception) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        return f"The handbook returned HTTP {error.response.status_code}."
    if isinstance(error, httpx.TimeoutException):
        return "The handbook request timed out. Try again later."
    if isinstance(error, httpx.HTTPError):
        return "Could not connect to the handbook. Check the MCP server's network and TLS configuration."
    return strip_web_urls(str(error))


class Handbook:
    def __init__(self, config: dict):
        self.pages = config["pages"]
        self.timeout = float(config.get("timeout_seconds", 15))
        self.cache_ttl = float(config.get("cache_ttl_seconds", 300))
        self.ssl_context = ssl.create_default_context()
        self.cache: dict[str, tuple[float, dict]] = {}
        if not self.pages or self.timeout <= 0 or self.cache_ttl < 0:
            raise ValueError("Handbook pages, a positive timeout, and a nonnegative cache TTL are required")
        for page in self.pages.values():
            parsed = urlsplit(page["url"])
            if parsed.scheme != "https" or parsed.netloc not in {"zh.ai-weeks.ch", "ai-weeks.ch"}:
                raise ValueError(f"Only official AI Weeks HTTPS pages are allowed: {page['url']}")

    def topics(self) -> list[dict]:
        return [
            {"topic": topic, "title": page["title"]}
            for topic, page in self.pages.items()
        ]

    def read(self, topic: str, force_refresh: bool = False) -> dict:
        if topic not in self.pages:
            raise ValueError("Unknown topic. Use list_hackathon_topics to find valid topics.")
        cached = self.cache.get(topic)
        if not force_refresh and cached is not None and time.monotonic() < cached[0]:
            return {**cached[1], "cached": True}
        page = self.pages[topic]
        response = httpx.get(
            page["url"],
            timeout=self.timeout,
            follow_redirects=False,
            verify=self.ssl_context,
            headers={"User-Agent": "DynamicMCP-Hackathon/1.0", "Accept": "text/html"},
        )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type not in {"text/html", "application/xhtml+xml"}:
            raise ValueError("Expected an HTML handbook page")
        result = {
            "topic": topic,
            "title": page["title"],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "content": extract_content(response.text, page["url"]),
        }
        self.cache[topic] = (time.monotonic() + self.cache_ttl, result)
        return {**result, "cached": False}

    def search(self, query: str, limit: int = 5) -> dict:
        terms = query.casefold().split()
        if not terms:
            raise ValueError("Provide at least one search word")
        if not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")

        def read_topic(topic: str) -> tuple[dict | None, dict | None]:
            try:
                return self.read(topic), None
            except (httpx.HTTPError, ValueError) as error:
                return None, {"topic": topic, "error": describe_fetch_error(error)}

        matches = []
        errors = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            for page, error in executor.map(read_topic, self.pages):
                if error is not None:
                    errors.append(error)
                if page is None:
                    continue
                title = page["title"].casefold()
                content = page["content"].casefold()
                if not all(term in f"{title}\n{content}" for term in terms):
                    continue
                position = max(0, content.find(terms[0]) - 120)
                matches.append({
                    "topic": page["topic"],
                    "title": page["title"],
                    "fetched_at": page["fetched_at"],
                    "snippet": page["content"][position:position + 600],
                    "score": sum(content.count(term) + 5 * title.count(term) for term in terms),
                })
        matches.sort(key=lambda match: (-match["score"], match["topic"]))
        return {
            "query": query,
            "matches": matches[:limit],
            "total_matches": len(matches),
            "searched_topics": len(self.pages),
            "complete": not errors,
            "errors": errors,
        }


def register(mcp, config: dict):
    handbook = Handbook(config)

    @mcp.tool(
        name="list_hackathon_topics",
        description="List available Zurich Hackathon topic IDs and titles. Read them using get_hackathon_info.",
    )
    def list_hackathon_topics() -> list[dict]:
        return handbook.topics()

    @mcp.tool(
        name="get_hackathon_info",
        description=(
            "Get Zurich Hackathon information fetched by the server, with full text and fetch time, "
            "not links for the client to browse. Use list_hackathon_topics for topic IDs. "
            "Set force_refresh to bypass the short-lived cache."
        ),
    )
    def get_hackathon_info(topic: str = "overview", force_refresh: bool = False) -> dict:
        try:
            return handbook.read(topic, force_refresh)
        except (httpx.HTTPError, ValueError) as error:
            raise ToolError(describe_fetch_error(error)) from error

    @mcp.tool(
        name="search_hackathon_info",
        description=(
            "Search official Zurich Hackathon pages for all query words, ignoring case. "
            "Returns ranked excerpts and retrieval errors; read matching topics for complete details."
        ),
    )
    def search_hackathon_info(query: str, limit: int = 5) -> dict:
        try:
            return handbook.search(query, limit)
        except ValueError as error:
            raise ToolError(str(error)) from error