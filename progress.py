from datetime import datetime, timezone
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import shlex
import sqlite3
import tempfile
import time

from iterations import DELIVERED, build_summary


STATUS_LABELS = {
    "running": "进行中",
    "recovery_required": "待人工恢复",
    "completed": "已完成",
    "failed": "失败",
    "interrupted": "已中断",
    "blocked": "已阻塞",
    "planned": "计划已生成",
    "pending": "待执行",
    "returned": "已退回",
    "accepted": "验收通过",
    "publishing": "发布中",
    "published": "已发布",
    "publish_failed": "发布失败",
    "judging": "等待 LLM 判断",
    "awaiting_confirmation": "待人工确认",
    "paused": "已暂停",
}
ITEM_LABELS = {
    "command_execution": ("运行命令", "命令结束"),
    "file_change": ("修改文件", "文件修改结束"),
    "mcp_tool_call": ("调用外部工具", "外部工具调用结束"),
    "web_search": ("查找资料", "资料查询结束"),
    "todo_list": ("更新计划", "计划已更新"),
}
COMMAND_ACTIONS = {
    "git": {"diff", "status", "log"},
    "npm": {"test", "run", "build"},
    "pnpm": {"test", "run", "build"},
    "cargo": {"check", "test", "build"},
    "go": {"test", "build"},
}
VISIBLE_COMMANDS = {*COMMAND_ACTIONS, "rg", "ls", "cat", "sed", "python", "python3", "pytest", "node"}

def artifact_name(task_id, attempt, extension):
    numbered = attempt > 1 or (extension == "json" and task_id in ("status", "workflow"))
    stem = f"{task_id}.{attempt}" if numbered else task_id
    return f"{stem}.{extension}"


def codex_activity(event):
    event_type = event.get("type")
    if event_type == "turn.started":
        return "分析任务"
    if event_type == "turn.failed":
        return "Codex 报告任务失败"
    if event_type not in ("item.started", "item.updated", "item.completed"):
        return None
    item = event.get("item")
    if not isinstance(item, dict):
        return None
    kind = item.get("type")
    labels = ITEM_LABELS.get(kind)
    if labels is None:
        return None
    completed = event_type == "item.completed"
    activity = labels[completed]
    if kind == "file_change":
        paths = [change["path"][:200] for change in (item.get("changes") or [])[:5]
                 if isinstance(change, dict) and isinstance(change.get("path"), str)]
        if paths:
            activity += f" · {', '.join(paths)}"
    if kind == "command_execution":
        # Codex may wrap commands in a shell. Show known command names only.
        try:
            words = shlex.split(item.get("command") or "")
            if len(words) == 3 and words[1] in ("-c", "-lc"):
                words = shlex.split(words[2])
        except ValueError:
            words = []
        if words and words[0] in VISIBLE_COMMANDS:
            activity += f" · {words[0]}"
            if len(words) > 1 and words[1] in COMMAND_ACTIONS.get(words[0], ()):
                activity += f" {words[1]}"
    exit_code = item.get("exit_code")
    if kind == "command_execution" and completed and isinstance(exit_code, int):
        activity += f"（退出码 {exit_code}）"
    return activity


def print_board(data):
    print(f"团队状态：{STATUS_LABELS[data['state']]} · {data['updated_at']}", flush=True)
    if data.get("paused"):
        print(f"  已暂停：{data['paused']}", flush=True)
    for decision in data.get("decisions", {}).values():
        if decision["state"] in ("judging", "waiting"):
            print(f"  {decision['id']} · {decision['task'] or '发布'} · {decision['reason']}", flush=True)
    planning = data.get("planning")
    if planning:
        print(f"  {data['roles']['ld']} / 分工规划：{STATUS_LABELS[planning['state']]} · {planning['activity']}", flush=True)
    tasks = data["tasks"]
    if planning and not tasks:
        return
    release = data.get("release")
    if release:
        print(f"  发布：{STATUS_LABELS[release['state']]} · {release['activity']}", flush=True)
        if release.get("url"):
            print(f"  {release['url']}", flush=True)
    for role_id, role_name in data["roles"].items():
        assigned = [task for task in tasks.values() if task["role"] == role_id]
        if not assigned:
            print(f"  {role_name}：未分配", flush=True)
        for task in assigned:
            if task["state"] == "pending":
                waiting = [
                    dependency for dependency in task["depends_on"]
                    if tasks[dependency]["state"] != "completed"
                ]
                activity = f"等待 {', '.join(waiting)}" if waiting else task["activity"]
            else:
                activity = f"{STATUS_LABELS[task['state']]} · {task['activity']}"
                if task.get("evidence"):
                    activity += f" · 证据：{task['evidence']}"
            attempt = f"（第 {task['attempt']} 轮）" if task.get("attempt", 1) > 1 else ""
            print(f"  {role_name} / {task['id']}{attempt}：{activity}", flush=True)


