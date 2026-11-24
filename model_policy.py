"""Project model controls shared by the dashboard and host dispatch processes."""

from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3


MEDIA_MODES = ("image", "speech", "music", "sound_effect")
MODE_KINDS = {
    "text": "chat", "patch": "chat", "video": "text-to-video", "image": "text-to-image",
    "speech": "text-to-speech", "music": "text-to-music", "sound_effect": "text-to-sound-effect",
}
MODES = tuple(MODE_KINDS)
MODEL_ID = re.compile(r"~?[A-Za-z0-9][A-Za-z0-9_./:+-]{0,255}")
PROVIDER_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}")


class PolicyConflict(ValueError):
    """The operator edited a stale dashboard snapshot."""


def banned(policy, provider, model):
    blocked = [entry["model"] for entry in policy.get("banned", []) if entry["provider"] == provider]
    if provider != "openrouter":
        return model in blocked
    if not blocked:
        return False
    # Dynamic routers may choose any downstream model, including a banned one.
    if model.startswith(("openrouter/", "~")):
        return True
    return any(model.partition(":")[0] == other.partition(":")[0] for other in blocked)


def empty_policy():
    return {"revision": 0, "selected": {}, "banned": [], "preference": ""}


def _policy_path(registry):
    path = Path(registry) / "model-policy.sqlite3"
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("模型策略必须保存在项目登记目录内")
    return path


def _read(db):
    row = db.execute("SELECT data FROM policy WHERE id=1").fetchone()
    return json.loads(row[0]) if row else empty_policy()


def read_policy(registry):
    path = _policy_path(registry)
    if not path.exists():
        return empty_policy()
    with closing(sqlite3.connect(path)) as db:
        # Reader can arrive between sqlite3.connect and the initial transaction.
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='policy'").fetchone():
            return empty_policy()
        return _read(db)


def update_policy(registry, request):
    fields = {"select": ("mode", "provider", "model"), "clear": ("mode",),
              "ban": ("provider", "model"), "unban": ("provider", "model"), "preference": ("value",)}
    action = request.get("action")
    if not isinstance(action, str) or action not in fields or set(request) != {"action", "revision", *fields[action]}:
        raise ValueError("未知模型规则或字段")
    revision = request["revision"]
    if type(revision) is not int or revision < 0:
        raise ValueError("需要有效的规则版本")
    mode = request.get("mode")
    if "mode" in request and mode not in MODES:
        raise ValueError("未知的派工类型")
    if "model" in request:
        model, provider = request["model"], request["provider"]
        if (not isinstance(model, str) or not MODEL_ID.fullmatch(model)
                or not isinstance(provider, str) or not PROVIDER_ID.fullmatch(provider)):
            raise ValueError("无效的供应商或模型 ID")
        ref = {"provider": provider, "model": model}
    if action == "preference" and (not isinstance(request["value"], str) or len(request["value"]) > 1000):
        raise ValueError("模型倾向应为 1000 字以内的文本")
    path = _policy_path(registry)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS policy (id INTEGER PRIMARY KEY, data TEXT NOT NULL)")
        policy = _read(db)
        if revision != policy["revision"]:
            raise PolicyConflict("规则已被其他操作更新，请刷新后重试")
        if action == "select":
            if banned(policy, ref["provider"], ref["model"]):
                raise ValueError("不能选择已禁用的模型")
            policy["selected"][mode] = ref
        elif action == "clear":
            policy["selected"].pop(mode, None)
        elif action == "ban":
            if ref not in policy["banned"]:
                policy["banned"].append(ref)
            policy["selected"] = {k: v for k, v in policy["selected"].items() if not banned(policy, v["provider"], v["model"])}
        elif action == "unban":
            policy["banned"] = [v for v in policy["banned"] if v != ref]
        else:
            policy["preference"] = request["value"].strip()
        policy["revision"] += 1
        db.execute("INSERT OR REPLACE INTO policy VALUES (1,?)", (json.dumps(policy, ensure_ascii=False),))
        return policy
