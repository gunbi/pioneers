import argparse
from dataclasses import asdict, dataclass
from graphlib import CycleError, TopologicalSorter
import json
import os
from pathlib import Path
from queue import Empty, Queue
import re
import subprocess
import sys
import tempfile
from threading import Thread
import time
from typing import Optional

from capabilities import ROOT, assigned_capabilities, capability_prompt, run_tool
from progress import Progress, codex_activity, print_board, show_progress


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
    return validate_workflow(json.loads(path.read_text(encoding="utf-8")))


def validate_workflow(workflow, generated=False):
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
        if generated and (not task.get("acceptance") or "complexity" not in task):
            raise ValueError(f"{task_id}: planned tasks need complexity and acceptance criteria")
        skills = task.get("skills", [])
        allowed_skills = assigned_capabilities(role_id)["skills"]
        if not isinstance(skills, list) or not all(
            isinstance(skill, str) and skill in allowed_skills for skill in skills
        ):
            raise ValueError(f"{task_id}: skills must be an array of skill IDs assigned to {role_id}")
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
    parts.extend(capability_prompt(task["role"], task.get("skills", [])))
    for dependency in task.get("depends_on", []):
        parts.append(f"前置任务 {dependency} 的产物：\n{results[dependency]}")
    return "\n\n".join(parts)


def queue_lines(stream, lines):
    try:
        for line in stream:
            lines.put(line)
    finally:
        lines.put(None)


@dataclass(frozen=True)
class Executor:
    workspace: Path
    model: Optional[str]
    runner: Optional[Path] = None

    def run(self, role_id, prompt, report, schema=None):
        role = ROLES[role_id]
        if self.runner is not None:
            request = {
                "phase": "plan" if schema else "task",
                "role": role_id,
                "workspace": str(self.workspace),
                "model": self.model,
                "sandbox": role.sandbox,
                "prompt": prompt,
                "output_schema": json.loads(schema.read_text(encoding="utf-8")) if schema else None,
            }
            return run_agent_process(
                [str(self.runner)], self.workspace, json.dumps(request, ensure_ascii=False),
                report, external=True,
            )
        command = ["codex", "exec", "--json", "--model", self.model, "--sandbox", role.sandbox]
        if schema:
            command.extend(["--output-schema", str(schema)])
        return run_agent_process([*command, "-"], self.workspace, prompt, report)


