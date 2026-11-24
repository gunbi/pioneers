"""Project opt-in and provider adapters for host-dispatched model tasks."""

import json
import os
import re
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from dispatch import writes_workspace


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
    def check_available(cls, model):
        body = cls.request("/models?sub_type=chat", timeout=15)
        models = body.get("data") if isinstance(body, dict) else None
        if not isinstance(models, list):
            raise ProviderError("SiliconFlow returned an invalid model catalog")
        if not any(isinstance(entry, dict) and entry.get("id") == model for entry in models):
            raise ProviderError(f"Model {model} is unavailable in this SiliconFlow chat catalog")

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


def resolve_route(workspace, task, profile=None, *, isolated):
    """Resolve against the original project at claim time. None selects the native host."""
    if profile == "host":
        return None
    profiles, defaults = _profiles_config(workspace)
    requires_host = bool(task.get("tools") or task.get("review") or task.get("effect"))
    task_type = "host" if requires_host else ("patch" if writes_workspace(task) else "text")
    if profile is None:
        profile = defaults.get("types", {}).get(task_type, defaults.get("roles", {}).get(task["role"]))
    if profile is None or profile == "host":
        return None
    if not isinstance(profile, str) or profile not in profiles:
        raise ValueError(f"Unknown model profile: {profile}")
    spec = profiles[profile]
    if not isinstance(spec, dict) or set(spec) - {"provider", "model", "mode", "test_command"}:
        raise ValueError("Model profile must contain provider, model, mode and optional patch test_command")
    provider, model, mode = spec.get("provider"), spec.get("model"), spec.get("mode")
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise ValueError("Model profile references an unsupported provider")
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:+-]{0,255}", model):
        raise ValueError("Model profile needs a valid provider model ID")
    if requires_host:
        raise ValueError("Use the host executor for tasks with tools, external effects or independent acceptance")
    if mode != task_type:
        raise ValueError(f"This task requires mode {task_type}; choose a compatible profile or host")
    route = {"profile": profile, "provider": provider, "model": model, "mode": mode}
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
    PROVIDERS[provider].api_key()
    return route


def complete(route, prompt):
    return PROVIDERS[route["provider"]].complete(route["model"], prompt)


def check_available(route):
    PROVIDERS[route["provider"]].check_available(route["model"])
