"""Model discovery and durable asynchronous video jobs on SiliconFlow."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import re
import time

from model_policy import MODEL_ID
from model_timeouts import seconds


CATALOG_FILTERS = {
    "chat": "sub_type=chat", "image": "type=image", "video": "type=video", "text-to-video": "sub_type=text-to-video",
    "audio": "type=audio", "text-to-image": "sub_type=text-to-image", "text-to-speech": "sub_type=text-to-speech", "embedding": "sub_type=embedding", "rerank": "sub_type=reranker",
}

# SiliconFlow documents separate recommended canvases for Kolors and Qwen.
# https://api-docs.siliconflow.cn/docs/api/images-generations-post
_KOLORS_SIZES = {"1:1": "1024x1024", "3:4": "960x1280", "1:2": "720x1440", "9:16": "720x1280"}
_QWEN_SIZES = {"1:1": "1328x1328", "16:9": "1664x928", "9:16": "928x1664", "4:3": "1472x1140",
               "3:4": "1140x1472", "3:2": "1584x1056", "2:3": "1056x1584"}
_Z_IMAGE_SIZES = {**_KOLORS_SIZES, "4:3": "1280x960", "16:9": "1280x720", "2:1": "1440x720",
                  "2:3": "896x1344", "3:2": "1344x896"}
# Exact canvas presets from the hosted MOSS endpoint's validation response:
# https://github.com/CherryHQ/cherry-studio/issues/24046
_MOSS_MODEL = "OpenMOSS-Team/MOSS-Image"
_MOSS_SIZES = {"1:1": "1024x1024", "2:3": "832x1216", "3:2": "1216x832", "3:4": "896x1152",
               "4:3": "1152x896", "9:16": "768x1344", "16:9": "1344x768", "21:9": "1568x672"}


def valid_image_size(model, size):
    """Check a frozen size against the hosted model's enforced canvas restrictions."""
    return model.removeprefix("Pro/") != _MOSS_MODEL or size in _MOSS_SIZES.values()


def image_size(model, ratio):
    model = model.removeprefix("Pro/")
    if model == "Kwai-Kolors/Kolors":
        sizes = _KOLORS_SIZES
    elif model.startswith("Qwen/Qwen-Image") and supports_text_prompt(model, "text-to-image"):
        sizes = _QWEN_SIZES
    elif model == _MOSS_MODEL:
        sizes = _MOSS_SIZES
    elif model in ("Tongyi-MAI/Z-Image", "Tongyi-MAI/Z-Image-Turbo"):
        sizes = _Z_IMAGE_SIZES
    else:
        # The catalog identifies modalities but publishes no per-model image sizes.
        sizes = {"1:1": "1024x1024"}
    if ratio and ratio not in sizes:
        raise ValueError(f"SiliconFlow {model} 未配置画幅 {ratio}，可用画幅：{', '.join(sizes)}")
    return sizes[ratio or "1:1"]


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
    def rows(path):
        body = provider.request(path, timeout=15)
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise ValueError("SiliconFlow returned an invalid model catalog")
        return {row["id"]: row for row in body["data"] if isinstance(row, dict)
                and isinstance(row.get("id"), str) and MODEL_ID.fullmatch(row["id"])}

    source = rows("/models")
    catalog = {model: [] for model in source}
    with ThreadPoolExecutor(max_workers=3) as pool:
        subsets = {kind: pool.submit(copy_context().run, rows, "/models?" + query) for kind, query in CATALOG_FILTERS.items()}
        for kind, subset in subsets.items():
            for model in subset.result():
                if model in catalog and supports_text_prompt(model, kind):
                    catalog[model].append(kind)
    result = []
    for model in sorted(catalog):
        item = {"id": model, "kinds": catalog[model] or ["other"]}
        row = source[model]
        description = row.get("description")
        if isinstance(description, str) and description.strip():
            item["description"] = " ".join(description.split())[:400]
        if type(row.get("context_length")) is int and row["context_length"] > 0:
            item["context_length"] = row["context_length"]
        result.append(item)
    return result


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
        body = provider.request("/video/submit", payload=payload, timeout=seconds("PIONEERS_MODEL_FIRST_RESPONSE_SECONDS"))
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
