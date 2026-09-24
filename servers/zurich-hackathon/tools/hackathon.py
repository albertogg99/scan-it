from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import ssl
import time
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
import httpx
from markdownify import markdownify
from mcp.server.mcpserver.exceptions import ToolError


def extract_content(html: str, source_url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    content = soup.find("main") or soup.find("article")
    if content is None:
        raise ValueError(f"No readable page content found at {source_url}")
    for element in content.select("script, style, nav, footer, noscript, svg, template, form, button"):
        element.decompose()
    for link in content.find_all("a", href=True):
        link["href"] = urljoin(source_url, str(link["href"]))
    for image in content.find_all("img", src=True):
        image["src"] = urljoin(source_url, str(image["src"]))
    text = markdownify(str(content), heading_style="ATX").strip()
    if not text:
        raise ValueError(f"No readable page content found at {source_url}")
    return text


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
            {"topic": topic, "title": page["title"], "source_url": page["url"]}
            for topic, page in self.pages.items()
        ]

    def read(self, topic: str, force_refresh: bool = False) -> dict:
        if topic not in self.pages:
            raise ValueError(f"Unknown topic {topic!r}. Use list_hackathon_topics to find valid topics.")
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
            raise ValueError(f"Expected an HTML handbook page at {page['url']}")
        result = {
            "topic": topic,
            "title": page["title"],
            "source_url": page["url"],
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
                return None, {"topic": topic, "source_url": self.pages[topic]["url"], "error": str(error)}

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
                    "source_url": page["source_url"],
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
        description="List available Zurich Hackathon handbook topics and their official source URLs.",
    )
    def list_hackathon_topics() -> list[dict]:
        return handbook.topics()

    @mcp.tool(
        name="get_hackathon_info",
        description=(
            "Read an official Zurich Hackathon handbook topic, including source URL and fetch time. "
            "Use list_hackathon_topics for topic IDs. Set force_refresh to bypass the short-lived cache."
        ),
    )
    def get_hackathon_info(topic: str = "overview", force_refresh: bool = False) -> dict:
        try:
            return handbook.read(topic, force_refresh)
        except (httpx.HTTPError, ValueError) as error:
            raise ToolError(f"Could not read handbook topic {topic!r}: {error}") from error

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