def run_agent_process(command, workspace, payload, report, external=False):
    lines = Queue()
    last_message = ""
    turn_failed = False
    with tempfile.TemporaryFile() as input_file, tempfile.TemporaryFile() as errors:
        input_file.write(payload.encode("utf-8"))
        input_file.seek(0)
        try:
            process = subprocess.Popen(
                command,
                cwd=workspace,
                stdin=input_file,
                stdout=subprocess.PIPE,
                stderr=errors,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError as error:
            raise RuntimeError(f"Executor not found: {command[0]}") from error

        with process:
            Thread(target=queue_lines, args=(process.stdout, lines), daemon=True).start()
            deadline = time.monotonic() + 600
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise Empty
                    line = lines.get(timeout=remaining)
                    if line is None:
                        break
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if external:
                        message = event.get("message")
                        if event.get("type") == "activity" and isinstance(message, str):
                            report(message)
                        if event.get("type") == "result" and isinstance(message, str):
                            last_message = message
                        continue
                    activity = codex_activity(event)
                    if activity:
                        report(activity)
                    if event.get("type") == "turn.failed":
                        turn_failed = True
                    if event.get("type") == "item.completed":
                        item = event.get("item") or {}
                        if item.get("type") == "agent_message":
                            last_message = item.get("text") or ""
                code = process.wait(timeout=max(0, deadline - time.monotonic()))
            except (Empty, subprocess.TimeoutExpired) as error:
                raise RuntimeError("Executor exceeded the 600-second task timeout") from error
            finally:
                if process.poll() is None:
                    process.kill()

        if code:
            errors.seek(0)
            detail = errors.read().decode("utf-8", errors="replace").strip().splitlines()
            suffix = f": {detail[-1]}" if detail else ""
            raise RuntimeError(f"Executor exited with status {code}{suffix}")
        if turn_failed:
            raise RuntimeError("Codex reported a failed turn")
    return last_message


def plan_workflow(goal, executor, run_dir, progress):
    schema = ROOT / "workflow.schema.json"
    roles = "\n".join(
        f"- {role_id} {role.name}：{role.responsibility} "
        f"（可用 Skill：{', '.join(assigned_capabilities(role_id)['skills']) or '无'}）"
        for role_id, role in ROLES.items()
    )
    prompt = (
        "你是 pioneers 的 LD。根据目标自主选择角色、拆分任务和设置依赖。"
        "可以查阅当前项目，规划阶段仅做只读分析。"
        "只启用完成目标所需的角色，任务之间用 depends_on 约定交接。"
        "每项任务列出复杂度、理由和可验证的验收条件，只指定该角色可用且必要的 Skill。"
        "只返回符合指定结构的工作流 JSON。\n\n"
        f"用户目标：{goal}\n\n可用角色：\n{roles}"
    )
    progress.planning("running", "正在选择角色和任务依赖")
    try:
        raw = executor.run("ld", prompt, lambda text: progress.planning("running", text), schema)
        workflow = json.loads(raw)
        if isinstance(workflow, dict):
            workflow["goal"] = goal
        result = validate_workflow(workflow, generated=True)
        path = run_dir / "workflow.json"
        path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except KeyboardInterrupt:
        progress.planning("interrupted", "规划被用户中断")
        progress.finish("interrupted")
        raise
    except (OSError, RuntimeError, ValueError) as error:
        progress.planning("failed", str(error))
        progress.finish("failed")
        raise RuntimeError(f"Planning failed: {error}") from error

    _, tasks, order = result
    progress.set_tasks(tasks, order)
    progress.planning("completed", f"分工已保存到 {path.name}")
    return result


def run_workflow(goal, tasks, order, run_dir, executor, progress):
    results = {}
    for task_id in order:
        task = tasks[task_id]
        prompt = task_prompt(goal, task, results)
        progress.task(task_id, "running", "启动角色执行" if executor else "生成离线交接预览")
        try:
            if executor:
                result = executor.run(task["role"], prompt,
                                      lambda activity: progress.task(task_id, "running", activity))
            else:
                result = f"离线演示，未执行任务。\n\n{prompt}"
            result = result.strip()
            if not result:
                raise RuntimeError("Executor returned no final message")
            artifact = run_dir / f"{task_id}.md"
            artifact.write_text(result + "\n", encoding="utf-8")
        except KeyboardInterrupt:
            progress.task(task_id, "interrupted", "运行被用户中断")
            progress.finish("interrupted")
            raise
        except (OSError, RuntimeError) as error:
            progress.task(task_id, "failed", str(error))
            progress.finish("failed")
            raise RuntimeError(f"{task_id}: {error}") from error
        results[task_id] = result
        activity = "产物已保存" if executor else "离线预览已保存"
        progress.task(task_id, "completed", f"{activity} {artifact.name}")
    progress.finish("completed")


def main():
    parser = argparse.ArgumentParser(description="Plan and run a pioneers team workflow.")
    parser.add_argument("workflow", type=Path, nargs="?", help="Workflow JSON file.")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--list-roles", action="store_true", help="List the available team roles.")
    action.add_argument("--create-team", action="store_true", help="Initialize a team and print its JSON manifest.")
    action.add_argument("--status", type=Path, metavar="RUN_DIR", help="Show the latest team status.")
    action.add_argument("--watch", type=Path, metavar="RUN_DIR", help="Follow team status until the run stops.")
    action.add_argument("--list-capabilities", metavar="ROLE", help="List the tools and skills available to a role.")
    action.add_argument("--run-tool", metavar="TOOL", help="Run a built-in check and print its JSON result.")
    action.add_argument("--goal", help="Ask an LLM to assign roles and build a workflow for this goal.")
    parser.add_argument("--plan-only", action="store_true", help="Save the workflow without running its tasks.")
    parser.add_argument("--runner", type=Path, help="Executable adapter for a non-Codex platform.")
    parser.add_argument("--role", choices=ROLES, help="Role invoking a built-in tool.")
    parser.add_argument("--input", type=Path, help="Input file for a built-in tool, relative to the workspace.")
    parser.add_argument("--live", action="store_true", help="Use the configured agent executor.")
    parser.add_argument("--model", help="Default model for a new team, or model for a workflow run.")
    parser.add_argument("--workspace", type=Path, help="Git working directory for team setup or live tasks.")
    parser.add_argument("--output-dir", type=Path, default=Path(".pioneers/runs"))
    args = parser.parse_args()

    if args.list_roles:
        for role_id, role in ROLES.items():
            print(f"{role_id:<9} {role.name} ({role.sandbox})  {role.responsibility}")
        return 0
    viewing = args.status is not None or args.watch is not None
    tool_action = args.list_capabilities is not None or args.run_tool is not None
    if (args.create_team or viewing or tool_action) and (args.workflow is not None or args.live):
        parser.error("team setup, status and tools are separate actions from running a workflow")
    if args.goal is not None and (args.workflow is not None or not args.live or not args.goal.strip()):
        parser.error("--goal needs a non-empty goal and --live, without a workflow file")
    if args.plan_only and args.goal is None:
        parser.error("--plan-only requires --goal")
    if args.runner is not None and not args.live:
        parser.error("--runner requires --live")
    if not (args.create_team or viewing or tool_action or args.goal is not None or args.workflow is not None):
        parser.error("provide --goal, a workflow JSON file or a team action")

    try:
        if viewing:
            show_progress(args.status if args.watch is None else args.watch, watch=args.watch is not None)
            return 0
        workspace = args.workspace.resolve() if args.workspace else None
        if args.list_capabilities is not None:
            if args.list_capabilities not in ROLES:
                raise ValueError(f"Unknown role: {args.list_capabilities}")
            print(json.dumps(assigned_capabilities(args.list_capabilities), ensure_ascii=False, indent=2))
            return 0
        if args.run_tool is not None:
            if args.role is None:
                raise ValueError("--run-tool requires --role")
            result = run_tool(args.run_tool, args.role, workspace, args.input)
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result["ok"] else 1
        model = args.model.strip() if args.model is not None else None
        if args.model is not None and not model:
            raise ValueError("--model must be a non-empty model ID")
        if args.create_team:
            result = create_team(team_path(workspace), model)
            print(json.dumps(result, ensure_ascii=False))
            return 0

        if args.goal is None:
            goal, tasks, order = load_workflow(args.workflow)
        else:
            goal, tasks, order = args.goal.strip(), {}, ()
        executor = None
        if args.live:
            path = team_path(workspace)
            runner = args.runner.resolve() if args.runner is not None else None
            if runner is not None and (not runner.is_file() or not os.access(runner, os.X_OK)):
                raise ValueError("--runner must point to an executable file")
            if model is None:
                team = read_team(path)
                model = team["model"] if team is not None else None
            if not model and runner is None:
                raise ValueError("Specify --model or use a team configured with a default model")
            executor = Executor(workspace, model, runner)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        run_dir = Path(tempfile.mkdtemp(prefix="run-", dir=args.output_dir))
        progress = Progress(run_dir, tasks, order, ROLES, args.live)
        print(f"Mode: {'live' if args.live else 'offline demo'}", flush=True)
        print(f"Run output: {run_dir.resolve()}", flush=True)
        if args.goal is not None:
            goal, tasks, order = plan_workflow(goal, executor, run_dir, progress)
            if args.plan_only:
                progress.finish("planned")
                return 0
        print_board(progress.data)
        run_workflow(goal, tasks, order, run_dir, executor, progress)
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
