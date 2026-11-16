import argparse
from dataclasses import asdict, dataclass
from graphlib import CycleError, TopologicalSorter
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


@dataclass(frozen=True)
class Role:
    name: str
    responsibility: str
    sandbox: str = "read-only"


ROLES = {
    "ld": Role("团队负责人", "明确交付目标、任务依赖和各角色的交接要求。"),
    "pm": Role("产品经理", "定义用户场景、首版范围和可复现的验收条件。"),
    "ui": Role("UI 设计师", "说明页面布局、交互状态和设计交付要求。"),
    "backend": Role("后端开发", "实现接口和业务逻辑，验证接口行为。", "workspace-write"),
    "frontend": Role("前端开发", "实现 Web 界面和交互，验证核心用户流程。", "workspace-write"),
    "ios": Role("iOS 开发", "实现 iOS 用户流程，记录构建和设备验证结果。", "workspace-write"),
    "android": Role("安卓开发", "实现 Android 用户流程，记录构建和设备验证结果。", "workspace-write"),
    "qa": Role("测试", "按照验收条件检查变更，记录测试证据、失败项和未验证项。"),
}


def team_path(workspace):
    if workspace is None or not (workspace / ".git").exists():
        raise ValueError("--workspace must point to a Git repository or worktree root")
    path = workspace / ".pioneers" / "team.json"
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError("The team directory and manifest must be local to the workspace")
    return path


def read_team(path):
    try:
        team = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if (
        not isinstance(team, dict)
        or team.get("schema_version") != 1
        or not isinstance(team.get("roles"), dict)
        or "model" not in team
        or (team["model"] is not None and not isinstance(team["model"], str))
    ):
        raise ValueError(f"Invalid team manifest: {path}")
    return team


def create_team(path, model):
    team = {
        "schema_version": 1,
        "model": model,
        "roles": {role_id: asdict(role) for role_id, role in ROLES.items()},
    }
    path.parent.mkdir(exist_ok=True)
    # Publish the complete manifest atomically and preserve an existing team.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent) as draft:
        json.dump(team, draft, ensure_ascii=False, indent=2)
        draft.write("\n")
        draft.flush()
        try:
            os.link(draft.name, path)
        except FileExistsError:
            existing = read_team(path)
            if existing is None:
                raise RuntimeError("The team manifest disappeared during setup. Run creation again.")
            if model is not None and existing["model"] != model:
                raise ValueError("Team already exists with a different model; use --model when running a workflow")
            return {"status": "existing", "manifest": str(path), "team": existing}
    return {"status": "created", "manifest": str(path), "team": team}


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
        role_id = task.get("role")
        if not isinstance(role_id, str) or role_id not in ROLES:
            raise ValueError(f"{task_id}: unknown role {role_id!r}; use --list-roles")
        if not isinstance(task.get("prompt"), str) or not task["prompt"].strip():
            raise ValueError(f"{task_id}: prompt must be a non-empty string")
        if "complexity" in task:
            complexity = task["complexity"]
            if (
                not isinstance(complexity, dict)
                or complexity.get("level") not in ("low", "medium", "high")
                or not isinstance(complexity.get("reason"), str)
                or not complexity["reason"].strip()
            ):
                raise ValueError(f"{task_id}: complexity needs a low, medium or high level and a reason")
        acceptance = task.get("acceptance", [])
        if not isinstance(acceptance, list) or not all(
            isinstance(item, str) and item.strip() for item in acceptance
        ):
            raise ValueError(f"{task_id}: acceptance must be an array of non-empty strings")
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
    role = ROLES[task["role"]]
    parts = [
        f"你是项目团队的{role.name}。你的职责是：{role.responsibility}",
        f"项目目标：{goal}",
        f"当前任务：{task['prompt']}",
    ]
    if "complexity" in task:
        complexity = task["complexity"]
        parts.append(f"任务复杂度：{complexity['level']}。依据：{complexity['reason']}")
    if task.get("acceptance"):
        criteria = "\n".join(f"- {item}" for item in task["acceptance"])
        parts.append(f"验收条件：\n{criteria}")
    for dependency in task.get("depends_on", []):
        parts.append(f"前置任务 {dependency} 的产物：\n{results[dependency]}")
    return "\n\n".join(parts)


def run_codex(workspace, model, role, prompt):
    try:
        result = subprocess.run(
            ["codex", "exec", "--model", model, "--sandbox", role.sandbox, "-"],
            cwd=workspace,
            input=prompt,
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


def run_workflow(goal, tasks, order, run_dir, live, workspace, model):
    results = {}
    for task_id in order:
        task = tasks[task_id]
        role = ROLES[task["role"]]
        prompt = task_prompt(goal, task, results)
        print(f"[{task['role']}] {task_id} ({role.name})", flush=True)
        try:
            result = run_codex(workspace, model, role, prompt) if live else f"离线演示，未执行任务。\n\n{prompt}"
        except RuntimeError as error:
            raise RuntimeError(f"{task_id}: {error}") from error
        result = result.strip()
        if not result:
            raise RuntimeError(f"{task_id} returned no text")
        (run_dir / f"{task_id}.md").write_text(result + "\n", encoding="utf-8")
        results[task_id] = result


def main():
    parser = argparse.ArgumentParser(description="Run a team workflow with Codex.")
    parser.add_argument("workflow", type=Path, nargs="?", help="Workflow JSON file.")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--list-roles", action="store_true", help="List the available team roles.")
    action.add_argument("--create-team", action="store_true", help="Initialize a team and print its JSON manifest.")
    parser.add_argument("--live", action="store_true", help="Execute tasks with Codex CLI.")
    parser.add_argument("--model", help="Default model for a new team, or model for a workflow run.")
    parser.add_argument("--workspace", type=Path, help="Git working directory for team setup or live tasks.")
    parser.add_argument("--output-dir", type=Path, default=Path(".pioneers/runs"))
    args = parser.parse_args()

    if args.list_roles:
        for role_id, role in ROLES.items():
            print(f"{role_id:<9} {role.name} ({role.sandbox})  {role.responsibility}")
        return 0
    if args.create_team and (args.workflow is not None or args.live):
        parser.error("--create-team is a separate action from running a workflow")
    if not args.create_team and args.workflow is None:
        parser.error("provide a workflow JSON file or use --create-team or --list-roles")

    try:
        workspace = args.workspace.resolve() if args.workspace else None
        model = args.model.strip() if args.model is not None else None
        if args.model is not None and not model:
            raise ValueError("--model must be a non-empty model ID")
        if args.create_team:
            result = create_team(team_path(workspace), model)
            print(json.dumps(result, ensure_ascii=False))
            return 0

        goal, tasks, order = load_workflow(args.workflow)
        if args.live:
            path = team_path(workspace)
            if model is None:
                team = read_team(path)
                model = team["model"] if team is not None else None
            if not model:
                raise ValueError("Specify --model or use a team configured with a default model")
        args.output_dir.mkdir(parents=True, exist_ok=True)
        run_dir = Path(tempfile.mkdtemp(prefix="run-", dir=args.output_dir))
        print(f"Mode: {'live' if args.live else 'offline demo'}", flush=True)
        print(f"Run output: {run_dir.resolve()}", flush=True)
        run_workflow(goal, tasks, order, run_dir, args.live, workspace, model)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
