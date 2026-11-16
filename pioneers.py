import argparse
from graphlib import CycleError, TopologicalSorter
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


MODEL_ROUTES = {
    "fast": ("https://api.deepseek.com/chat/completions", "DEEPSEEK"),
    "deep": ("https://api.openai.com/v1/chat/completions", "OPENAI"),
}


def load_workflow(path):
    workflow = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(workflow, dict):
        raise ValueError("Workflow must be a JSON object")
    goal = workflow.get("goal")
    items = workflow.get("tasks")
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("Workflow needs a non-empty goal")
    if not isinstance(items, list) or not items:
        raise ValueError("Workflow needs a non-empty tasks array")

    tasks = {}
    graph = TopologicalSorter()
    for task in items:
        if not isinstance(task, dict):
            raise ValueError("Each task must be a JSON object")
        task_id = task.get("id")
        if not isinstance(task_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", task_id):
            raise ValueError(f"Invalid task id: {task_id!r}")
        if task_id in tasks:
            raise ValueError(f"Duplicate task id: {task_id}")
        for field in ("role", "prompt"):
            if not isinstance(task.get(field), str) or not task[field].strip():
                raise ValueError(f"{task_id}: {field} must be a non-empty string")
        if task.get("route") not in ("fast", "deep", "code"):
            raise ValueError(f"{task_id}: route must be fast, deep or code")
        dependencies = task.get("depends_on", [])
        if not isinstance(dependencies, list) or not all(isinstance(dep, str) for dep in dependencies):
            raise ValueError(f"{task_id}: depends_on must be an array of task ids")
        tasks[task_id] = task
        graph.add(task_id, *dependencies)

    unknown = {
        dependency
        for task in tasks.values()
        for dependency in task.get("depends_on", [])
        if dependency not in tasks
    }
    if unknown:
        raise ValueError(f"Unknown dependencies: {', '.join(sorted(unknown))}")
    try:
        order = tuple(graph.static_order())
    except CycleError as error:
        raise ValueError(f"Task dependencies form a cycle: {' -> '.join(error.args[1])}") from error
    return goal, tasks, order


def task_prompt(goal, task, results):
    parts = [f"项目目标：{goal}", f"当前任务：{task['prompt']}"]
    for dependency in task.get("depends_on", []):
        parts.append(f"前置任务 {dependency} 的产物：\n{results[dependency]}")
    return "\n\n".join(parts)


def call_model(route, role, prompt):
    endpoint, prefix = MODEL_ROUTES[route]
    key = os.environ.get(f"{prefix}_API_KEY")
    model = os.environ.get(f"{prefix}_MODEL")
    if not key or not model:
        raise RuntimeError(f"Set {prefix}_API_KEY and {prefix}_MODEL for the {route} route")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": f"你是项目团队的{role}。完成当前任务并交付结果。"},
            {"role": "user", "content": prompt},
        ],
    }
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=120) as response:
            data = json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"{prefix} returned HTTP {error.code}") from error
    except (URLError, TimeoutError) as error:
        raise RuntimeError(f"{prefix} request failed: {error}") from error

    try:
        choice = data["choices"][0]
        if choice["finish_reason"] != "stop":
            raise RuntimeError(f"{prefix} did not finish normally: {choice['finish_reason']}")
        return choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError(f"{prefix} returned an unexpected response") from error


def run_codex(workspace, role, prompt):
    if workspace is None or not workspace.is_dir():
        raise RuntimeError("The code route needs --workspace pointing to a Git working directory")

    try:
        result = subprocess.run(
            ["codex", "exec", "--sandbox", "workspace-write", "-"],
            cwd=workspace,
            input=f"你是项目团队的{role}。\n\n{prompt}",
            capture_output=True,
            encoding="utf-8",
            timeout=600,
            check=False,
        )
    except FileNotFoundError as error:
        raise RuntimeError("Codex CLI was not found on PATH") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Codex exceeded the 600-second task timeout") from error
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        raise RuntimeError(f"Codex exited with status {result.returncode}{suffix}")
    return result.stdout


def run_workflow(goal, tasks, order, run_dir, live, workspace):
    results = {}
    for task_id in order:
        task = tasks[task_id]
        route = task["route"]
        role = task["role"]
        prompt = task_prompt(goal, task, results)
        print(f"[{route}] {task_id} ({role})", flush=True)
        if not live:
            result = f"离线演示，未执行任务。\n\n{prompt}"
        elif route == "code":
            result = run_codex(workspace, role, prompt)
        else:
            result = call_model(route, role, prompt)
        if not isinstance(result, str) or not result.strip():
            raise RuntimeError(f"{task_id} returned no text")
        (run_dir / f"{task_id}.md").write_text(result.strip() + "\n", encoding="utf-8")
        results[task_id] = result.strip()


def main():
    parser = argparse.ArgumentParser(description="Route a project workflow to different agent runners.")
    parser.add_argument("workflow", type=Path)
    parser.add_argument("--live", action="store_true", help="Call external APIs and execute Codex tasks.")
    parser.add_argument("--workspace", type=Path, help="Git working directory for live Codex tasks.")
    parser.add_argument("--output-dir", type=Path, default=Path(".pioneers/runs"))
    args = parser.parse_args()

    try:
        goal, tasks, order = load_workflow(args.workflow)
        workspace = args.workspace.resolve() if args.workspace else None
        if args.live and any(task["route"] == "code" for task in tasks.values()):
            if workspace is None or not workspace.is_dir():
                raise ValueError("Supply a Git working directory with --workspace before starting live execution")
            if not (workspace / ".git").exists():
                raise ValueError("--workspace must point to a Git repository or worktree root")
        args.output_dir.mkdir(parents=True, exist_ok=True)
        run_dir = Path(tempfile.mkdtemp(prefix="run-", dir=args.output_dir))
        print(f"Mode: {'live' if args.live else 'offline demo'}", flush=True)
        print(f"Run output: {run_dir.resolve()}", flush=True)
        run_workflow(goal, tasks, order, run_dir, args.live, workspace)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
