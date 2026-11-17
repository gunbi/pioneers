from datetime import datetime, timezone
import json


EFFECTS = {
    "send": "对外发送",
    "publish": "对外发布",
    "payment": "付款",
    "permissions": "权限变更",
    "delete": "删除",
    "irreversible": "其他难以撤销的操作",
}
IMPACTS = {
    "architecture": "重大架构方案",
    "feature_design": "重要功能设计",
    "costly_change": "高成本改动",
}


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def audit(progress, action, reason, **details):
    progress.data.setdefault("operator_log", []).append(
        {"action": action, "reason": reason, "at": timestamp(), **details}
    )


def proposal(progress, kind, task, content, *, evidence=None, effect=None, impact=None, note=None):
    decisions = progress.data.setdefault("decisions", {})
    key = f"D{len(decisions) + 1}"
    job = progress.data["tasks"].get(task, {})
    entry = {
        "id": key, "kind": kind, "task": task, "attempt": job.get("attempt", 1),
        "content": content, "evidence": evidence, "effect": effect, "impact": impact, "at": timestamp(),
        "state": "judging" if kind == "result" else "waiting",
        "reason": "等待 LLM 评估是否需要人工确认" if kind == "result"
                  else f"{EFFECTS.get(effect) or IMPACTS[impact]}须由人工确认",
    }
    if note:
        entry["note"] = note
    decisions[key] = entry
    return entry


def supersede(progress, key):
    entry = progress.data.get("decisions", {}).get(key)
    if entry and entry["state"] != "superseded":
        entry["previous_state"] = entry["state"]
        entry["state"] = "superseded"


def judgment(text):
    value = json.loads(text)
    if (not isinstance(value, dict) or set(value) - {"needs_confirmation", "reason", "impact", "effect"}
            or type(value.get("needs_confirmation")) is not bool
            or not isinstance(value.get("reason"), str) or not value["reason"].strip()):
        raise ValueError("LLM judgment needs needs_confirmation (boolean) and a non-empty reason")
    impact = value.get("impact")
    if impact is not None and (not isinstance(impact, str) or impact not in IMPACTS):
        raise ValueError("Unknown judgment impact")
    effect = value.get("effect")
    if effect is not None and (not isinstance(effect, str) or effect not in EFFECTS):
        raise ValueError("Unknown judgment effect")
    return value["needs_confirmation"], value["reason"].strip(), impact, effect
