"""Wait budgets for paid requests and their supervised workers, in seconds."""

import os

from model_patch import TEST_TIMEOUT


DEFAULTS = {
    "PIONEERS_MODEL_FIRST_RESPONSE_SECONDS": 300,
    "PIONEERS_MODEL_STREAM_IDLE_SECONDS": 120,
    "PIONEERS_MODEL_TOTAL_SECONDS": 900,
    "PIONEERS_MEDIA_RESPONSE_SECONDS": 600,
    "PIONEERS_MEDIA_TOTAL_SECONDS": 1800,
}


def seconds(name):
    value = os.environ.get(name, DEFAULTS[name])
    try:
        n = int(value)
        if not 1 <= n <= 86400:
            raise ValueError
        return n
    except ValueError:
        raise ValueError(f"{name} must be an integer from 1 to 86400 seconds") from None


def worker_limit(mode):
    # Validate overrides before allowing the worker to submit a paid request.
    limits = {name: seconds(name) for name in DEFAULTS}
    if mode == "patch":
        return limits["PIONEERS_MODEL_TOTAL_SECONDS"] + TEST_TIMEOUT
    if mode == "text":
        return limits["PIONEERS_MODEL_TOTAL_SECONDS"]
    return limits["PIONEERS_MEDIA_TOTAL_SECONDS"]
