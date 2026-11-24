"""OpenRouter model discovery and its asynchronous text-to-video protocol."""

from concurrent.futures import ThreadPoolExecutor
import re

from model_policy import MODEL_ID


def rows(provider, path, *, public):
    body = provider.request(path, timeout=20, public=public)
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise ValueError("OpenRouter returned an invalid model catalog")
    return {row["id"]: row for row in body["data"] if isinstance(row, dict)
            and isinstance(row.get("id"), str) and MODEL_ID.fullmatch(row["id"])}


def video_from_text(model, video):
    if not video or video.get("upscale_factor") is not None:
        return False
    # Video endpoints include editing, lip-sync and upscaling. The API's list of
    # input modalities alone cannot tell whether a reference image is required.
    description = str(model.get("description", "")) + " " + str(video.get("description", ""))
    return model["id"] == "minimax/hailuo-3" or bool(re.search(
        r"text[- ]to[- ]video|from (?:a |an )?(?:short |simple )?text (?:or image )?prompts?"
        r"|from text alone|text-only (?:prompts?|generation)|from (?:a )?text description",
        description, re.IGNORECASE))


def kinds(row, video):
    arch = row.get("architecture")
    if not isinstance(arch, dict):
        arch = {}
    ins = arch.get("input_modalities")
    outs = arch.get("output_modalities")
    ins = ins if isinstance(ins, list) else []
    outs = outs if isinstance(outs, list) else []
    endpoints = row.get("endpoints")
    types = []
    if ("text" in ins and "text" in outs and
            (endpoints is None or "/api/v1/chat/completions" in endpoints)):
        types.append("chat")
    elif "text" in outs:
        types.append("text")
    for output, kind in (("image", "image"), ("audio", "audio"), ("speech", "audio"),
                         ("transcription", "transcription"), ("embeddings", "embedding"),
                         ("rerank", "rerank"), ("decisions", "decisions")):
        if output in outs and kind not in types:
            types.append(kind)
    if "video" in outs or video:
        types.append("video")
        if "text" in ins and video_from_text(row, video):
            types.append("text-to-video")
    return types or ["other"]


def catalog(provider):
    with ThreadPoolExecutor(max_workers=2) as pool:
        all_models = pool.submit(rows, provider, "/models?output_modalities=all", public=True)
        videos = rows(provider, "/videos/models", public=True)
        models = all_models.result()
    result = []
    for model in sorted(models.keys() | videos.keys()):
        row = models.get(model, {"id": model})
        result.append({"id": model, "kinds": kinds(row, videos.get(model))})
    return result


def check_available(provider, model, mode, options):
    if mode == "video":
        videos = rows(provider, "/videos/models", public=True)
        general = rows(provider, "/models?output_modalities=video", public=True)
        if model in videos and "text-to-video" in kinds(general.get(model, {"id": model}), videos[model]):
            for field, supported in (("duration", "supported_durations"), ("resolution", "supported_resolutions"),
                                     ("ratio", "supported_aspect_ratios")):
                choices = videos[model].get(supported)
                if field in options and choices and options[field] not in choices:
                    raise ValueError(f"OpenRouter {model} does not support {field}={options[field]}; allowed: {choices}")
            return
    else:
        # This authenticated list applies the account's provider and privacy preferences.
        allowed = rows(provider, "/models/user", public=False)
        if model in allowed and "chat" in kinds(allowed[model], None):
            return
    raise ValueError(f"Model {model} is unavailable for OpenRouter {mode} tasks")


class Video:
    @staticmethod
    def settings(spec):
        # Omit unspecified options so each model can use its own supported defaults.
        values = {k: spec[k] for k in ("duration", "resolution", "ratio") if k in spec}
        if "duration" in values and (type(values["duration"]) is not int or values["duration"] <= 0):
            raise ValueError("OpenRouter video duration must be a positive integer")
        for key in ("resolution", "ratio"):
            if key in values and (not isinstance(values[key], str) or not re.fullmatch(r"[\w:.-]{1,24}", values[key])):
                raise ValueError(f"Invalid OpenRouter video {key}")
        return values

    @classmethod
    def payload(cls, route, prompt):
        options = cls.settings(route)
        if "ratio" in options:
            options["aspect_ratio"] = options.pop("ratio")
        return {"model": route["model"], "prompt": prompt, **options}

    @staticmethod
    def submit(provider, payload):
        body = provider.request("/videos", payload=payload, timeout=45)
        return body.get("id") if isinstance(body, dict) else None

    @staticmethod
    def poll(provider, job):
        # Construct the URL locally; upstream polling links never receive a credential.
        body = provider.request("/videos/" + job, timeout=30)
        if not isinstance(body, dict) or body.get("id", job) != job:
            raise ValueError("Invalid OpenRouter video receipt")
        states = {"pending": "InQueue", "in_progress": "InProgress", "completed": "Succeed", "failed": "Failed"}
        status = body.get("status")
        return states.get(status) if isinstance(status, str) else None, body

    @staticmethod
    def link(provider, job, body):
        signed = body.get("urls")
        if isinstance(signed, list) and signed:
            return signed[0], {"signed": True}
        # The documented content endpoint uses account authentication. Never
        # instruct users to send their key to a URL received in a response.
        content = provider.base_url + "/videos/" + job + "/content"
        unsigned = body.get("unsigned_urls")
        if not isinstance(unsigned, list) or not any(url in (content, content + "?index=0") for url in unsigned):
            raise ValueError("OpenRouter did not return a usable video content URL")
        return content + "?index=0", {"signed": False}

    @staticmethod
    def link_label(receipt):
        return "视频地址（临时签名链接）" if receipt.get("signed") else "视频地址（在 OpenRouter 请求头中提供 Bearer 凭证后下载）"

    @staticmethod
    def refresh_link(receipt):
        # Refresh a signed delivery on recovery without submitting another job.
        return bool(receipt.get("signed"))
