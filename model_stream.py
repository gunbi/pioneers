"""Bounded SSE decoding for OpenAI-compatible generation endpoints."""

from itertools import chain
import json
import socket
import time

from provider_errors import ProviderError, TransientProviderError


def response_lines(response, max_bytes, *, started, first, idle, total):
    # urllib exposes the live socket on HTTPResponse's buffered SocketIO.
    # A read1 consumes currently available bytes, including partial SSE lines.
    sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
    received = 0
    started_data = False
    pending = bytearray()
    while True:
        now = time.monotonic()
        remaining = started + total - now
        if remaining <= 0:
            raise TransientProviderError("Generation stream exceeded its total time limit")
        wait = min(remaining, idle if started_data else started + first - now)
        if wait <= 0:
            raise TransientProviderError("Generation timed out waiting for its first response data")
        if sock is not None and response.fp is not None:
            sock.settimeout(wait)
        try:
            chunk = response.read1(min(65536, max_bytes - received + 1))
        except (TimeoutError, socket.timeout):
            phase = "more stream data" if started_data else "first response data"
            raise TransientProviderError(f"Generation timed out waiting for {phase}") from None
        if not chunk:
            if pending:
                yield bytes(pending)
            return
        received += len(chunk)
        if received > max_bytes:
            raise ProviderError("Generation stream exceeds the response size limit")
        parts = chunk.split(b"\n")
        pending.extend(parts[0])
        if len(parts) > 1:
            for line in chain((bytes(pending),), parts[1:-1]):
                if not started_data and line.lstrip().startswith((b"data:", b"{", b"[")):
                    started_data = True
                yield line
            pending = bytearray(parts[-1])
        # A comment-only keepalive must not shorten the first-event budget.
        if not started_data and pending[:32].lstrip().startswith((b"data:", b"{", b"[")):
            started_data = True


def sse_events(lines):
    fields = []
    for raw_line in lines:
        line = raw_line.rstrip(b"\r")
        if line.startswith(b"data:"):
            fields.append(line[5:].lstrip(b" "))
        elif not line and fields:
            data = b"\n".join(fields)
            fields.clear()
            if data == b"[DONE]":
                return
            event = json.loads(data)
            if not isinstance(event, dict) or "error" in event:
                raise ProviderError("Provider generation failed in the response stream")
            yield event
    if fields == [b"[DONE]"]:
        return
    raise ProviderError("Provider stream ended before completion")


def read_completion(lines):
    first = next((line for line in lines if line.strip()), b"")
    lines = chain((first,), lines)
    if not first.startswith((b"data:", b":", b"event:", b"id:", b"retry:")):
        # Some endpoints answer a streaming request with a single JSON completion.
        return json.loads(b"\n".join(lines))

    parts = []
    usage = {}
    model = None
    finished = None
    for event in sse_events(lines):
        if event.get("model"):
            model = event["model"]
        if isinstance(event.get("usage"), dict):
            usage.update(event["usage"])
        choices = event.get("choices", [])
        if not isinstance(choices, list):
            raise ProviderError("Invalid choices in generation stream")
        for choice in choices:
            if not isinstance(choice, dict):
                raise ProviderError("Invalid choice in generation stream")
            if choice.get("index", 0) != 0:
                continue
            delta = choice.get("delta", {})
            if not isinstance(delta, dict):
                raise ProviderError("Invalid delta in generation stream")
            content = delta.get("content")
            if content is not None:
                if not isinstance(content, str):
                    raise ProviderError("Invalid text in generation stream")
                parts.append(content)
            if choice.get("finish_reason") is not None:
                if finished is not None and finished != choice["finish_reason"]:
                    raise ProviderError("Provider returned conflicting finish reasons")
                finished = choice["finish_reason"]
    return {"choices": [{"finish_reason": finished, "message": {"content": "".join(parts)}}],
            "model": model, "usage": usage}