class Progress:
    def __init__(self, run_dir, tasks, order, roles, mode, goal, require_release=False):
        self.path = run_dir / "status.json"
        self.data = {
            "state": "running",
            "mode": mode,
            "goal": goal,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            "roles": {role_id: role.name for role_id, role in roles.items()},
            "tasks": {},
            "events": [],
        }
        if require_release:
            self.data["release"] = {"state": "pending", "activity": "等待任务完成和 QA 验收"}
        self.set_tasks(tasks, order)

    @classmethod
    def open(cls, run_dir):
        progress = cls.__new__(cls)
        progress.path = run_dir / "status.json"
        progress.data = json.loads(progress.path.read_text(encoding="utf-8"))
        return progress

    @classmethod
    @contextmanager
    def locked(cls, run_dir):
        with sqlite3.connect(run_dir / "host.lock.db") as lock:
            lock.execute("BEGIN IMMEDIATE")
            progress = cls.open(run_dir)
            before = deepcopy(progress.data)
            progress.deferred = True
            yield progress
            progress.deferred = False
            if progress.data != before:
                progress.save()

    def set_tasks(self, tasks, order, *, preserve=False):
        records = {
            task_id: {
                "id": task_id,
                "role": tasks[task_id]["role"],
                "prompt": tasks[task_id]["prompt"],
                "planned_skills": list(dict.fromkeys(tasks[task_id].get("skills", []))),
                "depends_on": tasks[task_id].get("depends_on", []),
                "state": "pending",
                "activity": "等待执行",
                "attempt": 1,
            }
            for task_id in order
        }
        if preserve:
            self.data["tasks"].update(records)
        else:
            self.data["tasks"] = records
        self.save()

    def planning(self, state, activity):
        current = {"state": state, "activity": activity}
        if self.data.get("planning") != current:
            self.data["planning"] = current
            self.event("ld", None, state, activity)
            print(f"[{self.data['roles']['ld']}] 分工规划 · {STATUS_LABELS[state]} · {activity}", flush=True)

    def save(self):
        if getattr(self, "deferred", False):
            return
        self.data["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        if self.data["state"] in DELIVERED and self.data["mode"] != "offline":
            self.data["summary"] = build_summary(self.data)
        else:
            self.data.pop("summary", None)
        with tempfile.TemporaryDirectory(dir=self.path.parent) as staging:
            draft = Path(staging) / self.path.name
            draft.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            draft.replace(self.path)

    def event(self, role, task, state, activity, *, evidence=None, handoff=None, persist=True):
        events = self.data["events"]
        record = {"role": role, "task": task, "state": state, "activity": activity,
                  "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        if evidence:
            record["evidence"] = evidence
        if handoff:
            record["handoff"] = handoff
        if task:
            job = self.data["tasks"][task]
            record["attempt"] = job.get("attempt", 1)
            if job.get("member"):
                record["member"] = job["member"]
        events.append(record)
        events[:-100] = [event for event in events[:-100] if event.get("evidence") or event["state"] in ("interrupted", "recovery_required")]
        if persist:
            self.save()

    def task(self, task_id, state, activity, *, evidence=None, handoff=None, announce=True, persist=True):
        task = self.data["tasks"][task_id]
        if task["state"] == state and task["activity"] == activity and task.get("evidence") == evidence:
            return
        task.update(state=state, activity=activity)
        if state != "running":
            task.pop("lease_expires_at", None)
        if evidence:
            task["evidence"] = evidence
        else:
            task.pop("evidence", None)
        self.event(task["role"], task_id, state, activity, evidence=evidence, handoff=handoff, persist=persist)
        if announce:
            role_name = self.data["roles"][task["role"]]
            detail = f"{activity} · 证据：{evidence}" if evidence else activity
            print(f"[{role_name}] {task_id} · {STATUS_LABELS[state]} · {detail}", flush=True)

    def set_outcome(self, state):
        if state in DELIVERED and self.data["state"] != state:
            self.data["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        elif state not in DELIVERED:
            self.data.pop("finished_at", None)
        self.data["state"] = state
        for task in self.data["tasks"].values():
            if task["state"] == "pending" and state in ("failed", "interrupted", "blocked"):
                task.update(state="blocked", activity="本次运行已停止")

    def finish_tasks(self):
        if "release" not in self.data:
            self.finish("completed")
            return
        self.accept_release()
        self.event("ld", None, "accepted", self.data["release"]["activity"])
        print_board(self.data)

    def accept_release(self):
        self.set_outcome("accepted")
        self.data["release"] = {"state": "accepted", "activity": "独立验收通过，等待发布"}

    def finish(self, state):
        self.set_outcome(state)
        self.save()
        print_board(self.data)


def show_progress(run_dir, watch):
    path = run_dir / "status.json"
    previous = None
    while True:
        snapshot = path.read_text(encoding="utf-8")
        if snapshot != previous:
            data = json.loads(snapshot)
            print_board(data)
            waiting = data["state"] in ("running", "accepted", "publishing") or (
                data["state"] == "completed" and data.get("paused")
            )
            if not watch or not waiting:
                return
            print(flush=True)
            previous = snapshot
        time.sleep(0.5)
