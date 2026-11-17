from collections import Counter
from datetime import datetime
from pathlib import Path
import re

from skill_sources import read_json, write_json


RUN_ID = re.compile(r"run-[a-z0-9_-]+")
DELIVERED = {"completed", "published"}
ACTIVE = {"running", "accepted", "publishing", "publish_failed"}


def register_run(registry, run_dir):
    if not RUN_ID.fullmatch(run_dir.name):
        raise ValueError(f"Invalid run ID: {run_dir.name}")
    registry.mkdir(parents=True, exist_ok=True)
    path = registry / f"{run_dir.name}.json"
    previous = read_json(path)
    if previous and previous.get("path") != str(run_dir):
        raise ValueError(f"A different run is already registered as {run_dir.name}")
    if not previous:
        write_json(path, {"path": str(run_dir)})


def registered_run(registry, run_id):
    if not RUN_ID.fullmatch(run_id):
        return None
    value = read_json(registry / f"{run_id}.json").get("path")
    if isinstance(value, str):
        path = Path(value)
        if path.is_absolute() and path.name == run_id and (path / "status.json").is_file():
            return path
    return None


def build_summary(data):
    release = data.get("release", {})
    decisions = data.get("decisions", {})
    approval_reasons = {event.get("decision"): event["reason"] for event in data.get("operator_log", [])
                        if event["action"] == "approve"}
    results = []
    for task in data["tasks"].values():
        decision = decisions.get(task.get("decision"), {})
        results.append({
            "task": task["id"], "role": data["roles"].get(task["role"], task["role"]),
            "state": task["state"], "attempt": task.get("attempt", 1),
            "result": decision.get("content") or task["activity"],
            "evidence": task.get("evidence"), "git": task.get("git"),
        })
    return {
        "goal": data["goal"], "state": data["state"],
        "finished_at": data.get("finished_at") or data.get("updated_at"),
        "results": results,
        "approvals": [{"id": item["id"], "task": item.get("task"), "reason": item["reason"],
                       "by": item.get("approved_by"), "operator_reason": approval_reasons.get(item["id"])}
                      for item in decisions.values() if item["state"] == "approved"],
        "release": {key: release[key] for key in ("state", "activity", "url") if key in release},
    }


def iteration_summary(data):
    if data["mode"] in ("host", "live") and data["state"] in DELIVERED:
        return data.get("summary") or build_summary(data)
    return None


def project_snapshot(registry, project_name):
    runs = []
    pending = []
    for entry in registry.glob("run-*.json"):
        run_dir = registered_run(registry, entry.stem)
        if run_dir is None:
            continue
        data = read_json(run_dir / "status.json")
        if data.get("mode") not in ("host", "live") or not isinstance(data.get("tasks"), dict):
            continue
        state = data["state"]
        active = state in ACTIVE or bool(data.get("paused") and state == "completed")
        runs.append({
            "id": entry.stem, "goal": data["goal"], "state": state, "active": active,
            "paused": bool(data.get("paused")),
            "started_at": data.get("started_at") or next((event["at"] for event in data.get("events", [])), None),
            "updated_at": data.get("updated_at"),
            "counts": Counter(task["state"] for task in data["tasks"].values()),
            "has_summary": state in DELIVERED, "finished_at": data.get("finished_at") or data.get("updated_at"),
        })
        if active:
            pending.extend({"run": entry.stem, "goal": data["goal"], "id": item["id"],
                            "task": item.get("task"), "reason": item["reason"], "at": item["at"]}
                           for item in data.get("decisions", {}).values() if item["state"] == "waiting")
    runs.sort(key=lambda row: (row["started_at"] or row["updated_at"] or "", row["id"]), reverse=True)
    pending.sort(key=lambda item: (item["at"], item["run"], item["id"]))
    return {"project": project_name, "runs": runs, "pending": pending}


def latest_delivery(registry, project_name, snapshot=None):
    if snapshot is None:
        snapshot = project_snapshot(registry, project_name)
    delivered = [row for row in snapshot["runs"] if row["has_summary"]]

    def delivery_time(row):
        value = (row["finished_at"] or "").replace(" UTC", "+00:00")
        return datetime.fromisoformat(value).timestamp() if value else 0

    for row in sorted(delivered, key=delivery_time, reverse=True):
        run_dir = registered_run(registry, row["id"])
        if run_dir is not None:
            data = read_json(run_dir / "status.json")
            if data and (summary := iteration_summary(data)):
                return {"run": row["id"], "run_dir": str(run_dir), "summary": summary}
    return None
