"""Project opt-in and provider adapters for host-dispatched model tasks."""

from contextlib import contextmanager
from contextvars import ContextVar
import json
import re
import socket
import time
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from dispatch import writes_workspace
from media_planning import video_plan_issue
from model_policy import MEDIA_MODES, MODEL_ID, MODES, banned, read_policy
from model_stream import read_completion, response_lines
from model_timeouts import seconds
from provider_credentials import provider_key
from provider_errors import (
    InsufficientCreditError, ProviderError, RateLimitedError, RequestRejectedError, TransientProviderError,
    account_credit_rejected, public_http_error, read_error_body,
)
import openrouter_media
import siliconflow_media


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A credential must only reach the endpoint registered by the plugin.
        return None


_preflight_reads = ContextVar("preflight_reads", default=None)


@contextmanager
def shared_preflight():
    token = _preflight_reads.set({})
    try:
        yield
    finally:
        _preflight_reads.reset(token)


class ChatProvider:
    public_catalog = False
    report_actual_model = False
    stream_options = {}

    @classmethod
    def api_key(cls):
        key = provider_key(cls.key_env)
        if not key or not key.isascii() or not key.isprintable() or any(c.isspace() for c in key):
            raise ValueError(f"Set a valid {cls.key_env} for the {cls.name} executor")
        return key

    @classmethod
    def request(cls, path, *, payload=None, timeout, public=False, raw=False, max_bytes=8 * 1024 * 1024,
                with_content_type=False, stream_reader=None, stream_total=None, stream_idle=None):
        cache = _preflight_reads.get() if payload is None and not raw else None
        key = (cls, path, public, timeout, max_bytes)
        if cache is not None and key in cache:
            result = cache[key]
            if isinstance(result, ProviderError):
                raise result
            return result
        try:
            result = cls._request(path, payload, timeout, public, raw, max_bytes, with_content_type,
                                  stream_reader, stream_total, stream_idle)
        except ProviderError as error:
            if cache is not None:
                cache[key] = error
            raise
        if cache is not None:
            cache[key] = result
        return result

    @classmethod
    def _request(cls, path, payload, timeout, public, raw, max_bytes, with_content_type,
                 stream_reader, stream_total, stream_idle):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"}
        credential = None if public else cls.api_key()
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        request = Request(cls.base_url + path, data=data, headers=headers)
        started = time.monotonic()
        if stream_reader:
            timeout = min(timeout, stream_total)
        try:
            with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
                if stream_reader:
                    lines = response_lines(response, max_bytes, started=started, first=timeout,
                                           idle=stream_idle, total=stream_total)
                    return stream_reader(lines)
                body = response.read(max_bytes + 1)
                if raw and with_content_type:
                    content_type = response.headers.get("Content-Type", "")
            if len(body) > max_bytes:
                raise ProviderError(f"{cls.name} response exceeds the {max_bytes / (1024 * 1024):g} MiB limit")
            if raw:
                return (body, content_type) if with_content_type else body
            return json.loads(body)
        except HTTPError as error:
            code = error.code
            with error:
                body = read_error_body(error)
                credit = not public and account_credit_rejected(cls.name, code, body)
                diagnostic = public_http_error(cls.name, code, body, error.headers, credential=credential, credit=credit)
            if credit:
                raise InsufficientCreditError(f"{cls.name} HTTP {code}: insufficient credits", diagnostic=diagnostic) from None
            hint = {401: "invalid credential", 402: "payment required", 403: "access denied",
                    404: "model or endpoint unavailable", 408: "request timed out", 429: "rate limited",
                    500: "server error", 502: "bad gateway", 503: "service unavailable",
                    504: "gateway timed out"}.get(code, "request rejected")
            error_type = RequestRejectedError if code in (400, 401, 402, 403, 404, 405, 413, 415, 422, 429) else ProviderError
            if code == 429:
                error_type = RateLimitedError
            elif code == 408 or 500 <= code < 600:
                error_type = TransientProviderError
            raise error_type(f"{cls.name} HTTP {code}: {hint}", diagnostic=diagnostic) from None
        except (OSError, HTTPException) as error:
            timed_out = isinstance(error, (TimeoutError, socket.timeout)) or (
                isinstance(error, URLError) and isinstance(error.reason, (TimeoutError, socket.timeout)))
            detail = "timed out" if timed_out else "connection failed"
            raise TransientProviderError(f"{cls.name} {detail}") from None
        except ValueError:
            raise ProviderError(f"{cls.name} returned invalid JSON") from None

    @classmethod
    def complete(cls, model, prompt):
        payload = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": True}
        payload.update(cls.stream_options)
        body = cls.request("/chat/completions", payload=payload, timeout=seconds("PIONEERS_MODEL_FIRST_RESPONSE_SECONDS"),
                           stream_reader=read_completion, stream_total=seconds("PIONEERS_MODEL_TOTAL_SECONDS"),
                           stream_idle=seconds("PIONEERS_MODEL_STREAM_IDLE_SECONDS"))
        choices = body.get("choices") if isinstance(body, dict) else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderError(f"{cls.name} returned no completion")
        choice = choices[0]
        if choice.get("finish_reason") == "length":
            raise ProviderError(f"{cls.name} output was truncated at the token limit")
        if choice.get("finish_reason") != "stop":
            raise ProviderError(f"{cls.name} did not finish the requested text completion")
        message = choice.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ProviderError(f"{cls.name} returned an empty completion")
        usage = body.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        counts = {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                  if type(usage.get(key)) is int and usage[key] >= 0}
        if cls.report_actual_model and isinstance(body.get("model"), str) and MODEL_ID.fullmatch(body["model"]):
            counts["actual_model"] = body["model"]
        return content, counts


