"""Project opt-in and provider adapters for host-dispatched model tasks."""

import json
import os
import re
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from dispatch import writes_workspace
from model_policy import MODEL_ID, MODES, read_policy


class ProviderError(RuntimeError):
    """A safe diagnostic that can be recorded on a task attempt."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A credential must only reach the endpoint registered by the plugin.
        return None


class SiliconFlow:
    key_env = "SILICONFLOW_API_KEY"
    base_url = "https://api.siliconflow.cn/v1"

    @classmethod
    def api_key(cls):
        key = os.environ.get(cls.key_env, "").strip()
        if not key or not key.isascii() or not key.isprintable() or any(c.isspace() for c in key):
            raise ValueError(f"Set a valid {cls.key_env} for the SiliconFlow executor")
        return key

    @classmethod
    def request(cls, path, *, payload=None, timeout):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = Request(cls.base_url + path, data=data,
                          headers={"Authorization": f"Bearer {cls.api_key()}", "Content-Type": "application/json"})
        try:
            with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
                limit = 8 * 1024 * 1024
                raw = response.read(limit + 1)
            if len(raw) > limit:
                raise ProviderError("SiliconFlow response exceeds the 8 MiB limit")
            return json.loads(raw)
        except HTTPError as error:
            code = error.code
            error.close()
            hint = {401: "invalid credential", 402: "insufficient credits", 403: "access denied",
                    404: "model or endpoint unavailable", 408: "request timed out", 429: "rate limited",
                    504: "gateway timed out"}.get(code, "request rejected")
            raise ProviderError(f"SiliconFlow HTTP {code}: {hint}") from None
        except (OSError, HTTPException) as error:
            timed_out = isinstance(error, TimeoutError) or (
                isinstance(error, URLError) and isinstance(error.reason, TimeoutError))
            detail = "timed out" if timed_out else "connection failed"
            raise ProviderError(f"SiliconFlow {detail}") from None
        except ValueError:
            raise ProviderError("SiliconFlow returned invalid JSON") from None

    @classmethod
    def list_catalog(cls):
        from siliconflow_media import list_catalog
        return list_catalog(cls)

    @classmethod
    def check_available(cls, model, mode):
        subtype = "text-to-video" if mode == "video" else "chat"
        body = cls.request("/models?sub_type=" + subtype, timeout=15)
        models = body.get("data") if isinstance(body, dict) else None
        if not isinstance(models, list):
            raise ProviderError("SiliconFlow returned an invalid model catalog")
        if not any(isinstance(entry, dict) and entry.get("id") == model for entry in models):
            raise ProviderError(f"Model {model} is unavailable in this SiliconFlow {subtype} catalog")

    @classmethod
    def complete(cls, model, prompt):
        payload = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False}
        body = cls.request("/chat/completions", payload=payload, timeout=120)
        choices = body.get("choices") if isinstance(body, dict) else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderError("SiliconFlow returned no completion")
        choice = choices[0]
        if choice.get("finish_reason") == "length":
            raise ProviderError("SiliconFlow output was truncated at the token limit")
        if choice.get("finish_reason") != "stop":
            raise ProviderError("SiliconFlow did not finish the requested text completion")
        message = choice.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("SiliconFlow returned an empty completion")
        usage = body.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        counts = {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                  if type(usage.get(key)) is int and usage[key] >= 0}
        return content, counts


PROVIDERS = {"siliconflow": SiliconFlow}


def _profiles_config(workspace):
    path = Path(workspace) / ".pioneers" / "models.json"
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("Model configuration must be local to the project")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, {}
    except ValueError:
        raise ValueError("Invalid JSON in .pioneers/models.json") from None
    if (not isinstance(data, dict) or set(data) - {"profiles", "defaults"}
            or not isinstance(data.get("profiles"), dict)):
        raise ValueError("Expected a profiles object in .pioneers/models.json")
    profiles = data["profiles"]
    if "host" in profiles:
        raise ValueError("The host profile name is reserved for the native executor")
    defaults = data.get("defaults", {})
    if not isinstance(defaults, dict) or set(defaults) - {"types", "roles"}:
        raise ValueError("Model defaults must contain types or roles mappings")
    for kind in ("types", "roles"):
        mapping = defaults.get(kind, {})
        if not isinstance(mapping, dict) or any(
            not isinstance(name, str) or (name != "host" and name not in profiles) for name in mapping.values()
        ):
            raise ValueError(f"Model defaults.{kind} must reference configured profiles or host")
    return profiles, defaults


def _profile_info(name, spec):
    allowed = {"provider", "model", "mode", "test_command", "tags", "duration", "resolution", "ratio"}
    if not isinstance(spec, dict) or set(spec) - allowed:
        raise ValueError("Invalid model profile fields")
    provider, model, mode = spec.get("provider"), spec.get("model"), spec.get("mode")
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise ValueError("Model profile references an unsupported provider")
    if not isinstance(model, str) or not MODEL_ID.fullmatch(model):
        raise ValueError("Model profile needs a valid provider model ID")
    if mode not in MODES:
        raise ValueError("Unknown model profile mode")
    tags = spec.get("tags", [])
    if not isinstance(tags, list) or len(tags) > 32 or not all(isinstance(tag, str) and 0 < len(tag.strip()) <= 64 for tag in tags):
        raise ValueError("Model profile tags must be a list of short strings")
    return {"name": name, "provider": provider, "model": model, "mode": mode, "tags": tags}


def public_profiles(workspace):
    profiles, _ = _profiles_config(workspace)
    return [_profile_info(name, spec) for name, spec in profiles.items()]


def dispatch_models(registry, workspace):
    context = {"model_policy": read_policy(registry)}
    try:
        context["model_profiles"] = public_profiles(workspace)
    except (ValueError, OSError) as error:
        context["model_profiles_error"] = str(error)
    return context


def banned(policy, provider, model):
    return {"provider": provider, "model": model} in policy.get("banned", [])


def _preferred_profile(profiles, mode, policy, default):
    preference = policy.get("preference", "").strip().casefold()
    if not preference or re.search(r"不|避免|排除|慎用|禁|avoid|never|except|\bnot\b|don't", preference):
        return None
    matches = []
    for name, spec in profiles.items():
        entry = _profile_info(name, spec)
        if entry["mode"] != mode or banned(policy, entry["provider"], entry["model"]):
            continue
        labels = [entry["model"], name, *entry["tags"]]
        if any(label.casefold() in preference for label in labels):
            matches.append(name)
    if default in matches:
        return default
    return matches[0] if len(matches) == 1 else None


def resolve_route(workspace, task, profile=None, *, isolated, policy=None):
    """Resolve against the original project at claim time. None selects the native host."""
    if profile == "host":
        return None
    profiles, defaults = _profiles_config(workspace)
    policy = policy or {}
    requires_host = bool(task.get("tools") or task.get("review") or task.get("effect"))
    task_type = "video" if task.get("video_prompt") else ("patch" if writes_workspace(task) else "text")
    if requires_host:
        task_type = "host"
    default = defaults.get("types", {}).get(task_type, defaults.get("roles", {}).get(task["role"]))
    selected = policy.get("selected", {}).get(task_type)
    reason = "宿主指定配置"
    if selected:
        # Copy execution options only from a compatible project-owned template.
        compatible = [name for name, spec in profiles.items() if _profile_info(name, spec)["mode"] == task_type]
        if profile is not None and profile not in compatible:
            raise ValueError(f"Choose a compatible {task_type} profile for the project selection")
        matching = [name for name in compatible if all(profiles[name].get(k) == v for k, v in selected.items())]
        template = profile or (default if default in compatible else None)
        if template is None and len(matching) == 1:
            template = matching[0]
        if template is None and len(compatible) == 1:
            template = compatible[0]
        if task_type == "patch" and not template:
            raise ValueError("Project patch selection requires an unambiguous patch profile with test_command")
        spec = {**profiles.get(template, {}), **selected, "mode": task_type}
        profile = template or "dashboard"
        reason = "项目已指定此模型"
    else:
        if profile is None:
            preferred = _preferred_profile(profiles, task_type, policy, default)
            profile = preferred or default
            reason = "匹配项目倾向与模型名或标签" if preferred else "项目任务类型或角色默认配置"
            if policy.get("preference") and not preferred:
                reason += "（倾向未唯一匹配，已提供给宿主）"
        spec = profiles.get(profile)
    if profile is None or profile == "host":
        return None
    if not isinstance(profile, str) or spec is None:
        raise ValueError(f"Unknown model profile: {profile}")
    entry = _profile_info(profile, spec)
    provider, model, mode = entry["provider"], entry["model"], entry["mode"]
    if banned(policy, provider, model):
        raise ValueError(f"项目已禁用模型 {provider} / {model}")
    if requires_host:
        raise ValueError("Use the host executor for tasks with tools, external effects or independent acceptance")
    if mode != task_type:
        raise ValueError(f"This task requires mode {task_type}; choose a compatible profile or host")
    route = {"profile": profile, "provider": provider, "model": model, "mode": mode, "reason": reason}
    if mode == "patch":
        if not isolated:
            raise ValueError("Patch profiles require an isolated Git worktree")
        from model_patch import validate_paths
        validate_paths(Path(workspace), task.get("write_paths", []))
        command = spec.get("test_command")
        if not isinstance(command, list) or not command or not all(
                isinstance(arg, str) and arg and "\x00" not in arg for arg in command):
            raise ValueError("Patch profiles require test_command as an argv array")
        route["test_command"] = list(command)
    elif "test_command" in spec:
        raise ValueError("Only patch profiles can run a test_command")
    video_fields = {"duration", "resolution", "ratio"} & spec.keys()
    if mode == "video":
        duration = spec.get("duration", 5)
        resolution = spec.get("resolution", "768p")
        ratio = spec.get("ratio", "16:9")
        if (type(duration) is not int or not 5 <= duration <= 15 or resolution not in ("480p", "768p")
                or ratio not in ("1:1", "3:4", "4:3", "9:16", "16:9", "21:9")):
            raise ValueError("Unsupported video duration, resolution or aspect ratio")
        route.update(duration=duration, resolution=resolution, ratio=ratio)
    elif video_fields:
        raise ValueError("Video generation settings require a video profile")
    PROVIDERS[provider].api_key()
    return route


def complete(route, prompt):
    return PROVIDERS[route["provider"]].complete(route["model"], prompt)


def check_available(route):
    PROVIDERS[route["provider"]].check_available(route["model"], route.get("mode", "text"))
