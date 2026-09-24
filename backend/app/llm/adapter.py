"""OpenAI-compatible streaming, with Apertus raw-marker and structured reasoning normalization."""

from collections import OrderedDict

from openai import AsyncOpenAI


class Normalizer:
    def __init__(self):
        self.buffer = ""
        self.inside = False
        self.structured = False
        self.calls = OrderedDict()
        self.finish_reason = None

    def feed(self, delta: dict, finish_reason=None) -> list[tuple[str, object]]:
        events = []
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if reasoning:
            self.structured = True
            events.append(("reasoning", reasoning))
        content = delta.get("content") or ""
        self.buffer += content
        opening, closing = "<|inner_prefix|>", "<|inner_suffix|>"
        while self.buffer:
            found = [(self.buffer.index(marker), marker) for marker in (opening, closing) if marker in self.buffer]
            if found:
                position, marker = min(found)
                if position and not (self.inside and self.structured):
                    events.append(("reasoning" if self.inside else "text", self.buffer[:position]))
                self.buffer = self.buffer[position + len(marker):]
                self.inside = marker == opening
            else:
                retained = max((length for marker in (opening, closing)
                                for length in range(1, min(len(marker), len(self.buffer) + 1))
                                if self.buffer.endswith(marker[:length])), default=0)
                if len(self.buffer) > retained and not (self.inside and self.structured):
                    events.append(("reasoning" if self.inside else "text", self.buffer[:-retained] if retained else self.buffer))
                self.buffer = self.buffer[-retained:] if retained else ""
                break
        for fragment in delta.get("tool_calls") or []:
            index = fragment["index"]
            call = self.calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
            function = fragment.get("function") or {}
            if fragment.get("id"):
                call["id"] += fragment["id"]
            if function.get("name"):
                call["name"] += function["name"]
            if function.get("arguments"):
                call["arguments"] += function["arguments"]
                events.append(("arguments", (index, function["arguments"])))
        self.finish_reason = finish_reason or self.finish_reason
        return events

    def finish(self) -> list[tuple[str, str]]:
        if not self.buffer:
            return []
        result = [("reasoning" if self.inside else "text", self.buffer)] if not self.buffer.startswith("<|") and not (self.inside and self.structured) else []
        self.buffer = ""
        return result


async def stream_completion(settings, messages: list, tools: list):
    client = AsyncOpenAI(api_key=settings.api_key, base_url=settings.llm_base_url,
                         default_headers={"User-Agent": settings.llm_user_agent}, timeout=settings.llm_timeout_s)
    try:
        stream = await client.chat.completions.create(model=settings.llm_model, messages=messages,
                                                       tools=tools or None, stream=True)
        async for chunk in stream:
            if chunk.choices:
                choice = chunk.choices[0]
                yield choice.delta.model_dump(exclude_none=True), choice.finish_reason
    finally:
        await client.close()