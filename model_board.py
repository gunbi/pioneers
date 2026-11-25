"""Project model directory and observed usage from Pioneers delivery runs."""

from datetime import datetime, timezone
from contextvars import copy_context
import re
from threading import Lock, Thread
from time import monotonic

from iterations import registered_run
from model_plugins import PROVIDERS, public_profiles
from model_policy import MEDIA_MODES, MODE_KINDS, banned, read_policy, update_policy
from provider_credentials import catalog_path, project_credentials
from skill_sources import read_json, write_json


MEDIA_FORMATS = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                 "webp": "image/webp", "mp3": "audio/mpeg", "wav": "audio/wav"}
MEDIA_FILENAME = re.compile(r"([a-z0-9][a-z0-9_-]*)\.([1-9][0-9]*)\.([a-z0-9]+)")


def media_reference(task_id, executor):
    """Only advertise framework-produced files bound to this task's execution record."""
    media = executor.get("media")
    if not isinstance(media, dict) or not isinstance(media.get("filename"), str):
        return None
    match = MEDIA_FILENAME.fullmatch(media["filename"])
    if not match or match[1] != task_id or match[3] not in MEDIA_FORMATS:
        return None
    mime = MEDIA_FORMATS[match[3]]
    mode = executor.get("mode")
    if (mode not in MEDIA_MODES or (mode == "image") != mime.startswith("image/") or media.get("mime") != mime or
            type(media.get("bytes")) is not int or media["bytes"] <= 0):
        return None
    return {"filename": media["filename"], "mime": mime, "bytes": media["bytes"]}


def run_usage(registry):
    rows = []
    for entry in registry.glob("run-*.json"):
        directory = registered_run(registry, entry.stem)
        if not directory:
            continue
        data = read_json(directory / "status.json")
        for task_id, task in data.get("tasks", {}).items():
            for attempt, executor in task.get("executors", {}).items():
                if not executor.get("model") or not str(attempt).isdigit():
                    continue
                usage = executor.get("usage") or {}
                video = executor.get("video") or {}
                total = usage.get("total_tokens")
                if total is None and all(isinstance(usage.get(key), int) for key in ("prompt_tokens", "completion_tokens")):
                    total = usage["prompt_tokens"] + usage["completion_tokens"]
                current = int(attempt) == task.get("attempt", 1)
                state = "failed" if executor.get("error") else task["state"] if current else "previous"
                generation = executor.get("video_generation")
                if current and state == "running" and executor.get("mode") == "video" and type(generation) is int:
                    receipt = read_json(directory / f".video-{task_id}-{generation}.json")
                    if receipt:
                        video = receipt
                rows.append({
                    "run": entry.stem, "title": data.get("title") or data.get("goal") or entry.stem,
                    "task": task_id, "attempt": int(attempt), "provider": executor["provider"],
                    "model": executor.get("actual_model") or executor["model"], "requested_model": executor["model"], "mode": executor.get("mode"), "state": state,
                    "started_at": executor.get("started_at"), "elapsed_seconds": executor.get("elapsed_seconds"),
                    "input": usage.get("prompt_tokens"), "output": usage.get("completion_tokens"), "total": total,
                    "videos": usage.get("videos"), "video_seconds": usage.get("video_seconds"),
                    "images": usage.get("images"), "audio_files": usage.get("audio_files"),
                    "media": media_reference(task_id, executor),
                    "request_id": video.get("request_id"), "video_state": video.get("state"),
                    "reason": executor.get("reason"), "error": executor.get("error"),
                })
    return sorted(rows, key=lambda row: (row["started_at"] or "", row["run"], row["task"], row["attempt"]), reverse=True)


class ModelBoard:
    def __init__(self, registry, name, workspace):
        self.registry = registry
        self.name = name
        self.workspace = workspace
        self.lock = Lock()
        self.refreshing = set()
        self.last_check = {}
        self.errors = {}

    def _refresh(self, name):
        cache = catalog_path(self.registry, name)
        try:
            provider = PROVIDERS[name]
            models = provider.list_catalog()
            self.registry.mkdir(parents=True, exist_ok=True)
            write_json(cache, {"models": models, "updated_at": datetime.now(timezone.utc).isoformat()})
            with self.lock:
                self.errors.pop(cache, None)
        except (OSError, ValueError, RuntimeError) as error:
            with self.lock:
                self.errors[cache] = str(error)
            raise
        finally:
            with self.lock:
                self.refreshing.discard(cache)
                self.last_check[cache] = monotonic()

    def _background_refresh(self, name):
        try:
            self._refresh(name)
        except (OSError, ValueError, RuntimeError):
            pass  # The next snapshot exposes the diagnostic alongside the last good directory.

    def providers(self):
        rows = []
        with project_credentials(self.registry) as source:
            for name, provider in PROVIDERS.items():
                cache = catalog_path(self.registry, name)
                saved = read_json(cache)
                try:
                    provider.api_key()
                    connected = True
                except ValueError:
                    connected = False
                with self.lock:
                    if ((connected or provider.public_catalog) and cache not in self.refreshing and
                            (cache not in self.last_check or monotonic() - self.last_check[cache] >= 600)):
                        self.refreshing.add(cache)
                        Thread(target=copy_context().run, args=(self._background_refresh, name), daemon=True).start()
                    rows.append({"id": name, "connected": connected, "public_catalog": provider.public_catalog, "models": saved.get("models", []),
                                 "updated_at": saved.get("updated_at"), "error": source.error or self.errors.get(cache),
                                 "refreshing": cache in self.refreshing})
        return rows

    def snapshot(self):
        policy = read_policy(self.registry)
        providers = self.providers()
        for provider in providers:
            for model in provider["models"]:
                if banned(policy, provider["id"], model["id"]):
                    model["blocked"] = True
        return {"project": self.name, "providers": providers, "policy": policy,
                "profiles": public_profiles(self.workspace) if self.workspace else [], "usage": run_usage(self.registry)}

    def apply(self, request):
        action = request.get("action")
        name = request.get("provider")
        if action in ("refresh", "select", "ban", "unban") and (not isinstance(name, str) or name not in PROVIDERS):
            raise ValueError("请选择已接入的模型提供商")
        if action == "refresh":
            with project_credentials(self.registry):
                cache = catalog_path(self.registry, name)
                with self.lock:
                    if cache in self.refreshing:
                        return {"ok": True, "refreshing": True}
                    self.refreshing.add(cache)
                self._refresh(name)
        else:
            if action == "select":
                with project_credentials(self.registry):
                    models = read_json(catalog_path(self.registry, name)).get("models", [])
                mode = request.get("mode")
                kind = MODE_KINDS.get(mode) if isinstance(mode, str) else None
                if not kind or not any(m["id"] == request.get("model") and kind in m["kinds"] for m in models):
                    raise ValueError("目录中未找到适用于此任务类型的模型，请先刷新目录")
            update_policy(self.registry, request)
        return {"ok": True}
