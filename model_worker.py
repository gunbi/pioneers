"""Run a single claimed model request under the host executor's lease."""

import json
from pathlib import Path
import sys

from model_patch import apply_patch_result, check_checkout
from model_plugins import complete
from skill_sources import write_json


def run(request, workspace, output):
    result = {}
    try:
        route = request["route"]
        if route["mode"] == "patch":
            check_checkout(workspace, request["checkout"])
        content, result["usage"] = complete(route, request["prompt"])
        if route["mode"] == "patch":
            content = apply_patch_result(workspace, request["write_paths"], content, route["test_command"],
                                         supervised=True, checkout=request["checkout"])
        result["content"] = content
    except (ValueError, RuntimeError, OSError) as error:
        result["error"] = str(error)[:500]
    write_json(output, result)
    return int("error" in result)


if __name__ == "__main__":
    request_file, output_file = map(Path, sys.argv[1:])
    sys.exit(run(json.loads(request_file.read_text(encoding="utf-8")), Path.cwd(), output_file))
