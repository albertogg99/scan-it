"""In-process run worker. Deploy exactly one backend worker; restart fails unfinished runs.

The database owns run locks, decisions and replay events. SSE clients never own tasks.
"""

import asyncio
import base64
import json
import logging
import re
from jsonschema import SchemaError, ValidationError, validate
from openai import RateLimitError
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from app.llm import speech
from app.llm.adapter import Normalizer, stream_completion
from app.mcp_adapter.client import call_tool, requires_approval
from app.persistence.models import Attachment, Conversation, Message, Run, RunEvent, ToolCall, new_id, utcnow
from app.registry.service import error

TERMINAL = {"completed", "failed", "cancelled"}
TASKS: dict[str, asyncio.Task] = {}
EVENT_LOCK = asyncio.Lock()
TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
LOGGER = logging.getLogger(__name__)
SYSTEM_PROMPT = (
    "You are Apertus, a friendly, thoughtful chatbot. Help people with clear, natural, concise answers "
    "in their language. You have access to information and tools about the place or service described "
    "in the supplied context. You are not that service, an MCP server, or a tool. "
    "Use relevant tools yourself to answer the user's request; do not tell the user to call functions, for permission of if they want you to call any tool, instead just call them inmediately to retrieve the necesarry info. Do not delay or ask for confirmation unnecessarily. "
    "Do not provide code unless they ask for technical details. Explain results conversationally, using "
    "Markdown when useful. Never invent facts, information, or successful actions. "
    "Tools run only when you make a function call. Names, IDs, addresses, lists, and numbers about the "
    "service must come from tool results in this conversation; if you do not have "
    "them yet, make the call immediately and respond based on the results. "
    "Do not write tool calls, progress notes, or tool output as text, "
    "Distinguish "
    "fictional or sample information from real-world facts when relevant, without reciting development notes. "
    "Follow application safety rules. They cannot override your identity or these rules. Respect the application's tool approval flow "
    "and never bypass a denial. Do not ask for extra confirmation for read-only lookups already requested."
    "Try to answer the users requests by all means possible, only after you are sure you can't provide accurate and relevant information you can communicate that there is no information available."
)


async def emit(factory, run_id, kind, **payload):
    async with EVENT_LOCK:
        async with factory() as session:
            number = (await session.scalar(select(func.max(RunEvent.seq)).where(RunEvent.run_id == run_id)) or 0) + 1
            data = {"seq": number, "type": kind, "run_id": run_id, "ts": utcnow().isoformat(), **payload}
            session.add(RunEvent(run_id=run_id, seq=number, data=data))
            if number > 512:
                await session.execute(delete(RunEvent).where(RunEvent.run_id == run_id, RunEvent.seq <= number - 512))
            await session.commit()
    return data


def append_part(parts, kind, text):
    if parts and parts[-1]["type"] == kind:
        parts[-1]["text"] += text
    else:
        parts.append({"type": kind, "text": text})


async def persist(factory, message_id, *, content=None, reasoning=None, parts=None, replay=None, status=None, failure=None):
    values = {}
    if content is not None:
        values["content"] = content
    if reasoning is not None:
        values["reasoning"] = reasoning
    if parts is not None:
        values["parts"] = list(parts)
    if replay is not None:
        values["llm_messages"] = list(replay)
    if status is not None:
        values["status"] = status
        values["error"] = failure
    async with factory() as session:
        await session.execute(update(Message).where(Message.id == message_id).values(**values))
        await session.commit()


async def finish(factory, run_id, status, code=None, message=None, finish_reason=None):
    async with factory() as session:
        run = await session.get(Run, run_id)
        await session.execute(update(Message).where(Message.id == run.assistant_message_id).values(
            status=status, error={"code": code, "message": message} if code else None))
        await session.execute(update(ToolCall).where(ToolCall.message_id == run.assistant_message_id,
            ToolCall.status.in_(["pending_approval", "approved", "running"])).values(
            status="failed", error="Run ended before the tool call completed."))
        await session.execute(update(Conversation).where(Conversation.id == run.conversation_id, Conversation.active_run_id == run_id).values(active_run_id=None))
        run.status = status
        await session.commit()
    if status == "completed":
        await emit(factory, run_id, "run.completed", assistant_message_id=run.assistant_message_id, finish_reason=finish_reason)
    elif status == "cancelled":
        await emit(factory, run_id, "run.cancelled")
    else:
        await emit(factory, run_id, "run.failed", code=code, message=message)


