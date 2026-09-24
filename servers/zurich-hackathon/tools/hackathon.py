import json
from pathlib import Path
import re
import unicodedata

from mcp.server.mcpserver.exceptions import ToolError


def normalize_challenge(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return re.sub(r"[^a-z0-9]+", " ", normalized.encode("ascii", "ignore").decode()).strip()


class Handbook:
    def __init__(self, config: dict):
        data = json.loads(Path(__file__).with_name("handbook.json").read_text(encoding="utf-8"))
        self.snapshot_date = data["snapshot_date"]
        self.notice = data["notice"]
        self.pages = data["topics"]
        self.challenges = data["challenges"]
        for challenge_id, challenge in self.challenges.items():
            self.pages[f"challenge/{challenge_id}"] = challenge

    def metadata(self) -> dict:
        return {"offline": True, "snapshot_date": self.snapshot_date, "notice": self.notice}

    def topics(self) -> list[dict]:
        return [
            {"topic": topic, "title": page["title"]}
            for topic, page in self.pages.items()
        ]

    def read(self, topic: str, force_refresh: bool = False) -> dict:
        if topic not in self.pages:
            raise ValueError("Unknown topic. Use list_hackathon_topics to find valid topics.")
        page = self.pages[topic]
        return {
            **self.metadata(),
            "topic": topic,
            "title": page["title"],
            "content": "\n\n".join(page["content"]),
            "warnings": list(page.get("warnings", [])),
            "related_topics": list(page.get("related_topics", [])),
        }

    def list_challenges(self) -> dict:
        return {
            **self.metadata(),
            "challenges": [
                {"id": challenge_id, "title": challenge["title"],
                 "partner": challenge["partner"], "summary": challenge["summary"]}
                for challenge_id, challenge in self.challenges.items()
            ],
        }

    def challenge(self, query: str) -> dict:
        normalized = normalize_challenge(query)
        if not normalized:
            raise ValueError("Provide a challenge name, ID or partner. Use get_challenges to list them.")
        exact = []
        partial = []
        for challenge_id, challenge in self.challenges.items():
            names = [normalize_challenge(value) for value in (
                challenge_id, challenge["title"], challenge["partner"], *challenge["aliases"],
            )]
            if normalized in names:
                exact.append(challenge_id)
            elif any(normalized in name for name in names):
                partial.append(challenge_id)
        matches = exact or partial
        if not matches:
            raise ValueError("Unknown challenge. Use get_challenges to find valid names and IDs.")
        if len(matches) > 1:
            raise ValueError(f"Multiple challenges match: {', '.join(matches)}. Choose a specific ID.")
        challenge_id = matches[0]
        return {
            **self.read(f"challenge/{challenge_id}"),
            "id": challenge_id,
            "partner": self.challenges[challenge_id]["partner"],
            "summary": self.challenges[challenge_id]["summary"],
        }

    def search(self, query: str, limit: int = 5) -> dict:
        terms = query.casefold().split()
        if not terms:
            raise ValueError("Provide at least one search word")
        if not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        matches = []
        for topic in self.pages:
            page = self.read(topic)
            title = page["title"].casefold()
            content = page["content"].casefold()
            if not all(term in f"{title}\n{content}" for term in terms):
                continue
            position = max(0, content.find(terms[0]) - 120)
            matches.append({
                "topic": topic,
                "title": page["title"],
                "snapshot_date": self.snapshot_date,
                "snippet": page["content"][position:position + 600],
                "score": sum(content.count(term) + 5 * title.count(term) for term in terms),
            })
        matches.sort(key=lambda match: (-match["score"], match["topic"]))
        return {
            **self.metadata(),
            "query": query,
            "matches": matches[:limit],
            "total_matches": len(matches),
            "searched_topics": len(self.pages),
            "complete": True,
            "errors": [],
        }


def register(mcp, config: dict):
    handbook = Handbook(config)

    @mcp.tool(
        name="list_hackathon_topics",
        description="List all bundled Zurich Hackathon topics and challenge briefs. Everything is available offline.",
    )
    def list_hackathon_topics() -> list[dict]:
        return handbook.topics()

    @mcp.tool(
        name="get_hackathon_info",
        description=(
            "Read a bundled Zurich Hackathon topic, including its snapshot date and any source conflicts. "
            "Use list_hackathon_topics for IDs. force_refresh is retained for compatibility but is ignored: "
            "this server never fetches from the internet."
        ),
    )
    def get_hackathon_info(topic: str = "overview", force_refresh: bool = False) -> dict:
        try:
            return handbook.read(topic, force_refresh)
        except ValueError as error:
            raise ToolError(str(error)) from error

    @mcp.tool(
        name="search_hackathon_info",
        description=(
            "Search the offline Zurich Hackathon handbook and challenge briefs for all query words, "
            "ignoring case. Returns ranked excerpts; read matching topics for full context and warnings."
        ),
    )
    def search_hackathon_info(query: str, limit: int = 5) -> dict:
        try:
            return handbook.search(query, limit)
        except ValueError as error:
            raise ToolError(str(error)) from error

    @mcp.tool(
        name="get_challenges",
        description="List the seven Zurich Hackathon challenges with IDs, partners and short summaries, offline.",
    )
    def get_challenges() -> dict:
        return handbook.list_challenges()

    @mcp.tool(
        name="get_challenge",
        description=(
            "Get a full offline challenge brief: objectives, support, constraints and evaluation details. "
            "Accepts an ID, title or partner, e.g. 'swiss-grounding-mcp', 'Viseca', 'Apertus' or 'Swisscom (1)'. "
            "Ambiguous names return the matching IDs instead of guessing."
        ),
    )
    def get_challenge(challenge: str) -> dict:
        try:
            return handbook.challenge(challenge)
        except ValueError as error:
            raise ToolError(str(error)) from error

    def register_topic_tool(name: str, topic: str, description: str):
        @mcp.tool(name=name, description=description + " Uses the bundled offline snapshot, not live data.")
        def get_topic() -> dict:
            return handbook.read(topic)

    for name, (topic, description) in {
        "get_event_info": ("overview", "Get event dates, venue, opening time and WiFi access."),
        "get_schedule": ("schedule", "Get the full two-day schedule, badge pickup and submission deadlines."),
        "get_location": ("location", "Get the venue address, arrival directions, transit and parking information."),
        "get_faq": ("faq", "Get answers about participation, registration, accessibility, prizes and ownership."),
        "get_checklist": ("checklist", "Get what to bring and how to prepare for check-in."),
        "get_team_info": ("team_building", "Get team size, matching rules and how to find teammates."),
        "get_judging_criteria": ("judging", "Get judging criteria, pitch formats, jury rules and deadlines."),
        "get_submission_info": ("submission", "Get expert-jury submission timing, pitch length and rooms."),
        "get_contacts": ("communication", "Find organizers on site and the official communication channels."),
        "get_tech_goodies": ("tech_goodies", "Get the offered APIs, credits, models and technical resources."),
        "get_travel_reimbursement": ("travel_reimbursement", "Get travel reimbursement caps, rules and deadline."),
        "get_first_aid": ("first_aid", "Get emergency numbers and where to find first aid at Kraftwerk."),
    }.items():
        register_topic_tool(name, topic, description)