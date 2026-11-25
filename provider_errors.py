"""Safe errors that distinguish account billing from request-specific rejections."""

from http.client import HTTPException
import json


class ProviderError(RuntimeError):
    """A diagnostic safe to record on a task attempt."""


class InsufficientCreditError(ProviderError):
    """An authenticated provider rejected the request for lack of account credit."""


def account_credit_rejected(provider, error):
    if error.code != 402:
        return False
    if provider == "SiliconFlow":
        return True
    if provider != "OpenRouter":
        return False
    # OpenRouter also uses 402 for per-key limits and per-request token budgets.
    try:
        body = error.read(16385)
        if len(body) > 16384:
            return False
        detail = json.loads(body).get("error")
    except (ValueError, OSError, HTTPException, AttributeError, RecursionError):
        return False
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
