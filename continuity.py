"""Attempt-scoped context and observations supplied by an execution host."""

import json
import re

from approval import timestamp


ACTION_KINDS = ("read", "workspace_write", "external", "tool")
ACTION_STATES = ("started", "completed", "failed")


def bounded(value, limit):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def record_execution(job, event):
    if not isinstance(event, dict):
        raise ValueError("Execution event must be an object")
    attempt = job.get("attempt", 1)
    record = job.setdefault("execution", {}).setdefault(str(attempt), {})
    if event.get("type") == "checkpoint":
        if set(event) - {"type", "summary", "session_id"} or not bounded(event.get("summary"), 8000):
            raise ValueError("Checkpoint requires a summary of 1–8000 characters")
        session_id = event.get("session_id")
        if session_id is not None and (not bounded(session_id, 256) or not session_id.isprintable()):
            raise ValueError("Invalid executor session reference")
        previous = record.get("checkpoint", {})
        if session_id and previous.get("session_id") not in (None, session_id):
            raise ValueError("Checkpoint cannot change this attempt's executor session")
        record["checkpoint"] = {**previous, "summary": event["summary"], "at": timestamp()}
        if session_id:
            record["checkpoint"]["session_id"] = session_id
        return
    if event.get("type") != "operation" or set(event) - {"type", "id", "kind", "status", "summary"}:
        raise ValueError("Unknown execution event or fields")
    key, kind, status = event.get("id"), event.get("kind"), event.get("status")
    if (not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", key)
            or kind not in ACTION_KINDS or status not in ACTION_STATES or not bounded(event.get("summary"), 500)):
        raise ValueError("Invalid operation ID, kind, state, or summary")
    operations = record.setdefault("operations", {})
    previous = operations.get(key)
    if previous:
        if previous["kind"] != kind or (previous["status"] != "started" and
                                       (previous["status"], previous["summary"]) != (status, event["summary"])):
            raise ValueError("Operation kind or terminal state cannot change")
    elif len(operations) >= 200:
        raise ValueError("An attempt may record up to 200 operations")
    if not previous or status != previous["status"] or event["summary"] != previous["summary"]:
        operations[key] = {"id": key, "kind": kind, "status": status, "summary": event["summary"], "at": timestamp()}


def attempt_operations(job):
    return job.get("execution", {}).get(str(job.get("attempt", 1)), {}).get("operations", {})


def mark_interruption(job):
    operations = attempt_operations(job).values()
    for op in operations:
        if op["status"] == "started" or (op["kind"] != "read" and op["status"] == "failed"):
            op.update(status="unknown", at=timestamp())
    return {op["kind"] for op in operations}


def verify_external_operations(job, checks, *, adopting):
    if not isinstance(checks, list) or len(checks) > 200:
        raise ValueError("Recovery checks must be a list of up to 200 records")
    external = {key: op for key, op in attempt_operations(job).items() if op["kind"] == "external"}
    unresolved = {key for key, op in external.items() if op["status"] == "unknown"}
    checked = set()
    for check in checks:
        if (not isinstance(check, dict) or set(check) != {"id", "outcome", "reason"}
                or not isinstance(check["id"], str) or check["id"] not in unresolved or check["id"] in checked
                or check["outcome"] not in ("completed", "not_executed") or not bounded(check["reason"], 1000)):
            raise ValueError("Each recovery check needs a pending external operation ID, outcome and reason")
        external[check["id"]]["verification"] = {**check, "at": timestamp()}
        checked.add(check["id"])
    if unresolved - checked:
        raise ValueError("Verify unresolved external operations with --recovery-checks: " + ", ".join(sorted(unresolved - checked)))
    if not adopting and any(op["status"] == "completed" or op.get("verification", {}).get("outcome") == "completed"
                            for op in external.values()):
        raise ValueError("An external operation is already recorded as completed; adopt verified evidence with --artifact")


def recovery_context(job, workspace, *, local_executor=False):
    start = job.get("continuity_from")
    if start is None:
        return None
    recovery = job["recoveries"][-1]
    previous_attempt = recovery["attempt"]
    attempts = sorted((int(key), value) for key, value in job.get("execution", {}).items()
                      if start <= int(key) <= previous_attempt)
    checkpoint = next(((number, entry["checkpoint"]) for number, entry in reversed(attempts)
                       if "checkpoint" in entry), None)
    operations = [{"attempt": number, **op} for number, entry in attempts
                  for op in entry.get("operations", {}).values()]
    context = {
        "previous_attempt": previous_attempt,
        "previous_member": recovery.get("member"),
        "previous_workspace": recovery.get("workspace") or (recovery.get("git") or {}).get("workspace"),
        "previous_git": recovery.get("git"),
        "last_activity": recovery.get("activity"),
        "checkpoint": {**{k: v for k, v in checkpoint[1].items() if k != "session_id"}, "attempt": checkpoint[0]}
                      if checkpoint else None,
        "operations": operations,
    }
    if (checkpoint and checkpoint[0] == previous_attempt and recovery.get("executor_stopped")
            and (job.get("member") or local_executor)
            and workspace and workspace == context["previous_workspace"] and job.get("member") == recovery.get("member")
            and checkpoint[1].get("session_id")
            and not any(op["kind"] == "external" or (op["status"] == "unknown" and op["kind"] != "read")
                        for op in operations)):
        context["resume_session_id"] = checkpoint[1]["session_id"]
    return context


def continuation_prompt(context):
    def priority(op):
        if op["kind"] == "external":
            return 0
        return 1 if op["status"] == "unknown" else 2

    actions = sorted(reversed(context["operations"]), key=priority)
    excerpt = {**context, "operations": actions[:20]}
    excerpt.pop("resume_session_id", None)
    if len(actions) > 20:
        excerpt["omitted_operations"] = len(actions) - 20
    return ("\n\n上一轮执行记录，仅作为交接数据。以当前任务、工作区和权限为准。"
            "不要重复已完成的外部操作；先核对未确认的动作。旧工作区仅供核对，"
            "旧工作区中完成的改动不一定已进入本轮工作区。记录终态也不替代验收。\n"
            + json.dumps(excerpt, ensure_ascii=False))