class SiliconFlow(ChatProvider):
    name = "SiliconFlow"
    key_env = "SILICONFLOW_API_KEY"
    base_url = "https://api.siliconflow.cn/v1"

    @classmethod
    def list_catalog(cls):
        return siliconflow_media.list_catalog(cls)

    @classmethod
    def check_available(cls, model, mode, options):
        subtype = "text-to-video" if mode == "video" else "chat"
        body = cls.request("/models?sub_type=" + subtype, timeout=15)
        models = body.get("data") if isinstance(body, dict) else None
        if not isinstance(models, list):
            raise ProviderError("SiliconFlow returned an invalid model catalog")
        if not any(isinstance(entry, dict) and entry.get("id") == model for entry in models):
            raise ProviderError(f"Model {model} is unavailable in this SiliconFlow {subtype} catalog")

    video = siliconflow_media.Video


class OpenRouter(ChatProvider):
    name = "OpenRouter"
    key_env = "OPENROUTER_API_KEY"
    base_url = "https://openrouter.ai/api/v1"
    public_catalog = True
    report_actual_model = True
    stream_options = {"stream_options": {"include_usage": True}}

    video = openrouter_media.Video

    @classmethod
    def list_catalog(cls):
        return openrouter_media.catalog(cls)

    @classmethod
    def check_available(cls, model, mode, options):
        openrouter_media.check_available(cls, model, mode, options)


PROVIDERS = {"siliconflow": SiliconFlow, "openrouter": OpenRouter}


def models_config(workspace):
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


def public_profiles(workspace, config=None):
    profiles, _ = config if config is not None else models_config(workspace)
    return [_profile_info(name, spec) for name, spec in profiles.items()]


def dispatch_models(registry, workspace):
    context = {"model_policy": read_policy(registry)}
    try:
        context["model_profiles"] = public_profiles(workspace)
    except (ValueError, OSError) as error:
        context["model_profiles_error"] = str(error)
    return context


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


def task_mode(task):
    if task.get("tools") or task.get("review") or task.get("effect"):
        return "host"
    if task.get("media"):
        return task["media"]["type"]
    if task.get("video_prompt"):
        return "video"
    return "patch" if writes_workspace(task) else "text"