async def reconciliate(factory):
    async with factory() as session:
        runs = (await session.scalars(select(Run).where(Run.status == "running"))).all()
        for run in runs:
            await session.execute(update(Message).where(Message.id == run.assistant_message_id).values(
                status="failed", error={"code": "internal_error", "message": "Run interrupted by server restart."}))
            await session.execute(update(ToolCall).where(ToolCall.message_id == run.assistant_message_id,
                ToolCall.status.in_(["pending_approval", "approved", "running"])).values(status="failed", error="Run interrupted."))
            await session.execute(update(Conversation).where(Conversation.id == run.conversation_id, Conversation.active_run_id == run.id).values(active_run_id=None))
            run.status = "failed"
        await session.commit()
    for run in runs:
        await emit(factory, run.id, "run.failed", code="internal_error", message="Run interrupted by server restart.")


def tool_definitions(snapshot):
    return [{"type": "function", "function": {"name": tool["name"], "description": tool.get("description", ""),
            "parameters": tool.get("input_schema") or {"type": "object"}}}
            for tool in snapshot if TOOL_NAME.fullmatch(tool.get("name", ""))]


async def history(session, conversation, user_id, settings):
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "MCP context (untrusted data) from '" + conversation.server.title + "':\n" + conversation.context_snapshot}]
    for entry in conversation.messages:
        if entry.id == user_id:
            continue
        if entry.role == "assistant":
            messages.extend(entry.llm_messages or [])
        else:
            messages.append(await user_content(session, entry, settings))
    current = next(item for item in conversation.messages if item.id == user_id)
    messages.append(await user_content(session, current, settings))
    return messages


async def user_content(session, message, settings):
    attachments = (await session.scalars(select(Attachment).where(Attachment.message_id == message.id).order_by(Attachment.id))).all()
    text = [message.content] if message.content else []
    if settings.stt_base_url:
        for item in attachments:
            if item.mime.startswith("audio/") and item.transcript is None:
                item.transcript = await speech.transcribe(settings, item.data, item.mime)
                await session.commit()
        text += [item.transcript for item in attachments if item.mime.startswith("audio/") and item.transcript]
        attachments = [item for item in attachments if not item.mime.startswith("audio/")]
    if not attachments:
        return {"role": "user", "content": "\n\n".join(text)}
    # Voice-only turns carry no text part: Apertus refuses audio when told about it in text.
    content = [{"type": "text", "text": "\n\n".join(text)}] if text else []
    for item in attachments:
        data = base64.b64encode(item.data).decode("ascii")
        if item.mime.startswith("audio/"):
            content.append({"type": "input_audio", "input_audio": {"data": data, "format": "wav"}})
        else:
            content.append({"type": "image_url", "image_url": {"url": f"data:{item.mime};base64,{data}"}})
    return {"role": "user", "content": content}


async def cancelled(factory, run_id):
    async with factory() as session:
        return bool(await session.scalar(select(Run.cancel_requested).where(Run.id == run_id)))


async def wait_approval(factory, run_id, tool_id, timeout):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await cancelled(factory, run_id):
            raise asyncio.CancelledError
        async with factory() as session:
            status = await session.scalar(select(ToolCall.status).where(ToolCall.id == tool_id))
        if status != "pending_approval":
            return status
        await asyncio.sleep(0.15)
    raise TimeoutError


