import importlib.util
import inspect
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import yaml


SERVER_DIR = Path(__file__).resolve().parents[1] / "servers" / "zurich-hackathon"
spec = importlib.util.spec_from_file_location("hackathon", SERVER_DIR / "tools" / "hackathon.py")
hackathon = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hackathon)


class HackathonTests(unittest.TestCase):
    def setUp(self):
        self.config = {}
        self.handbook = hackathon.Handbook(self.config)

    def registered_tools(self):
        server = Mock()
        registered = {}

        def tool(**settings):
            def decorate(function):
                registered[settings["name"]] = function
                return function
            return decorate

        server.tool.side_effect = tool
        hackathon.register(server, self.config)
        return registered

    def test_read_works_without_network(self):
        with patch("socket.socket.connect", side_effect=AssertionError("Network access is disabled")):
            handbook = hackathon.Handbook(self.config)
            result = handbook.read("schedule", force_refresh=True)
        self.assertIn("Schedule", result["content"])
        self.assertTrue(result["offline"])
        self.assertEqual(result["snapshot_date"], "2026-09-24")
        self.assertNotIn("fetched_at", result)

    def test_startup_has_no_tool_dependencies_to_install(self):
        from main import _install_tool_requirements

        with TemporaryDirectory() as directory, patch("subprocess.run") as install:
            _install_tool_requirements(SERVER_DIR / "tools", ["hackathon"], Path(directory))
        install.assert_not_called()

    def test_all_topics_are_readable_with_network_disabled(self):
        with patch("socket.socket.connect", side_effect=AssertionError("Network access is disabled")):
            for topic in self.handbook.topics():
                with self.subTest(topic=topic["topic"]):
                    result = self.handbook.read(topic["topic"])
                    self.assertEqual(result["title"], topic["title"])
                    self.assertTrue(result["content"].strip())
                    self.assertTrue(result["offline"])
                    self.assertIn("Not live", result["notice"])
                    for related in result["related_topics"]:
                        self.handbook.read(related)

    def test_force_refresh_is_a_compatible_no_op(self):
        with patch("socket.socket.connect", side_effect=AssertionError("Network access is disabled")):
            first = self.handbook.read("schedule")
            refreshed = self.handbook.read("schedule", force_refresh=True)
        self.assertEqual(first, refreshed)
        self.assertNotIn("cached", first)

    def test_unknown_topic_is_rejected_without_network(self):
        with patch("socket.socket.connect", side_effect=AssertionError("Network access is disabled")):
            for topic in ("unknown", "http://localhost/secrets", "../handbook.json"):
                with self.subTest(topic=topic), self.assertRaisesRegex(ValueError, "Unknown topic"):
                    self.handbook.read(topic)

    def test_responses_include_content_without_website_links(self):
        results = [self.handbook.read(topic["topic"]) for topic in self.handbook.topics()]
        results.extend([
            self.handbook.topics(), self.handbook.list_challenges(),
            self.handbook.challenge("mcp"), self.handbook.search("expert jury"),
        ])
        payload = json.dumps(results)
        for unwanted in ("source_url", "https://", "http://", "]("):
            self.assertNotIn(unwanted, payload)

    def test_published_schedule_and_faq_answers_are_bundled(self):
        schedule = self.handbook.read("schedule")["content"]
        for expected in ("September 24, 2026", "September 25, 2026", "12:00", "17:30", "22:00"):
            self.assertIn(expected, schedule)
        faq = self.handbook.read("faq")["content"]
        for expected in ("English", "wheelchair", "16 or over", "intellectual property"):
            self.assertIn(expected, faq)

    def test_conflicts_and_image_only_content_are_flagged(self):
        schedule_warnings = " ".join(self.handbook.read("schedule")["warnings"])
        self.assertIn("21:00", schedule_warnings)
        self.assertIn("22:00", schedule_warnings)
        contact_warnings = " ".join(self.handbook.read("communication")["warnings"])
        self.assertIn("K3", contact_warnings)
        self.assertIn("K4", contact_warnings)
        self.assertIn("not included", " ".join(self.handbook.read("venue_map")["warnings"]))

    def test_challenge_listing_contains_seven_unique_briefs(self):
        result = self.handbook.list_challenges()
        challenges = result["challenges"]
        self.assertTrue(result["offline"])
        self.assertEqual(len(challenges), 7)
        self.assertEqual(len({challenge["id"] for challenge in challenges}), 7)
        for challenge in challenges:
            with self.subTest(challenge=challenge["id"]):
                detail = self.handbook.challenge(challenge["id"])
                self.assertEqual(detail["title"], challenge["title"])
                self.assertEqual(detail["partner"], challenge["partner"])
                self.assertEqual(detail["summary"], challenge["summary"])
                self.assertIn("## Objective", detail["content"])
                self.assertEqual(self.handbook.read(detail["topic"])["content"], detail["content"])

    def test_challenge_lookup_accepts_names_partners_and_aliases(self):
        examples = {
            "  SWISS GROUNDING MCP  ": "swiss-grounding-mcp",
            "Swisscom (1)": "swiss-grounding-mcp",
            "mcp": "swiss-grounding-mcp",
            "Swisscom 2": "fighting-identity-fraud-in-the-age-of-ai",
            "fraud": "fighting-identity-fraud-in-the-age-of-ai",
            "Viseca": "agent-on-a-leash",
            "Swiss Life": "ai-support-agent-for-operational-service-desks",
            "swisslife": "ai-support-agent-for-operational-service-desks",
            "Apertus": "build-a-public-ai-service",
            "VZ Verm\u00f6gensZentrum": "digital-legacy-ai",
            "VZ VermogensZentrum": "digital-legacy-ai",
            "UBS": "transaction-activity-forecasting",
        }
        for query, expected in examples.items():
            with self.subTest(query=query):
                self.assertEqual(self.handbook.challenge(query)["id"], expected)

    def test_ambiguous_challenge_returns_choices_instead_of_guessing(self):
        with self.assertRaisesRegex(ValueError, "Multiple challenges match") as raised:
            self.handbook.challenge("Swisscom")
        self.assertIn("swiss-grounding-mcp", str(raised.exception))
        self.assertIn("fighting-identity-fraud-in-the-age-of-ai", str(raised.exception))

    def test_empty_and_unknown_challenges_have_actionable_errors(self):
        for query in ("", "  ", "???", "not-a-challenge", "https://example.com"):
            with self.subTest(query=query), self.assertRaisesRegex(ValueError, "get_challenges"):
                self.handbook.challenge(query)

    def test_search_includes_challenge_briefs_and_uses_all_words(self):
        result = self.handbook.search("MCP jurisdiction")
        self.assertEqual(result["total_matches"], 1)
        self.assertEqual(result["matches"][0]["topic"], "challenge/swiss-grounding-mcp")
        self.assertEqual(result["searched_topics"], 28)
        self.assertTrue(result["complete"])
        self.assertEqual(result["errors"], [])
        self.assertTrue(result["offline"])
        for match in result["matches"]:
            self.assertTrue(self.handbook.read(match["topic"])["content"])

    def test_search_limits_and_ranks_matches_deterministically(self):
        result = self.handbook.search("pitch", limit=2)
        self.assertEqual(len(result["matches"]), 2)
        self.assertGreater(result["total_matches"], 2)
        self.assertEqual(result, self.handbook.search("PITCH", limit=2) | {"query": "pitch"})
        self.assertEqual(result["matches"], sorted(
            result["matches"], key=lambda match: (-match["score"], match["topic"]),
        ))

    def test_search_distinguishes_no_matches_from_invalid_queries(self):
        result = self.handbook.search("nonexistentword")
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["total_matches"], 0)
        self.assertTrue(result["complete"])
        for query, limit in ((" ", 5), ("lunch", 0), ("lunch", 21)):
            with self.subTest(query=query, limit=limit), self.assertRaises(ValueError):
                self.handbook.search(query, limit)

    def test_returned_lists_cannot_mutate_the_snapshot(self):
        self.handbook.read("schedule")["warnings"].clear()
        self.assertTrue(self.handbook.read("schedule")["warnings"])
        self.handbook.read("event_details")["related_topics"].clear()
        self.assertEqual(self.handbook.read("event_details")["related_topics"], ["faq"])
        self.handbook.list_challenges()["challenges"][0]["title"] = "Changed"
        self.assertEqual(self.handbook.challenge("Viseca")["title"], "Agent on a Leash")

    def test_simple_tools_have_no_arguments_and_read_the_right_topic(self):
        registered = self.registered_tools()
        topic_tools = {
            "get_event_info": "overview", "get_schedule": "schedule", "get_location": "location",
            "get_faq": "faq", "get_checklist": "checklist", "get_team_info": "team_building",
            "get_judging_criteria": "judging", "get_submission_info": "submission",
            "get_contacts": "communication", "get_tech_goodies": "tech_goodies",
            "get_travel_reimbursement": "travel_reimbursement", "get_first_aid": "first_aid",
        }
        self.assertEqual(set(registered), set(topic_tools) | {
            "list_hackathon_topics", "get_hackathon_info", "search_hackathon_info",
            "get_challenges", "get_challenge",
        })
        for name, topic in topic_tools.items():
            with self.subTest(tool=name):
                self.assertEqual(len(inspect.signature(registered[name]).parameters), 0)
                self.assertEqual(registered[name](), self.handbook.read(topic))

    def test_every_registered_tool_works_without_network(self):
        with patch("socket.socket.connect", side_effect=AssertionError("Network access is disabled")):
            registered = self.registered_tools()
            arguments = {
                "get_hackathon_info": {"topic": "schedule", "force_refresh": True},
                "search_hackathon_info": {"query": "expert jury"},
                "get_challenge": {"challenge": "mcp"},
            }
            for name, function in registered.items():
                with self.subTest(tool=name):
                    result = function(**arguments.get(name, {}))
                    self.assertTrue(result)
                    if isinstance(result, dict):
                        self.assertTrue(result["offline"])

    def test_registered_tools_translate_invalid_input_to_tool_errors(self):
        registered = self.registered_tools()
        examples = (
            ("get_hackathon_info", {"topic": "https://example.com"}, "Unknown topic"),
            ("get_challenge", {"challenge": "unknown"}, "get_challenges"),
            ("get_challenge", {"challenge": "Swisscom"}, "Multiple challenges"),
            ("search_hackathon_info", {"query": " "}, "at least one search word"),
            ("search_hackathon_info", {"query": "jury", "limit": 0}, "limit"),
        )
        for name, arguments, expected in examples:
            with self.subTest(tool=name), self.assertRaisesRegex(hackathon.ToolError, expected) as raised:
                registered[name](**arguments)
            self.assertNotIn("https://", str(raised.exception))

    def test_bundled_configuration_contains_the_handbook_topics(self):
        with (SERVER_DIR / "config" / "tools_config.yaml").open(encoding="utf-8") as config_file:
            config = yaml.safe_load(config_file)
        topics = {topic["topic"] for topic in hackathon.Handbook(config["hackathon"]).topics()}
        self.assertEqual(topics - {topic for topic in topics if topic.startswith("challenge/")}, {
            "overview", "faq", "event_details", "code_of_conduct", "first_aid", "location",
            "venue_map", "challenges", "qa_sessions", "tech_goodies", "team_building",
            "checklist", "schedule", "relax_recharge", "communication", "organizers",
            "judging", "experts", "submission", "travel_reimbursement", "photos_and_video",
        })
        self.assertEqual(len(topics), 28)


if __name__ == "__main__":
    unittest.main()