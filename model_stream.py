"""Bounded SSE decoding for OpenAI-compatible generation endpoints."""

from itertools import chain
import json
import os
import socket
import time

from provider_errors import ProviderError, TransientProviderError


SIZE_DEFAULTS = {
    "PIONEERS_MODEL_STREAM_TRANSPORT_BYTES": 128 * 1024 * 1024,
    "PIONEERS_MODEL_STREAM_EVENT_BYTES": 1024 * 1024,
    "PIONEERS_MODEL_STREAM_CONTENT_BYTES": 8 * 1024 * 1024,
}


def byte_limit(name):
    try:
        value = int(os.environ.get(name, SIZE_DEFAULTS[name]))
        if value > 0:
            return value
    except ValueError:
        pass
    raise ValueError(f"{name} must be a positive integer in bytes")


def check_size(kind, observed, limit):
    if observed > limit:
        raise ProviderError(f"Generation stream {kind} size limit exceeded ({observed} bytes, limit {limit} bytes)")


def response_lines(response, max_bytes, *, started, first, idle, total, event_bytes=None):
    # urllib exposes the live socket on HTTPResponse's buffered SocketIO.
    # A read1 consumes currently available bytes, including partial SSE lines.
    sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
    received = 0
    started_data = False
    pending = bytearray()
    event_size = 0
    is_json = None
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
        check_size("transport", received, max_bytes)
        parts = chunk.split(b"\n")
        for index, part in enumerate(parts):
            terminated = index < len(parts) - 1
            pending.extend(part)
            if event_bytes is not None:
                if is_json is None and (prefix := part.lstrip()):
                    is_json = prefix.startswith((b"{", b"["))
                if is_json is not True and pending and pending != b"\r":
                    # Until a JSON envelope is recognized, charge every field
                    # and line ending, including unclassified whitespace.
                    size = event_size + len(pending) + int(terminated)
                    check_size("event", size, event_bytes)
                    if terminated:
                        event_size = size
                elif terminated and (not pending or pending == b"\r"):
                    event_size = 0
            # A comment-only keepalive must not shorten the first-event budget.
            if not started_data and pending[:32].lstrip().startswith((b"data:", b"{", b"[")):
                started_data = True
            if terminated:
                yield bytes(pending)
                pending.clear()


def sse_events(lines):
    data = bytearray()
    has_data = False
    for raw_line in lines:
        line = raw_line.rstrip(b"\r")
        if line.startswith(b"data:"):
            if has_data:
                data.append(10)  # SSE joins data fields with a newline.
            data.extend(line[5:].lstrip(b" "))
            has_data = True
        elif not line and has_data:
            if data == b"[DONE]":
                return
            event = json.loads(data)
            data.clear()
            has_data = False
            if not isinstance(event, dict) or "error" in event:
                raise ProviderError("Provider generation failed in the response stream")
            yield event
    if has_data and data == b"[DONE]":
        return
    raise ProviderError("Provider stream ended before completion")


def token_counts(body):
    counts = body.get("usage")
    if not isinstance(counts, dict):
        return {}
    return {key: counts[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            if type(counts.get(key)) is int and counts[key] >= 0}


def read_completion(lines, *, content_bytes=None):
    if content_bytes is None:
        content_bytes = byte_limit("PIONEERS_MODEL_STREAM_CONTENT_BYTES")
    lines = iter(lines)
    first = next((line for line in lines if line.strip()), b"")
    if not first.startswith((b"data:", b":", b"event:", b"id:", b"retry:")):
        # Some endpoints ignore stream=true. The envelope is transport-bounded,
        # and the visible message uses the same UTF-8 budget as streamed deltas.
        document = bytearray(first)
        for line in lines:
            document.append(10)
            document.extend(line)
        body = json.loads(document)
        choices = body.get("choices") if isinstance(body, dict) else None
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                check_size("content", len(message["content"].encode("utf-8")), content_bytes)
        return body

    visible = bytearray()
    usage = {}
    model = None
    finished = None
    for event in sse_events(chain((first,), lines)):
        if event.get("model"):
            model = event["model"]
        usage.update(token_counts(event))
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
                if content:
                    encoded = content.encode("utf-8")
                    check_size("content", len(visible) + len(encoded), content_bytes)
                    visible.extend(encoded)
            if choice.get("finish_reason") is not None:
                if finished is not None and finished != choice["finish_reason"]:
                    raise ProviderError("Provider returned conflicting finish reasons")
                finished = choice["finish_reason"]
    return {"choices": [{"finish_reason": finished, "message": {"content": visible.decode("utf-8")}}],
            "model": model, "usage": usage}