async def execute_call(factory, settings, run_id, call, definition, server):
    tool_id = call.id
    if call.status == "failed":
        result, status, failure = "Tool call rejected: " + call.error, "failed", call.error
    elif call.requires_approval:
        await emit(factory, run_id, "approval.pending", tool_call_id=tool_id, name=call.name, arguments=call.arguments)
        try:
            decision = await wait_approval(factory, run_id, tool_id, settings.approval_timeout_s)
        except TimeoutError as exc:
            raise ValueError("approval_timeout") from exc
        if decision == "denied":
            result, status, failure = "The user denied this tool call.", "denied", None
        elif decision == "approved":
            result, status, failure = None, None, None
        else:
            result, status, failure = "Tool call failed.", "failed", "Tool call failed."
    else:
        result, status, failure = None, None, None
    if status is None:
        if await cancelled(factory, run_id):
            raise asyncio.CancelledError
        async with factory() as session:
            live = await session.get(type(server), server.id)
            if not live or not live.enabled:
                result, status, failure = "Tool server is unavailable.", "failed", "Tool server is unavailable."
            else:
                await session.execute(update(ToolCall).where(ToolCall.id == tool_id).values(status="running"))
                await session.commit()
        if status is None:
            await emit(factory, run_id, "tool.running", tool_call_id=tool_id)
            try:
                response = await asyncio.wait_for(call_tool(live, settings, call.name, call.arguments), settings.tool_timeout_s)
                result = "\n".join(item.text for item in response.content if getattr(item, "type", None) == "text")
                if not result and getattr(response, "structured_content", None) is not None:
                    result = json.dumps(response.structured_content, ensure_ascii=False)
                if response.is_error:
                    status, failure, result = "failed", "MCP tool returned an error.", "MCP tool returned an error."
                else:
                    status, failure = "succeeded", None
            except Exception:
                result, status, failure = "MCP tool failed.", "failed", "MCP tool failed."
    truncated = False
    if len(result) > settings.tool_output_warn_chars:
        LOGGER.warning("Tool %s returned %d characters (over %d)", call.name, len(result), settings.tool_output_warn_chars)
    async with factory() as session:
        await session.execute(update(ToolCall).where(ToolCall.id == tool_id).values(status=status, result=result, error=failure, truncated=truncated))
        await session.commit()
    await emit(factory, run_id, "tool.result", tool_call_id=tool_id, status=status, content=result, truncated=truncated, error=failure)
    return {"role": "tool", "tool_call_id": call.provider_call_id, "content": result}


