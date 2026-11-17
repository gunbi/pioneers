from graphlib import TopologicalSorter


def waiting_on(data, task):
    return [dep for dep in task.get("depends_on", []) if data["tasks"][dep]["state"] != "completed"]


def preapproval_required(task):
    return bool(task.get("effect") or task.get("impact") == "costly_change")


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
                "scope": tasks[key].get("prompt", "")}
        if job["state"] in ("running", "judging"):
            active.append({**item, "member": job.get("member"), "decision": job.get("decision")})
        elif job["state"] in ("pending", "awaiting_confirmation", "blocked"):
            blockers = claim_blockers(data, tasks, key)
            if blockers:
                waiting.append({**item, "blockers": blockers})
            else:
                ready.append({**item, "remaining_steps": remaining[key]})
    ready.sort(key=lambda item: -item["remaining_steps"])
    summary = {"run_state": data["state"], "paused": data.get("paused"),
               "ready": ready, "waiting": waiting, "active": active}
    if plan := pending_replan(data):
        summary["replan"] = {**plan, "ready": replan_ready(data)}
    return summary
