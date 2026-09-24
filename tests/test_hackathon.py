import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import httpx
import yaml


SERVER_DIR = Path(__file__).resolve().parents[1] / "servers" / "zurich-hackathon"
spec = importlib.util.spec_from_file_location("hackathon", SERVER_DIR / "tools" / "hackathon.py")
hackathon = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hackathon)


class HackathonTests(unittest.TestCase):
    def setUp(self):
        self.url = "https://zh.ai-weeks.ch/schedule-2026"
        self.config = {
            "pages": {"schedule": {"title": "Schedule 2026", "url": self.url}},
            "cache_ttl_seconds": 300,
        }
        self.handbook = hackathon.Handbook(self.config)

    def response(self, content: str, status: int = 200, content_type: str = "text/html"):
        return httpx.Response(
            status,
            text=content,
            headers={"content-type": content_type},
            request=httpx.Request("GET", self.url),
        )

    def test_extracts_content_without_links_images_or_navigation(self):
        html = (
            '<html><nav>Outside navigation</nav><main><h1>Schedule</h1>'
            '<nav>Inside navigation</nav><script>hidden code</script>'
            '<style>hidden style</style><table><tr><th>Time</th><th>Event</th></tr>'
            '<tr><td>12:00</td><td>Submission</td></tr></table>'
            '<a href="/challenges">Challenges</a><img alt="Map" src="/map.png">'
            '</main><footer>Footer</footer></html>'
        )
        content = hackathon.extract_content(html, self.url)
        self.assertIn("# Schedule", content)
        self.assertIn("12:00", content)
        self.assertIn("Submission", content)
        self.assertIn("Challenges", content)
        self.assertIn("Map", content)
        self.assertNotIn("https://", content)
        self.assertNotIn("](", content)
        self.assertNotIn("navigation", content)
        self.assertNotIn("hidden", content)
        self.assertNotIn("Footer", content)

    def test_empty_or_unrecognized_page_is_not_reported_as_information(self):
        for html in ("<html>Loading...</html>", "<main><script>Loading...</script></main>"):
            with self.subTest(html=html), self.assertRaises(ValueError):
                hackathon.extract_content(html, self.url)

    def test_topic_listing_does_not_fetch_pages(self):
        with patch.object(hackathon.httpx, "get") as fetch:
            self.assertEqual(self.handbook.topics(), [{
                "topic": "schedule", "title": "Schedule 2026",
            }])
        fetch.assert_not_called()

    def test_bare_urls_and_url_link_labels_are_removed(self):
        html = (
            '<main><p>Doors open at 08:00.</p><p>https://example.com/details</p>'
            '<p>www.example.com</p><a href="https://ai-weeks.ch/2026/hack-zurich">'
            'ai-weeks.ch/2026/hack-zurich</a>'
            '<a href="https://example.com">https://example.com</a>'
            '<a href="/challenges">Challenge information</a></main>'
        )
        content = hackathon.extract_content(html, self.url)
        self.assertIn("Doors open at 08:00.", content)
        self.assertIn("Challenge information", content)
        self.assertNotIn("example.com", content)
        self.assertNotIn("ai-weeks.ch", content)

    def test_embedded_faq_answers_are_returned_without_executing_scripts(self):
        records = {
            "30": ["$", "section", None, {"children": [
                ["$", "h2", None, {"children": "FAQ"}], "$L40", "$L41", "$L42",
            ]}],
            "40": ["$", "h3", None, {"children": "What should participants bring?"}],
            "41": ["$", "p", None, {"children": [
                "Bring a laptop. ", ["$", "a", None, {
                    "href": "https://example.com/checklist", "children": "Checklist",
                }],
            ]}],
            "42": "$L42",
        }
        stream = "".join(f"{record_id}:{json.dumps(node)}\n" for record_id, node in records.items())
        midpoint = len(stream) // 2
        scripts = "".join(
            f"<script>self.__next_f.push({json.dumps([1, chunk])})</script>"
            for chunk in (stream[:midpoint], stream[midpoint:])
        )
        html = (
            '<main><section><h2>FAQ</h2><h3><button>Question</button></h3>'
            '<div hidden></div></section></main><script>throw new Error("Do not run")</script>'
            + scripts
        )
        content = hackathon.extract_content(html, self.url)
        self.assertIn("What should participants bring?", content)
        self.assertIn("Bring a laptop.", content)
        self.assertIn("Checklist", content)
        self.assertNotIn("example.com", content)
        self.assertNotIn("Do not run", content)
        self.assertNotIn("$L", content)

    def test_server_fetches_content_without_returning_urls(self):
        response = self.response(
            '<main><h1>Schedule</h1><p>Expert jury at 12:00.</p>'
            '<a href="/jury-process-and-criteria">Jury criteria</a></main>'
        )
        with patch.object(hackathon.httpx, "get", return_value=response) as fetch:
            result = self.handbook.read("schedule", force_refresh=True)
            search = self.handbook.search("expert jury")
        self.assertEqual(fetch.call_args.args[0], self.url)
        self.assertIn("Expert jury at 12:00.", result["content"])
        self.assertEqual(search["matches"][0]["topic"], "schedule")
        payload = json.dumps([self.handbook.topics(), result, search])
        self.assertNotIn("source_url", payload)
        self.assertNotIn("https://", payload)
        self.assertNotIn("](", payload)

    def test_retrieval_includes_provenance_and_caches_until_expiry(self):
        response = self.response("<main><h1>Schedule</h1><p>Doors open at 08:00.</p></main>")
        with (
            patch.object(hackathon.httpx, "get", return_value=response) as fetch,
            patch.object(hackathon.time, "monotonic", return_value=100) as clock,
        ):
            first = self.handbook.read("schedule")
            second = self.handbook.read("schedule")
            self.assertFalse(first["cached"])
            self.assertTrue(second["cached"])
            self.assertEqual(first["fetched_at"], second["fetched_at"])
            self.assertNotIn("source_url", first)
            self.assertIn("08:00", first["content"])
            fetch.assert_called_once()
            self.assertFalse(fetch.call_args.kwargs["follow_redirects"])
            self.assertIs(fetch.call_args.kwargs["verify"], self.handbook.ssl_context)
            self.assertTrue(self.handbook.ssl_context.check_hostname)
            clock.return_value = 401
            self.assertFalse(self.handbook.read("schedule")["cached"])
            self.assertEqual(fetch.call_count, 2)

    def test_force_refresh_bypasses_cache(self):
        response = self.response("<main>Updated schedule</main>")
        with patch.object(hackathon.httpx, "get", return_value=response) as fetch:
            self.handbook.read("schedule")
            self.handbook.read("schedule", force_refresh=True)
        self.assertEqual(fetch.call_count, 2)

    def test_unknown_topic_cannot_be_used_to_fetch_arbitrary_urls(self):
        with patch.object(hackathon.httpx, "get") as fetch:
            with self.assertRaisesRegex(ValueError, "Unknown topic"):
                self.handbook.read("http://localhost/secrets")
        fetch.assert_not_called()

    def test_configuration_rejects_non_official_or_insecure_urls(self):
        for url in ("http://zh.ai-weeks.ch/", "https://example.com/", "https://zh.ai-weeks.ch@localhost/"):
            self.config["pages"]["schedule"]["url"] = url
            with self.subTest(url=url), self.assertRaises(ValueError):
                hackathon.Handbook(self.config)

    def test_http_errors_and_redirects_are_not_cached(self):
        for status in (302, 404, 503):
            with self.subTest(status=status), patch.object(
                hackathon.httpx, "get", return_value=self.response("Error", status),
            ):
                with self.assertRaises(httpx.HTTPStatusError):
                    self.handbook.read("schedule")
                self.assertEqual(self.handbook.cache, {})

    def test_non_html_responses_are_rejected(self):
        with patch.object(hackathon.httpx, "get", return_value=self.response("{}", content_type="application/json")):
            with self.assertRaisesRegex(ValueError, "Expected an HTML"):
                self.handbook.read("schedule")

    def test_search_returns_matches_and_reports_partial_failures(self):
        self.config["pages"]["unavailable"] = {"title": "Unavailable", "url": "https://zh.ai-weeks.ch/unavailable"}
        page = {"topic": "schedule", "title": "Schedule 2026", "source_url": self.url,
                "fetched_at": "2026-09-24T08:00:00+00:00", "content": "Submission to the expert jury at 12:00."}

        def read(topic):
            if topic == "unavailable":
                raise httpx.ConnectError("Site unavailable")
            return page

        with patch.object(self.handbook, "read", side_effect=read):
            result = self.handbook.search("EXPERT jury", limit=1)
        self.assertEqual(result["matches"][0]["topic"], "schedule")
        self.assertEqual(result["total_matches"], 1)
        self.assertFalse(result["complete"])
        self.assertEqual(result["errors"][0]["topic"], "unavailable")
        self.assertNotIn("source_url", json.dumps(result))

    def test_http_errors_do_not_expose_request_or_help_urls(self):
        response = self.response("Unavailable", status=503)
        with patch.object(hackathon.httpx, "get", return_value=response):
            result = self.handbook.search("jury")
        error = result["errors"][0]["error"]
        self.assertIn("HTTP 503", error)
        self.assertNotIn("https://", json.dumps(result))

    def test_search_distinguishes_no_matches_from_invalid_queries(self):
        page = {"title": "Schedule", "content": "Lunch"}
        with patch.object(self.handbook, "read", return_value=page):
            result = self.handbook.search("unmatched")
        self.assertEqual(result["matches"], [])
        self.assertTrue(result["complete"])
        for query, limit in ((" ", 5), ("lunch", 0), ("lunch", 21)):
            with self.subTest(query=query, limit=limit), self.assertRaises(ValueError):
                self.handbook.search(query, limit)

    def test_register_exposes_all_three_tools(self):
        server = Mock()
        registered = {}

        def tool(**settings):
            def decorate(function):
                registered[settings["name"]] = function
                return function
            return decorate

        server.tool.side_effect = tool
        hackathon.register(server, self.config)
        self.assertEqual(set(registered), {"list_hackathon_topics", "get_hackathon_info", "search_hackathon_info"})
        self.assertEqual(registered["list_hackathon_topics"]()[0]["topic"], "schedule")
        with patch.object(hackathon.Handbook, "read", side_effect=httpx.ConnectError("Site unavailable")):
            with self.assertRaisesRegex(hackathon.ToolError, "network and TLS"):
                registered["get_hackathon_info"]("schedule")
        with self.assertRaisesRegex(hackathon.ToolError, "at least one search word"):
            registered["search_hackathon_info"](" ")
        with patch.object(hackathon.httpx, "get", return_value=self.response("Error", status=503)):
            with self.assertRaisesRegex(hackathon.ToolError, "HTTP 503") as raised:
                registered["get_hackathon_info"]("schedule")
        self.assertNotIn("https://", str(raised.exception))
        with self.assertRaises(hackathon.ToolError) as raised:
            registered["get_hackathon_info"]("https://example.com")
        self.assertNotIn("https://", str(raised.exception))

    def test_bundled_configuration_contains_the_handbook_topics(self):
        with (SERVER_DIR / "config" / "tools_config.yaml").open(encoding="utf-8") as config_file:
            config = yaml.safe_load(config_file)
        topics = {topic["topic"] for topic in hackathon.Handbook(config["hackathon"]).topics()}
        self.assertTrue({"overview", "schedule", "challenges", "judging", "location", "first_aid"} <= topics)
        self.assertEqual(len(topics), 21)
        self.assertEqual(config["hackathon"]["pages"]["faq"]["url"], config["hackathon"]["pages"]["event_details"]["url"])


if __name__ == "__main__":
    unittest.main()