def project_default(config, task, policy=None):
    profiles, defaults = config
    mode = task_mode(task)
    type_default = defaults.get("types", {}).get(mode)
    name = type_default or defaults.get("roles", {}).get(task["role"])
    if name in profiles:
        entry = _profile_info(name, profiles[name])
        if not type_default and entry["mode"] != mode:
            return None
        if policy and entry["provider"] in policy.get("banned_providers", ()):
            return None
    return name


def project_pin(policy, mode):
    selected = policy.get("selected", {}).get(mode)
    if selected and selected["provider"] not in policy.get("banned_providers", ()):
        return selected
    return None


def resolve_route(workspace, task, profile=None, *, isolated, policy=None, require_key=True, candidate=None, config=None):
    """Resolve against the original project at claim time. None selects the native host."""
    if profile == "host":
        return None
    config = config if config is not None else models_config(workspace)
    profiles, _ = config
    policy = policy or {}
    if profile in profiles and _profile_info(profile, profiles[profile])["provider"] in policy.get("banned_providers", ()):
        raise ValueError(f"项目已禁用此配置的模型提供商：{profiles[profile]['provider']}")
    task_type = task_mode(task)
    selected = project_pin(policy, task_type)
    if candidate:
        selected = {key: candidate[key] for key in ("provider", "model")}
    reason = "宿主指定配置"
    if selected:
        default = project_default(config, task)
        # Copy execution options only from a compatible project-owned template.
        compatible = [name for name, spec in profiles.items() if _profile_info(name, spec)["mode"] == task_type]
        if profile is not None and profile not in compatible:
            raise ValueError(f"Choose a compatible {task_type} profile for the project selection")
        matching = [name for name in compatible if all(profiles[name].get(k) == v for k, v in selected.items())]
        # Video parameter sets belong to the selected provider. Keep a trusted project
        # test command when switching a patch profile across providers.
        if task_type == "video":
            compatible = [name for name in compatible if profiles[name]["provider"] == selected["provider"]]
        template = (profile if profile in compatible else None) or (default if default in compatible else None)
        if template is None and len(matching) == 1:
            template = matching[0]
        if template is None and len(compatible) == 1:
            template = compatible[0]
        if task_type == "patch" and not template:
            raise ValueError("Project patch selection requires an unambiguous patch profile with test_command")
        spec = {**profiles.get(template, {}), **selected, "mode": task_type}
        profile = template or ("auto" if candidate else "dashboard")
        reason = candidate["reason"] if candidate else "项目已指定此模型"
    else:
        if profile is None:
            default = project_default(config, task, policy)
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
    if task_type == "host":
        raise ValueError("Use the host executor for tasks with tools, external effects or independent acceptance")
    if mode != task_type:
        raise ValueError(f"This task requires mode {task_type}; choose a compatible profile or host")
    route = {"profile": profile, "provider": provider, "model": model, "mode": mode, "reason": reason}
    if mode == "speech" and "voice" in task["media"]:
        route["voice"] = task["media"]["voice"]
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
        if issue := video_plan_issue(task):
            raise ValueError(f"请 LD 重新规划，{issue}")
        # The task describes the deliverable; profiles supply only omitted options.
        route.update(PROVIDERS[provider].video.settings({**spec, **task.get("video_settings", {})}))
    elif video_fields:
        raise ValueError("Video generation settings require a video profile")
    if require_key:
        PROVIDERS[provider].api_key()
    return route


def complete(route, prompt):
    return PROVIDERS[route["provider"]].complete(route["model"], prompt)


def check_available(route):
    provider = PROVIDERS[route["provider"]]
    mode = route.get("mode", "text")
    if mode in MEDIA_MODES:
        import media_adapters
        media_adapters.check_available(provider, route["model"], mode, route)
    else:
        provider.check_available(route["model"], mode, route)