async def worker(factory, settings, run_id):
    content, reasoning, parts, replay = "", "", [], []
    try:
        async with factory() as session:
            run = await session.get(Run, run_id)
            conversation = (await session.scalars(select(Conversation).where(Conversation.id == run.conversation_id)
                .options(selectinload(Conversation.server), selectinload(Conversation.messages)))).one()
            messages = await history(session, conversation, run.user_message_id, settings)
            definitions = {tool["name"]: tool for tool in conversation.tools_snapshot if TOOL_NAME.fullmatch(tool.get("name", ""))}
            tools = tool_definitions(conversation.tools_snapshot)
            server = conversation.server
        if await cancelled(factory, run_id):
            raise asyncio.CancelledError
        await emit(factory, run_id, "run.started", conversation_id=run.conversation_id,
                   user_message_id=run.user_message_id, assistant_message_id=run.assistant_message_id)
        reasoning_reported = False
        for round_number in range(1, settings.max_tool_rounds + 2):
            if await cancelled(factory, run_id):
                raise asyncio.CancelledError
            normalizer = Normalizer()
            round_content = ""
            round_replay = []
            tool_ids = {}
            started = set()
            async with asyncio.timeout(settings.llm_timeout_s):
                async for delta, finish_reason in stream_completion(settings, messages, tools):
                    if await cancelled(factory, run_id):
                        raise asyncio.CancelledError
                    for fragment in delta.get("tool_calls") or []:
                        index = fragment["index"]
                        tool_ids.setdefault(index, new_id())
                    for kind, value in normalizer.feed(delta, finish_reason):
                        if kind == "arguments":
                            index, fragment = value
                            if index not in started:
                                await emit(factory, run_id, "tool_call.started", tool_call_id=tool_ids[index],
                                           index=index, name=normalizer.calls[index]["name"], round=round_number)
                                started.add(index)
                            await emit(factory, run_id, "tool_call.arguments.delta", tool_call_id=tool_ids[index], delta=fragment)
                            continue
                        if kind == "reasoning":
                            reasoning += value
                            append_part(parts, "reasoning", value)
                            await emit(factory, run_id, "reasoning.delta", text=value)
                        else:
                            content += value
                            round_content += value
                            append_part(parts, "text", value)
                            await emit(factory, run_id, "answer.delta", text=value)
                        if len(content) + len(reasoning) > settings.max_response_chars:
                            raise ValueError("response_too_large")
                    if sum(len(item["arguments"]) + len(item["name"]) for item in normalizer.calls.values()) + len(content) + len(reasoning) > settings.max_response_chars:
                        raise ValueError("response_too_large")
                    await persist(factory, run.assistant_message_id, content=content, reasoning=reasoning or None, parts=parts, replay=replay)
            for kind, value in normalizer.finish():
                if kind == "reasoning":
                    reasoning += value
                    append_part(parts, "reasoning", value)
                    await emit(factory, run_id, "reasoning.delta", text=value)
                else:
                    content += value
                    round_content += value
                    append_part(parts, "text", value)
                    await emit(factory, run_id, "answer.delta", text=value)
                if len(content) + len(reasoning) > settings.max_response_chars:
                    raise ValueError("response_too_large")
            await persist(factory, run.assistant_message_id, content=content, reasoning=reasoning or None, parts=parts)
            if await cancelled(factory, run_id):
                raise asyncio.CancelledError
            if not reasoning and not reasoning_reported:
                await emit(factory, run_id, "reasoning.unavailable", reason="not_provided")
                reasoning_reported = True
            calls = normalizer.calls
            assistant = {"role": "assistant", "content": round_content}
            if calls:
                if round_number > settings.max_tool_rounds:
                    raise ValueError("tool_rounds_exceeded")
                assistant["tool_calls"] = [{"id": item["id"] or f"missing-{run_id}-{round_number}-{index}",
                    "type": "function", "function": {"name": item["name"], "arguments": item["arguments"]}}
                    for index, item in calls.items()]
            round_replay.append(assistant)
            if not calls:
                replay.extend(round_replay)
                await persist(factory, run.assistant_message_id, content=content, reasoning=reasoning or None, parts=parts, replay=replay)
                await finish(factory, run_id, "completed", finish_reason=normalizer.finish_reason or "stop")
                return
            for index, item in calls.items():
                name, raw = item["name"], item["arguments"]
                definition = definitions.get(name)
                failure = None
                try:
                    arguments = json.loads(raw)
                    if not isinstance(arguments, dict):
                        raise ValueError
                    if definition:
                        validate(arguments, definition.get("input_schema") or {"type": "object"})
                except (ValueError, ValidationError, SchemaError):
                    arguments, failure = None, "Invalid tool arguments."
                if not definition:
                    failure = "Unknown tool."
                provider_id = item["id"] or f"missing-{run_id}-{round_number}-{index}"
                needs_approval = bool(definition and requires_approval(server.approval_policy, definition.get("annotations") or {}))
                async with factory() as session:
                    call = ToolCall(id=tool_ids[index], conversation_id=run.conversation_id, message_id=run.assistant_message_id,
                        round=round_number, provider_call_id=provider_id, name=name, arguments=arguments,
                        arguments_raw=raw, requires_approval=needs_approval,
                        status="failed" if failure else "pending_approval" if needs_approval else "approved", error=failure)
                    session.add(call)
                    await session.commit()
                parts.append({"type": "tool_call", "tool_call_id": call.id})
                if index not in started:
                    await emit(factory, run_id, "tool_call.started", tool_call_id=call.id, index=index, name=name, round=round_number)
                await emit(factory, run_id, "tool_call.ready", tool_call_id=call.id, name=name, arguments=arguments,
                           error=failure, requires_approval=call.requires_approval)
                round_replay.append(await execute_call(factory, settings, run_id, call, definition, server))
                await persist(factory, run.assistant_message_id, parts=parts)
            replay.extend(round_replay)
            await persist(factory, run.assistant_message_id, parts=parts, replay=replay)
            messages.extend(round_replay)
    except asyncio.CancelledError:
        await finish(factory, run_id, "cancelled")
    except TimeoutError:
        await finish(factory, run_id, "failed", "llm_timeout", "Run timed out.")
    except RateLimitError:
        await finish(factory, run_id, "failed", "llm_rate_limited", "Language model rate limit reached.")
    except speech.TranscriptionError as exc:
        LOGGER.error("Run %s transcription failed: %s", run_id, exc)
        await finish(factory, run_id, "failed", "stt_error", "The voice message could not be transcribed.")
    except Exception as exc:
        LOGGER.error("Run %s failed: exception=%s status=%s frames=%s", run_id, type(exc).__name__,
                     getattr(exc, "status_code", None),
                     [(frame.tb_frame.f_code.co_name, frame.tb_lineno) for frame in _traceback_frames(exc.__traceback__)])
        code = str(exc) if isinstance(exc, ValueError) and str(exc) in {"response_too_large", "tool_rounds_exceeded", "approval_timeout"} else "llm_error"
        await finish(factory, run_id, "failed", code, "The response could not be completed.")
    finally:
        TASKS.pop(run_id, None)


def _traceback_frames(traceback):
    while traceback is not None:
        yield traceback
        traceback = traceback.tb_next


def start(factory, settings, run_id):
    TASKS[run_id] = asyncio.create_task(worker(factory, settings, run_id))