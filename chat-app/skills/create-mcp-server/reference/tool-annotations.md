# Tool annotations

Every tool must declare annotations. The host uses them to decide whether a call needs user approval, so they must be accurate.

```python
from mcp.types import ToolAnnotations

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

@mcp.tool(annotations=READ_ONLY)
def item_details(...): ...
```

## The hints

| Hint | Set `True` when | Set `False` when |
|---|---|---|
| `readOnlyHint` | The tool only reads. No writes anywhere: not locally, not upstream, no emails, no logs of user data. | The tool creates, updates, deletes, sends, books, or pays for anything. |
| `destructiveHint` | A write may delete, overwrite, cancel, or cause an irreversible or costly effect. | The tool is read-only, or only adds data that is easy to undo. |
| `idempotentHint` | Repeating the same call with the same arguments has no additional effect. | Each call has a new effect (e.g. creates a new record, sends a message). |
| `openWorldHint` | The tool talks to external systems (third-party APIs, the internet, other services). | The tool only uses local, bundled, or fully controlled data. |

Rules:

- **Always set both `readOnlyHint` and `destructiveHint` explicitly.** Never rely on defaults.
- A read-only tool sets `readOnlyHint=True, destructiveHint=False`.
- When unsure whether a write is destructive, set `destructiveHint=True`.
- A tool calling a public read-only API is `readOnlyHint=True, openWorldHint=True`.
- `title` is optional; the host does not need it.
- Do not mark a tool read-only to avoid approval prompts. The user who configures the server chooses the approval policy based on these hints.

## Approval policies on the host

The host operator picks one policy per server. The annotations decide the outcome under `ask_destructive`:

| Annotations on the tool | `auto` | `ask_always` | `ask_destructive` (default) |
|---|---|---|---|
| `readOnlyHint=True` | runs | asks | runs |
| `readOnlyHint=False, destructiveHint=False` | runs | asks | runs |
| `readOnlyHint=False, destructiveHint=True` | runs | asks | **asks** |
| `readOnlyHint=False`, `destructiveHint` unset | runs | asks | **asks** |
| **no `readOnlyHint` and no `destructiveHint`** | runs | asks | **runs without asking** |

The last row is why both hints are mandatory: a destructive tool without hints runs silently under the default policy.

When a user denies a call, the model receives "The user denied this tool call." Design write tools so a denial leaves nothing half-done.

## Common profiles

```python
READ_ONLY_LOCAL = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
READ_ONLY_REMOTE = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
ADDITIVE_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
DESTRUCTIVE_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)
```

| Example tool | Profile |
|---|---|
| Search a bundled catalog | `READ_ONLY_LOCAL` |
| Fetch live data from a public API | `READ_ONLY_REMOTE` |
| Add an item to a wishlist / create a draft | `ADDITIVE_WRITE` |
| Cancel a booking, delete a record, send a message, make a payment | `DESTRUCTIVE_WRITE` |
| Update a record in place by id (overwrites) | `DESTRUCTIVE_WRITE` with `idempotentHint=True` |

## Names, descriptions, and schemas

- Names: `snake_case`, verb first, matching `^[a-zA-Z0-9_-]{1,64}$`. The host drops tools with other names without warning.
- Docstring: one sentence saying what the tool does and what it returns. Mention the prerequisite tool if an id comes from another call. Put no behavioral rules for the model here; routing belongs in `instructions`.
- Parameters: typed and bounded with `pydantic.Field` so the schema carries `description`, `minLength`/`maxLength`, `minimum`/`maximum`, and enums (`Literal[...]`). The host validates arguments against this schema before calling; invalid arguments never reach the tool and the model only sees "Invalid tool arguments.", so descriptions must make valid input obvious.
- Prefer a few required parameters with sensible defaults over many optional ones.

```python
from typing import Annotated, Literal
from pydantic import Field

@mcp.tool(annotations=READ_ONLY_LOCAL)
def search_items(
    query: Annotated[str, Field(description="Part of the item name, e.g. 'river'", min_length=1, max_length=100)],
    category: Annotated[Literal["any", "books", "maps"], Field(description="Filter by category")] = "any",
    limit: Annotated[int, Field(description="Maximum results", ge=1, le=10)] = 5,
) -> dict:
    """Search items by name and return their ids and names."""
```
