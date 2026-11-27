"""Run a single claimed model request under the host executor's lease."""

import json
from pathlib import Path
import sys

from generated_media import media_job
from model_policy import MEDIA_MODES
from model_patch import apply_patch_result, check_checkout
from model_plugins import PROVIDERS, complete
from provider_errors import InsufficientCreditError, ProviderError, RequestRejectedError
from model_video import video_job
from skill_sources import write_json


def run(request, workspace, output):
    result = {}
    error_code = "outcome_unknown"
    try:
        route = request["route"]
        if route["mode"] in MEDIA_MODES:
            result = media_job(PROVIDERS[route["provider"]], route, request["media_prompt"],
                               Path(request["run_dir"]), request["task"], request["media_generation"],
                               reuse_only=request.get("reuse_only", False))
        elif route["mode"] == "video":
            video_job(PROVIDERS[route["provider"]], route, request["video_prompt"], request["video_receipt"], result)
        else:
            if route["mode"] == "patch":
                error_code = "not_submitted"
                check_checkout(workspace, request["checkout"])
                error_code = "outcome_unknown"
            content, result["usage"] = complete(route, request["prompt"])
            error_code = "invalid_delivery"
            if "actual_model" in result["usage"]:
                result["actual_model"] = result["usage"].pop("actual_model")
            if route["mode"] == "patch":
                content = apply_patch_result(workspace, request["write_paths"], content, route["test_command"],
                                             supervised=True, checkout=request["checkout"])
            result["content"] = content
    except ProviderError as error:
        if isinstance(error, InsufficientCreditError):
            error_code = "insufficient_credit"
        elif isinstance(error, RequestRejectedError):
            error_code = "request_rejected"
        result["error_code"] = error_code
        result["error"] = str(error)[:500]
        if error.diagnostic:
            result["provider_error"] = error.diagnostic
    except (ValueError, RuntimeError, OSError) as error:
        result["error_code"] = error_code
        result["error"] = str(error)[:500]
    write_json(output, result)
    return int("error" in result)


if __name__ == "__main__":
    request_file, output_file = map(Path, sys.argv[1:])
    sys.exit(run(json.loads(request_file.read_text(encoding="utf-8")), Path.cwd(), output_file))
