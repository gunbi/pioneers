from datetime import datetime, timezone
import json
import time


STATUS_LABELS = {
    "running": "进行中",
    "completed": "已完成",
    "failed": "失败",
    "interrupted": "已中断",
    "blocked": "已阻塞",
    "planned": "计划已生成",
}
ITEM_LABELS = {
    "command_execution": ("运行命令", "命令结束"),
    "file_change": ("修改文件", "文件修改结束"),
    "mcp_tool_call": ("调用外部工具", "外部工具调用结束"),
    "web_search": ("查找资料", "资料查询结束"),
    "todo_list": ("更新计划", "计划已更新"),
}


def codex_activity(event):
    event_type = event.get("type")
    if event_type == "turn.started":
        return "分析任务"
    if event_type == "turn.failed":
        return "Codex 报告任务失败"
    if event_type not in ("item.started", "item.updated", "item.completed"):
        return None
    item = event.get("item") or {}
    labels = ITEM_LABELS.get(item.get("type"))
    if labels is None:
        return None
    if event_type != "item.completed":
        return labels[0]
    exit_code = item.get("exit_code")
    if item.get("type") == "command_execution" and exit_code is not None:
        return f"命令结束（退出码 {exit_code}）"
    return labels[1]


def print_board(data):
    print(f"团队状态：{STATUS_LABELS[data['state']]} · {data['updated_at']}", flush=True)
    planning = data.get("planning")
    if planning:
        print(f"  团队负责人 / 分工规划：{STATUS_LABELS[planning['state']]} · {planning['activity']}", flush=True)
    tasks = data["tasks"]
    if planning and not tasks:
        return
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
                activity = f"等待 {', '.join(waiting)}" if waiting else "等待执行"
            else:
                activity = f"{STATUS_LABELS[task['state']]} · {task['activity']}"
            print(f"  {role_name} / {task['id']}：{activity}", flush=True)


class Progress:
    def __init__(self, run_dir, tasks, order, roles, live):
        self.path = run_dir / "status.json"
        self.data = {
            "state": "running",
            "mode": "live" if live else "offline",
            "roles": {role_id: role.name for role_id, role in roles.items()},
            "tasks": {},
        }
        self.set_tasks(tasks, order)

    def set_tasks(self, tasks, order):
        self.data["tasks"] = {
            task_id: {
                "id": task_id,
                "role": tasks[task_id]["role"],
                "depends_on": tasks[task_id].get("depends_on", []),
                "state": "pending",
                "activity": "等待执行",
            }
            for task_id in order
        }
        self.save()

    def planning(self, state, activity):
        current = {"state": state, "activity": activity}
        if self.data.get("planning") != current:
            self.data["planning"] = current
            self.save()
            print(f"[团队负责人] 分工规划 · {STATUS_LABELS[state]} · {activity}", flush=True)

    def save(self):
        self.data["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        draft = self.path.with_suffix(".tmp")
        draft.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        draft.replace(self.path)

    def task(self, task_id, state, activity):
        task = self.data["tasks"][task_id]
        if task["state"] == state and task["activity"] == activity:
            return
        task.update(state=state, activity=activity)
        self.save()
        role_name = self.data["roles"][task["role"]]
        print(f"[{role_name}] {task_id} · {STATUS_LABELS[state]} · {activity}", flush=True)

    def finish(self, state):
        self.data["state"] = state
        for task in self.data["tasks"].values():
            if task["state"] == "pending" and state in ("failed", "interrupted"):
                task.update(state="blocked", activity="本次运行已停止")
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
            if not watch or data["state"] != "running":
                return
            print(flush=True)
            previous = snapshot
        time.sleep(0.5)
