"""A durable, single-submission receipt shared across video task attempts."""

import hashlib
import json
import os
from pathlib import Path
import re
import socket
import time
from urllib.parse import urlsplit

from provider_errors import InsufficientCreditError, RateLimitedError, TransientProviderError
from skill_sources import write_json


VIDEO_STATES = ("InQueue", "InProgress", "Succeed", "Failed")


def valid_job_id(value):
    return isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_-][a-zA-Z0-9._-]{0,199}", value) is not None


def video_job(provider, route, prompt, receipt_path, result):
    """Share one receipt across attempts. A missing submit response needs operator reconciliation."""
    receipt_path = Path(receipt_path)
    if not receipt_path.is_absolute() or receipt_path.is_symlink():
        raise ValueError("Invalid local video receipt path")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Video generation requires a nonempty scene description")
    adapter = provider.video
    payload = adapter.payload(route, prompt)
    fingerprint = hashlib.sha256(json.dumps([route["provider"], payload], sort_keys=True).encode()).hexdigest()
    usage = {"videos": 1}
    if "duration" in payload:
        usage["video_seconds"] = payload["duration"]
    receipt = {"fingerprint": fingerprint, "state": "submitting"}
    fd = None
    if not route.get("reuse_only"):
        # Reserve before submitting. A recovering worker may only read an existing receipt.
        try:
            fd = os.open(receipt_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
    if fd is None:
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ValueError("视频回执无法读取，请人工核对原请求后恢复。") from None
        if not isinstance(receipt, dict) or receipt.get("fingerprint") != fingerprint:
            raise ValueError("视频回执与当前任务参数不一致，请核对原请求。")
        if not receipt.get("request_id"):
            result["video"] = {"state": "submitting"}
            raise ValueError("视频提交结果尚未确认，请人工核对原请求。")
        if receipt.get("state") not in ("submitted", *VIDEO_STATES):
            raise ValueError("视频回执状态无效，请核对原请求。")
        if receipt["state"] == "Succeed" and adapter.refresh_link(receipt):
            # A recovery may happen after the delivery link's lifetime; re-query the same request.
            receipt["state"] = "submitted"
    else:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(receipt, handle)
            handle.flush()
            os.fsync(handle.fileno())
        result["video"] = {"state": "submitting"}
        try:
            request_id = adapter.submit(provider, payload)
        except InsufficientCreditError:
            # The authenticated submission was rejected before a job existed.
            receipt_path.unlink()
            result.pop("video", None)
            raise
        if not valid_job_id(request_id):
            raise ValueError(f"{provider.name} 未返回有效的视频请求 ID，请人工核对提交结果。")
        receipt.update(state="submitted", request_id=request_id)
        write_json(receipt_path, receipt)

    request_id = receipt["request_id"]
    if not valid_job_id(request_id):
        raise ValueError("Invalid video receipt request ID")
    result["video"] = {"request_id": request_id, "state": receipt["state"]}
    poll_failures = 0
    while receipt["state"] not in ("Succeed", "Failed"):
        try:
            state, body = adapter.poll(provider, request_id)
        except (TransientProviderError, RateLimitedError, TimeoutError, socket.timeout):
            if poll_failures >= 3:
                raise
            time.sleep(5 * 2 ** poll_failures)
            poll_failures += 1
            continue
        poll_failures = 0
        if state not in VIDEO_STATES:
            raise ValueError(f"{provider.name} returned an invalid video job status")
        result["video"]["state"] = state
        if state in ("InQueue", "InProgress"):
            if state != receipt["state"]:
                receipt["state"] = state
                write_json(receipt_path, receipt)
            time.sleep(5)
            continue
        if state == "Succeed":
            result["usage"] = usage
            url, info = adapter.link(provider, request_id, body)
            if not isinstance(url, str) or len(url) > 16384 or not url.isascii() or any(
                ch.isspace() or not ch.isprintable() or ch in '<>\\' for ch in url
            ):
                raise ValueError(f"{provider.name} returned an invalid video result URL")
            address = urlsplit(url)
            if address.scheme != "https" or not address.hostname or address.username is not None:
                raise ValueError(f"{provider.name} returned an invalid video result URL")
            receipt.update(info)
            receipt["url"] = url
            receipt["url_checked_at"] = time.time()
        receipt["state"] = state
        write_json(receipt_path, receipt)

    if receipt["state"] == "Failed":
        raise ValueError(f"{provider.name} 视频生成失败，请核对请求 {request_id}。")
    result["usage"] = usage
    duration = f"（{payload['duration']} 秒）" if "duration" in payload else ""
    result["content"] = (f"视频已生成{duration}。\n\n"
                         f"请求 ID：{request_id}\n\n{adapter.link_label(receipt)}：\n{receipt['url']}")
