"""Safe errors that distinguish account billing from request-specific rejections."""

from http.client import HTTPException
import json
import re


class ProviderError(RuntimeError):
    """A diagnostic safe to record on a task attempt."""

    def __init__(self, message, *, diagnostic=None):
        super().__init__(message)
        self.diagnostic = diagnostic


class TransientProviderError(ProviderError):
    """Transport or temporary upstream failure. Submitted inference may have run."""


class RequestRejectedError(ProviderError):
    """The provider explicitly rejected the request before inference."""


class RateLimitedError(RequestRejectedError):
    """The endpoint rejected this request because of its current rate limit."""


class InsufficientCreditError(RequestRejectedError):
    """An authenticated provider rejected the request for lack of account credit."""


def account_credit_rejected(provider, status, body):
    if status != 402:
        return False
    if provider == "SiliconFlow":
        return True
    if provider != "OpenRouter":
        return False
    # OpenRouter also uses 402 for per-key limits and per-request token budgets.
    detail = body.get("error")
    if not isinstance(detail, dict) or detail.get("code", 402) not in (402, "402"):
        return False
    meta = detail.get("metadata", {})
    if not isinstance(meta, dict) or meta.get("limit_source") not in (None, "openrouter_credits"):
        return False
    if meta.get("provider_name") or meta.get("reason") == "in_flight_budget_exhausted":
        return False
    message = detail.get("message")
    if not isinstance(message, str):
        return False
    message = message.strip().casefold()
    if any(clue in message for clue in ("max_tokens", "max tokens", "can only afford", "requires more credits",
                                        "api key", "key spending", "key limit", "key budget", "in-flight", "in flight")):
        return False
    return message.startswith(("insufficient credits", "insufficient balance", "out of credits",
                               "your account has insufficient credits", "your account is out of credits"))


# Only known field names and bounded identifiers may reach the dashboard.
# Error messages often echo the entire rejected prompt or a signed URL.
_FIELDS = ("image_size", "aspect_ratio", "num_inference_steps", "guidance_scale", "response_format",
           "max_tokens", "temperature", "duration", "resolution", "ratio", "model", "voice", "prompt", "messages")
_FIELD = re.compile(r"\b(?:" + "|".join(_FIELDS) + r")\b", re.I)
_CODE_WORDS = frozenset((
    "invalid missing unsupported insufficient bad rate request model content server permission provider context "
    "parameter param params field required type internal input validation billing access quota exceeded not found "
    "code token tokens key api authentication limit limited credits balance error length policy rejected "
    "image size resolution aspect ratio format failed maximum safety unavailable denied").split())
_TRACE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{3,127}")
_CREDENTIAL = re.compile(r"(?:sk[-_]|bearer|basic[ :]|gh[pousr]_|github_pat_|akia|eyJ)", re.I)
_HTTP_REASONS = {400: "invalid request parameters", 401: "invalid credential", 402: "payment required",
                 403: "access denied", 404: "model or endpoint unavailable", 408: "request timed out",
                 413: "request too large", 415: "unsupported content type", 422: "request validation failed",
                 429: "rate limited", 500: "server error", 502: "bad gateway", 503: "service unavailable",
                 504: "gateway timed out"}


def read_error_body(error):
    try:
        body = error.read(16385)
        if len(body) <= 16384:
            value = json.loads(body)
            if isinstance(value, dict):
                return value
    except (ValueError, OSError, HTTPException, RecursionError):
        pass
    return {}


def _reason(detail, http_status, credit):
    if credit:
        return "insufficient account credits"
    message = detail.get("message")
    message = message.casefold() if isinstance(message, str) else ""
    param = detail.get("param")
    match = _FIELD.search(message)
    field = param if isinstance(param, str) and param in _FIELDS else None
    if not field and match:
        field = match.group().lower()
    hints = (("required", ("required", "missing", "not provided", "不能为空", "缺少", "必填")),
             ("unsupported", ("not support", "unsupported", "not allowed", "not available", "does not exist", "不存在", "不支持")),
             ("out of range", ("range", "exceed", "too long", "too large", "too small", "too short", "at least", "at most", "超出")),
             ("invalid", ("invalid", "incorrect", "not valid", "must be", "should be", "format", "不合法", "无效", "格式错误")))
    for reason, clues in hints:
        if any(clue in message for clue in clues):
            return f"{field}: {reason}" if field else f"request parameter: {reason}"
    if any(clue in message for clue in ("moderation", "content filter", "safety policy", "content policy", "内容审核", "安全审核")):
        return "content policy rejection"
    if "overload" in message:
        return "model service overloaded"
    if field and http_status in (400, 422):
        return f"{field}: validation failed"
    return _HTTP_REASONS.get(http_status, "provider rejected the request")


def public_http_error(provider, http_status, body, headers, *, credential=None, credit=False):
    """Whitelist diagnostics from the vendor envelope. Body, user input and arbitrary headers stay private."""
    detail = body.get("error")
    if not isinstance(detail, dict):
        detail = body
    result = {"http_status": http_status, "reason": _reason(detail, http_status, credit)}
    code = detail.get("code")
    if type(code) in (str, int):
        label = str(code)
        if (len(label) <= 64 and not (credential and credential in label)
                and (re.fullmatch(r"[0-9]{1,12}", label)
                     or all(word in _CODE_WORDS for word in label.lower().split("_")))):
            result["code"] = label
    names = ("x-siliconcloud-trace-id", "x-trace-id", "x-request-id") if provider == "SiliconFlow" else ("x-request-id", "x-trace-id")
    normalized = {str(k).lower(): v for k, v in (headers or {}).items() if str(k).lower() in names}
    for name in names:
        value = normalized.get(name)
        if (isinstance(value, str) and _TRACE.fullmatch(value) and not _CREDENTIAL.search(value)
                and not (credential and credential in value)):
            result["trace_id"] = value
            break
    return result
