"""Record sanitized streaming samples from the configured OpenAI-compatible provider.

Usage: uv run --with httpx python scripts/provider_probe.py [case ...]
Cases: text, reasoning, tool, image. Writes probe-samples/<case>.jsonl and prints a summary.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import sys
import time
import zlib
from pathlib import Path

import httpx

try:
    import truststore  # uses the OS trust store (needed behind TLS-inspecting proxies)

    truststore.inject_into_ssl()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "probe-samples"
MARKERS = ("<|inner_prefix|>", "<|inner_suffix|>", "<think>", "</think>")


def load_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def tiny_png() -> str:
    width = height = 32
    raw = b"".join(b"\x00" + b"\xd5\x2b\x1e" * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return "data:image/png;base64," + base64.b64encode(png).decode()


TOOL = {
    "type": "function",
    "function": {
        "name": "get_departures",
        "description": "Get the next public transport departures for a Zurich stop.",
        "parameters": {
            "type": "object",
            "properties": {
                "stop": {"type": "string", "description": "Stop name, e.g. Zürich, Central"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["stop"],
        },
    },
}

CASES = {
    "text": {"messages": [{"role": "user", "content": "Reply with one short sentence: what is the capital of Switzerland?"}]},
    "reasoning": {"messages": [{"role": "user", "content": "A tram leaves every 7 minutes starting 08:03. What is the first departure after 09:00? Think briefly."}]},
    "reasoning_kwargs": {
        "messages": [{"role": "user", "content": "A tram leaves every 7 minutes starting 08:03. What is the first departure after 09:00? Think briefly."}],
        "chat_template_kwargs": {"enable_thinking": True},
    },
    "tool": {
        "messages": [{"role": "user", "content": "What are the next departures from Zürich, Central? Use the tool."}],
        "tools": [TOOL],
        "tool_choice": "auto",
    },
    "image": {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What single color fills this image? One word."},
                    {"type": "image_url", "image_url": {"url": tiny_png()}},
                ],
            }
        ]
    },
}


def run_case(client: httpx.Client, base_url: str, model: str, name: str) -> dict:
    body = {"model": model, "stream": True, **CASES[name]}
    summary: dict = {"case": name, "status": None, "delta_keys": set(), "markers_in_content": [], "finish_reasons": set()}
    content, reasoning, tool_frag = [], [], []
    started = time.monotonic()
    OUT.mkdir(exist_ok=True)
    with (OUT / f"{name}.jsonl").open("w", encoding="utf-8") as out:
        try:
            with client.stream("POST", f"{base_url}/chat/completions", json=body) as resp:
                summary["status"] = resp.status_code
                if resp.status_code != 200:
                    text = resp.read().decode(errors="replace")[:2000]
                    out.write(json.dumps({"error_status": resp.status_code, "body": text}) + "\n")
                    summary["error"] = text
                    return summary
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        if line.strip():
                            out.write(json.dumps({"non_data_line": line[:500]}) + "\n")
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        out.write(json.dumps({"done": True}) + "\n")
                        break
                    chunk = json.loads(data)
                    chunk.pop("id", None)
                    out.write(json.dumps(chunk, ensure_ascii=False) + "\n")
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        summary["delta_keys"].update(k for k, v in delta.items() if v not in (None, "", []))
                        if choice.get("finish_reason"):
                            summary["finish_reasons"].add(choice["finish_reason"])
                        if delta.get("content"):
                            content.append(delta["content"])
                        for key in ("reasoning_content", "reasoning"):
                            if delta.get(key):
                                reasoning.append(delta[key])
                        for tc in delta.get("tool_calls") or []:
                            tool_frag.append(tc)
        except httpx.HTTPError as exc:
            summary["error"] = f"{type(exc).__name__}: {exc}"
    full = "".join(content)
    summary["markers_in_content"] = [m for m in MARKERS if m in full]
    summary["content_preview"] = full[:400]
    summary["reasoning_preview"] = "".join(reasoning)[:400]
    summary["tool_call_fragments"] = len(tool_frag)
    summary["tool_call_first_fragments"] = tool_frag[:3]
    summary["seconds"] = round(time.monotonic() - started, 1)
    summary["delta_keys"] = sorted(summary["delta_keys"])
    summary["finish_reasons"] = sorted(summary["finish_reasons"])
    return summary


def main() -> int:
    load_env()
    key = os.environ.get("LLM_API_KEY") or os.environ.get("PUBLIC_AI_API_KEY")
    if not key:
        print("No LLM_API_KEY / PUBLIC_AI_API_KEY in environment or .env", file=sys.stderr)
        return 2
    base_url = os.environ.get("LLM_BASE_URL", "https://api.publicai.co/v1").rstrip("/")
    model = os.environ.get("LLM_MODEL", "swiss-ai/apertus-v1.5-70b-thinking")
    agent = os.environ.get("LLM_USER_AGENT", "SwissDiscover/0.1 (hackathon demo)")
    headers = {"Authorization": f"Bearer {key}", "User-Agent": agent, "Accept": "text/event-stream"}
    names = sys.argv[1:] or list(CASES)
    results = []
    with httpx.Client(headers=headers, timeout=httpx.Timeout(120, connect=15)) as client:
        for name in names:
            results.append(run_case(client, base_url, model, name))
    (OUT / "summary.json").write_text(json.dumps(results, indent=2, ensure_ascii=False, default=sorted), encoding="utf-8")
    print(json.dumps(results, indent=2, ensure_ascii=False, default=sorted))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
