"""Model discovery and durable asynchronous video jobs on SiliconFlow."""

from concurrent.futures import ThreadPoolExecutor
import re
import time


CATALOG_FILTERS = {
    "chat": "sub_type=chat", "image": "type=image", "video": "type=video", "text-to-video": "sub_type=text-to-video",
    "audio": "type=audio", "text-to-image": "sub_type=text-to-image", "text-to-speech": "sub_type=text-to-speech", "embedding": "sub_type=embedding", "rerank": "sub_type=reranker",
}


def supports_text_prompt(model, kind):
    # The image subtype also includes edit-only Qwen checkpoints that require an image.
    return kind != "text-to-image" or not re.search(r"(?:^|[-_/])(edit(?:ing)?|inpaint|controlnet)(?:$|[-_/])",
                                                    model, re.IGNORECASE)


def check_media_available(provider, model, mode, options):
    kind = {"image": "text-to-image", "speech": "text-to-speech"}.get(mode)
    if kind and supports_text_prompt(model, kind):
        body = provider.request("/models?" + CATALOG_FILTERS[kind], timeout=15)
        if isinstance(body, dict) and isinstance(body.get("data"), list):
            if any(isinstance(row, dict) and row.get("id") == model for row in body["data"]):
                return
    raise ValueError(f"Model {model} is unavailable for SiliconFlow {mode} tasks")


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
                if model in catalog and supports_text_prompt(model, kind):
                    catalog[model].append(kind)
    return [{"id": model, "kinds": catalog[model] or ["other"]} for model in sorted(catalog)]


class Video:
    @staticmethod
    def settings(spec):
        duration = spec.get("duration", 5)
        if type(duration) is not int or not 5 <= duration <= 15:
            raise ValueError("SiliconFlow video duration must be an integer from 5 to 15 seconds")
        resolution, ratio = spec.get("resolution", "768p"), spec.get("ratio", "16:9")
        if resolution not in ("480p", "768p") or ratio not in ("1:1", "3:4", "4:3", "9:16", "16:9", "21:9"):
            raise ValueError("Unsupported SiliconFlow video resolution or ratio")
        return {"duration": duration, "resolution": resolution, "ratio": ratio}

    @classmethod
    def payload(cls, route, prompt):
        return {"model": route["model"], "prompt": prompt, **cls.settings(route)}

    @staticmethod
    def submit(provider, payload):
        body = provider.request("/video/submit", payload=payload, timeout=45)
        return body.get("requestId") if isinstance(body, dict) else None

    @staticmethod
    def poll(provider, job):
        body = provider.request("/video/status", payload={"requestId": job}, timeout=30)
        return (body.get("status"), body) if isinstance(body, dict) else (None, {})

    @staticmethod
    def link(provider, job, body):
        output = body.get("results")
        videos = output.get("videos") if isinstance(output, dict) else None
        first = videos[0] if isinstance(videos, list) and videos else None
        return (first.get("url") if isinstance(first, dict) else None), {}

    @staticmethod
    def link_label(receipt):
        return "视频地址（供应商链接有效期约一小时）"

    @staticmethod
    def refresh_link(receipt):
        return time.time() - receipt.get("url_checked_at", 0) >= 3600
