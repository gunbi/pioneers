from graphlib import TopologicalSorter


def waiting_on(data, task):
    return [dep for dep in task.get("depends_on", []) if data["tasks"][dep]["state"] != "completed"]


def design_only(task):
    return not task.get("effect") and task.get("impact") in ("architecture", "feature_design")


def writes_workspace(task):
    return task["role"] in ("engineer", "backend", "frontend", "ios", "android") and not design_only(task)


def path_is_within(path, scope):
    return scope == "." or path == scope or path.startswith(scope + "/")


def write_conflicts(data, tasks, task_id):
    task = tasks[task_id]
    if not writes_workspace(task):
        return []
    isolated = bool(data.get("git_base"))
    scopes = [path.casefold() for path in task.get("write_paths") or ["."]]
    blockers = []
    for key, job in data["tasks"].items():
        holding_scope = job["state"] in ("running", "judging", "recovery_required") or (
            job["state"] == "awaiting_confirmation" and job.get("evidence")
        )
        if (key == task_id or not holding_scope or not writes_workspace(tasks[key])
                or (data["mode"] != "host" and job["state"] != "recovery_required")):
            continue
        other = [path.casefold() for path in tasks[key].get("write_paths") or ["."]]
        if not isolated or any(path_is_within(a, b) or path_is_within(b, a) for a in scopes for b in other):
            reason = "overlapping write paths" if isolated else "the shared workspace"
            blockers.append({"code": "workspace_conflict", "task": key, "state": job["state"],
                             "decision": job.get("decision"), "message": f"Task {task_id} is waiting for {key}: {reason}"})
    return blockers


def preapproval_required(task):
    return bool(task.get("effect") or task.get("impact") == "costly_change")


def can_adopt_merged(data, tasks, task_id):
    task = tasks[task_id]
    job = data["tasks"][task_id]
    return (data["mode"] == "host" and data["state"] == "running" and bool(data.get("git_base"))
            and job["state"] in ("pending", "recovery_required")
            and writes_workspace(task) and not task.get("review") and not preapproval_required(task)
            and not job.get("authorization") and not waiting_on(data, task)
            and not write_conflicts(data, tasks, task_id)
            and not any(operation["kind"] == "external"
                        for execution in job.get("execution", {}).values()
                        for operation in execution.get("operations", {}).values()))


def pending_replan(data):
    plan = data.get("replan")
    return plan if plan and plan["state"] == "pending" else None


def replan_ready(data):
    return (data["state"] == "running" and pending_replan(data) is not None and not data.get("paused")
            and not any(job["state"] == "running" for job in data["tasks"].values()))


def workflow_path(run_dir, data):
    revision = data.get("workflow_revision")
    return run_dir / (f"workflow.{revision}.json" if revision else "workflow.json")


def claim_blockers(data, workflow_tasks, task_id):
    job = data["tasks"][task_id]
    task = workflow_tasks[task_id]
    if job["state"] == "recovery_required":
        return [{"code": "recovery_required", "message": job["activity"]}]
    if data.get("paused"):
        return [{"code": "paused", "message": "Run is paused"}]
    if pending_replan(data):
        return [{"code": "replan", "message": "Waiting for LD to update the workflow"}]
    if data["state"] != "running":
        return [{"code": "run_state", "state": data["state"],
                 "message": f"Task {task_id} cannot be claimed in its current state"}]
    unavailable = {"code": "task_state", "state": job["state"], "decision": job.get("decision"),
                   "message": f"Task {task_id} cannot be claimed in its current state"}
    if job["state"] not in ("pending", "awaiting_confirmation"):
        return [unavailable]
    blockers = []
    for dep in waiting_on(data, task):
        upstream = data["tasks"][dep]
        blockers.append({"code": "dependency", "task": dep, "state": upstream["state"],
                         "decision": upstream.get("decision") or upstream.get("authorization"),
                         "message": f"Task {task_id} is waiting for: {dep}"})
    authorization = job.get("authorization")
    if authorization or preapproval_required(task):
        decision = data.get("decisions", {}).get(authorization, {})
        if decision.get("state") != "approved":
            blockers.append({"code": "authorization", "decision": authorization,
                             "message": f"Task {task_id} requires human authorization before execution"})
    if job["state"] != "pending" and not blockers:
        blockers.append(unavailable)
    if not blockers:
        blockers.extend(write_conflicts(data, workflow_tasks, task_id))
    return blockers


def dispatch_summary(data, tasks):
    order = tuple(TopologicalSorter({key: task.get("depends_on", []) for key, task in tasks.items()}).static_order())
    remaining = dict.fromkeys(order, 1)
    for key in reversed(order):
        if data["tasks"][key]["state"] == "completed":
            continue
        for dependency in tasks[key].get("depends_on", []):
            remaining[dependency] = max(remaining[dependency], remaining[key] + 1)
    ready, waiting, active = [], [], []
    for key in order:
        job = data["tasks"][key]
        item = {"task": key, "role": job["role"], "state": job["state"], "attempt": job.get("attempt", 1),
                "scope": tasks[key].get("prompt", ""), "write_paths": tasks[key].get("write_paths", [])}
        if job["state"] in ("running", "judging"):
            active.append({**item, "member": job.get("member"), "decision": job.get("decision"),
                           "branch": job.get("git", {}).get("branch"), "lease_expires_at": job.get("lease_expires_at")})
        elif job["state"] in ("pending", "awaiting_confirmation", "blocked", "recovery_required"):
            if can_adopt_merged(data, tasks, key):
                item["can_adopt_merged"] = True
            blockers = claim_blockers(data, tasks, key)
            if job["state"] == "recovery_required":
                item.update(branch=job.get("git", {}).get("branch"), member=job.get("member"))
            if blockers:
                waiting.append({**item, "blockers": blockers})
            else:
                if job.get("continuity_from"):
                    item["preferred_member"] = job["recoveries"][-1].get("member")
                ready.append({**item, "remaining_steps": remaining[key]})
    ready.sort(key=lambda item: -item["remaining_steps"])
    summary = {"run_state": data["state"], "paused": data.get("paused"),
               "ready": ready, "waiting": waiting, "active": active}
    if data["mode"] == "host":
        summary["worktrees"] = bool(data.get("git_base"))
    if plan := pending_replan(data):
        summary["replan"] = {**plan, "ready": replan_ready(data)}
    if release := data.get("release"):
        decision = data.get("decisions", {}).get(release.get("decision"), {})
        summary["release_ready"] = (not data.get("paused") and not pending_replan(data)
                                    and release["state"] == "accepted" and decision.get("state") == "approved")
    return summary


def dispatch_can_continue(summary):
    state = summary["run_state"]
    if state in ("published", "blocked", "failed", "interrupted") or (state == "completed" and not summary["paused"]):
        return True
    if summary["paused"]:
        return False
    return bool(summary["ready"] or summary.get("replan", {}).get("ready") or summary.get("release_ready")
                or any(item["state"] == "judging" for item in summary["active"]))
