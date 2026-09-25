from slugify import slugify


def register(mcp, config: dict):
    separator = config.get("separator", "-")

    @mcp.tool(
        name="slugify_text",
        description="Convert text to a URL-friendly slug.",
    )
    def slugify_text(text: str) -> str:
        return slugify(text, separator=separator)