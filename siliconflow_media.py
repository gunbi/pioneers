"""Model discovery and durable asynchronous video jobs on SiliconFlow."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from skill_sources import write_json


CATALOG_FILTERS = {
    "chat": "sub_type=chat", "image": "type=image", "video": "type=video", "text-to-video": "sub_type=text-to-video",
    "audio": "type=audio", "embedding": "sub_type=embedding", "rerank": "sub_type=reranker",
}
VIDEO_STATES = ("InQueue", "InProgress", "Succeed", "Failed")


def list_catalog(provider):
    """Fetch the full account catalog. Filtered views only supply capability labels."""
    def ids(path):
        body = provider.request(path, timeout=15)
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise ValueError("SiliconFlow returned an invalid model catalog")
        return {row["id"] for row in body["data"] if isinstance(row, dict)
                and isinstance(row.get("id"), str) and row["id"]}

    catalog = {model: [] for model in ids("/models")}
    with ThreadPoolExecutor(max_workers=3) as pool:
        subsets = {kind: pool.submit(ids, "/models?" + query) for kind, query in CATALOG_FILTERS.items()}
        for kind, subset in subsets.items():
            for model in subset.result():
                if model in catalog:
                    catalog[model].append(kind)
    return [{"id": model, "kinds": catalog[model] or ["other"]} for model in sorted(catalog)]


def video_payload(route, prompt):
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Video generation requires a nonempty scene description")
    duration = route.get("duration", 5)
    if type(duration) is not int or not 5 <= duration <= 15:
        raise ValueError("SiliconFlow video duration must be an integer from 5 to 15 seconds")
    resolution, ratio = route.get("resolution", "768p"), route.get("ratio", "16:9")
    if resolution not in ("480p", "768p") or ratio not in ("1:1", "3:4", "4:3", "9:16", "16:9", "21:9"):
        raise ValueError("Unsupported SiliconFlow video resolution or ratio")
    return {"model": route["model"], "prompt": prompt, "duration": duration, "resolution": resolution, "ratio": ratio}


def video_job(provider, route, prompt, receipt_path, result):
    """Share one receipt across attempts. A missing submit response needs operator reconciliation."""
    receipt_path = Path(receipt_path)
    if not receipt_path.is_absolute() or receipt_path.is_symlink():
        raise ValueError("Invalid local video receipt path")
    payload = video_payload(route, prompt)
    fingerprint = hashlib.sha256(json.dumps([route["provider"], payload], sort_keys=True).encode()).hexdigest()
    receipt = {"fingerprint": fingerprint, "state": "submitting"}
    try:
        # Reserve the job before the paid POST. Even simultaneous workers cannot both submit.
        fd = os.open(receipt_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
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
        if receipt["state"] == "Succeed" and time.time() - receipt.get("url_checked_at", 0) >= 3600:
            # A recovery may happen after the delivery link's lifetime; re-query the same request.
            receipt["state"] = "submitted"
    else:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(receipt, handle)
            handle.flush()
            os.fsync(handle.fileno())
        result["video"] = {"state": "submitting"}
        reply = provider.request("/video/submit", payload=payload, timeout=45)
        request_id = reply.get("requestId") if isinstance(reply, dict) else None
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-zA-Z0-9._-]{1,200}", request_id):
            raise ValueError("SiliconFlow 未返回有效的视频请求 ID，请人工核对提交结果。")
        receipt.update(state="submitted", request_id=request_id)
        write_json(receipt_path, receipt)

    request_id = receipt["request_id"]
    result["video"] = {"request_id": request_id, "state": receipt["state"]}
    while receipt["state"] not in ("Succeed", "Failed"):
        body = provider.request("/video/status", payload={"requestId": request_id}, timeout=30)
        state = body.get("status") if isinstance(body, dict) else None
        if state not in VIDEO_STATES:
            raise ValueError("SiliconFlow returned an invalid video job status")
        result["video"]["state"] = state
        if state in ("InQueue", "InProgress"):
            if state != receipt["state"]:
                receipt["state"] = state
                write_json(receipt_path, receipt)
            time.sleep(5)
            continue
        if state == "Succeed":
            result["usage"] = {"videos": 1, "video_seconds": payload["duration"]}
            output = body.get("results")
            videos = output.get("videos") if isinstance(output, dict) else None
            first = videos[0] if isinstance(videos, list) and videos else None
            url = first.get("url") if isinstance(first, dict) else None
            if not isinstance(url, str) or len(url) > 16384 or not url.isascii() or any(
                ch.isspace() or not ch.isprintable() or ch in '<>\\' for ch in url
            ):
                raise ValueError("SiliconFlow returned an invalid video result URL")
            address = urlsplit(url)
            if address.scheme != "https" or not address.hostname or address.username is not None:
                raise ValueError("SiliconFlow returned an invalid video result URL")
            receipt["url"] = url
            receipt["url_checked_at"] = time.time()
        receipt["state"] = state
        write_json(receipt_path, receipt)

    if receipt["state"] == "Failed":
        raise ValueError(f"SiliconFlow 视频生成失败，请核对请求 {request_id}。")
    result["usage"] = {"videos": 1, "video_seconds": payload["duration"]}
    result["content"] = (f"视频已生成（{payload['duration']} 秒）。\n\n"
                         f"请求 ID：{request_id}\n\n视频地址（供应商链接有效期约一小时）：\n{receipt['url']}")
