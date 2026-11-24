"""Project model directory and observed usage from Pioneers delivery runs."""

from datetime import datetime, timezone
import os
from threading import Lock, Thread
from time import monotonic

from iterations import registered_run
from model_plugins import PROVIDERS, public_profiles
from model_policy import banned, read_policy, update_policy
from skill_sources import read_json, write_json


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

    def _cache(self, name):
        return self.registry / ("model-catalog-" + name + ".json")

    def _refresh(self, name):
        try:
            provider = PROVIDERS[name]
            models = provider.list_catalog()
            self.registry.mkdir(parents=True, exist_ok=True)
            write_json(self._cache(name), {"models": models, "updated_at": datetime.now(timezone.utc).isoformat()})
            with self.lock:
                self.errors.pop(name, None)
        except (OSError, ValueError, RuntimeError) as error:
            with self.lock:
                self.errors[name] = str(error)
            raise
        finally:
            with self.lock:
                self.refreshing.discard(name)
                self.last_check[name] = monotonic()

    def _background_refresh(self, name):
        try:
            self._refresh(name)
        except (OSError, ValueError, RuntimeError):
            pass  # The next snapshot exposes the diagnostic alongside the last good directory.

    def providers(self):
        rows = []
        for name, provider in PROVIDERS.items():
            saved = read_json(self._cache(name))
            connected = bool(os.environ.get(provider.key_env, "").strip())
            with self.lock:
                if ((connected or provider.public_catalog) and name not in self.refreshing and
                        (name not in self.last_check or monotonic() - self.last_check[name] >= 600)):
                    self.refreshing.add(name)
                    Thread(target=self._background_refresh, args=(name,), daemon=True).start()
                rows.append({"id": name, "connected": connected, "public_catalog": provider.public_catalog, "models": saved.get("models", []),
                             "updated_at": saved.get("updated_at"), "error": self.errors.get(name),
                             "refreshing": name in self.refreshing})
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
            with self.lock:
                if name in self.refreshing:
                    return {"ok": True, "refreshing": True}
                self.refreshing.add(name)
            self._refresh(name)
        else:
            if action == "select":
                models = read_json(self._cache(name)).get("models", [])
                mode = request.get("mode")
                kind = {"text": "chat", "patch": "chat", "video": "text-to-video"}.get(mode) if isinstance(mode, str) else None
                if not kind or not any(m["id"] == request.get("model") and kind in m["kinds"] for m in models):
                    raise ValueError("目录中未找到适用于此任务类型的模型，请先刷新目录")
            update_policy(self.registry, request)
        return {"ok": True}
